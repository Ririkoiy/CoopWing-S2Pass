# -*- coding: utf-8 -*-
"""Integration tests: AdapterManager + TCP adapter lifecycle.

Covers:
  A. AdapterManager creates GenericTcpForwardAdapter for tcp_forward type
  B. TCP adapter forwards through an echo target
  C. Stop releases port, idempotent
  D. UDP adapter path unaffected
  E. Unknown adapter_config.adapter_type raises clear error
"""
from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
import unittest
from typing import Optional

from adapters.tcp_adapter import GenericTcpForwardAdapter
from backend.adapter_manager import AdapterManager
from backend.models import AdapterConfig

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _EchoServer:
    """Tiny asyncio TCP echo server on background thread."""

    def __init__(self):
        self.host = "127.0.0.1"
        self.port: int = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Echo server did not start")

    def stop(self):
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self):
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_server())
            loop.run_forever()
        finally:
            if self._server is not None:
                self._server.close()
                loop.run_until_complete(self._server.wait_closed())
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _start_server(self):
        self._server = await asyncio.start_server(
            self._handle, self.host, 0, reuse_address=True,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._ready.set()

    @staticmethod
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


def _tcp_send_recv(host: str, port: int, payload: bytes, timeout: float = 5.0) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _can_bind_tcp(port: int) -> bool:
    """Return True if *port* is free for TCP binding."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class BackendTcpAdapterIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.echo = _EchoServer()
        self.echo.start()

    def tearDown(self):
        self.echo.stop()

    # -- A. AdapterManager creates GenericTcpForwardAdapter -------------------

    def test_tcp_config_yields_tcp_adapter(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            bind_host="127.0.0.1",
            bind_port=0,
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)

        status = manager.start("s_tcp")
        try:
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.adapter_type, "tcp_forward")
            self.assertGreater(status.bind_port, 0)
            self.assertEqual(status.target_host, self.echo.host)
            self.assertEqual(status.target_port, self.echo.port)
            self.assertIsNone(status.error)
        finally:
            manager.stop("s_tcp")

    # -- B. TCP adapter forwards through echo target --------------------------

    def test_tcp_adapter_forwards_through_backend(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)

        ready = manager.start("s_tcp")
        try:
            self.assertEqual(ready.status, "ready")

            payload = b"backend-tcp-integration-test"
            result = _tcp_send_recv(ready.bind_host, ready.bind_port, payload)
            self.assertEqual(result, payload)
        finally:
            manager.stop("s_tcp")

    def test_tcp_adapter_forwards_binary(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)

        ready = manager.start("s_tcp")
        try:
            payload = bytes(range(256)) * 2
            result = _tcp_send_recv(ready.bind_host, ready.bind_port, payload)
            self.assertEqual(result, payload)
        finally:
            manager.stop("s_tcp")

    # -- C. Stop releases port, idempotent -----------------------------------

    def test_tcp_stop_releases_port(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)
        ready = manager.start("s_tcp")
        port = ready.bind_port

        stopped = manager.stop("s_tcp")

        self.assertEqual(stopped.status, "stopped")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if _can_bind_tcp(port):
                break
            time.sleep(0.05)
        self.assertTrue(_can_bind_tcp(port), f"Port {port} not released")

    def test_tcp_stop_idempotent(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)
        manager.start("s_tcp")

        first = manager.stop("s_tcp")
        second = manager.stop("s_tcp")

        self.assertEqual(first.status, "stopped")
        self.assertEqual(second.status, "stopped")

    def test_tcp_start_idempotent(self):
        manager = AdapterManager()
        config = AdapterConfig(
            enabled=True,
            adapter_type="tcp_forward",
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        manager.configure("s_tcp", config)

        first = manager.start("s_tcp")
        second = manager.start("s_tcp")

        try:
            self.assertEqual(first.status, "ready")
            self.assertEqual(second.status, "ready")
            self.assertEqual(first.bind_port, second.bind_port)
        finally:
            manager.stop("s_tcp")

    # -- D. UDP adapter path unaffected --------------------------------------

    def test_udp_adapter_path_unchanged(self):
        """Verify that existing UDP adapter path still works."""
        from adapters.transport import FakePairTransport

        manager = AdapterManager(
            transport_factory=lambda sid, cfg: FakePairTransport(),
        )
        config = AdapterConfig(
            enabled=True,
            adapter_type="local_udp_bridge",
            bind_port=0,
            target_port=40200,
        )
        manager.configure("s_udp", config)

        status = manager.start("s_udp")
        try:
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.adapter_type, "local_udp_bridge")
        finally:
            manager.stop("s_udp")

    # -- E. Unknown adapter type raises clear error --------------------------

    def test_unknown_adapter_type_raises(self):
        from backend.models import BackendError

        with self.assertRaises(BackendError) as ctx:
            AdapterConfig.from_dict({
                "enabled": True,
                "adapter_type": "invalid_adapter_type",
            })
        self.assertIn("adapter_type", ctx.exception.message)


class AdapterFactoryTests(unittest.TestCase):
    """Test the adapter factory directly."""

    def test_factory_creates_tcp_adapter(self):
        from adapters.factory import create_adapter
        from adapters.profile import GameProfile

        profile = GameProfile(
            profile_id="test_tcp",
            display_name="Test TCP",
            exe_path="",
            adapter_type="tcp_forward",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_host="127.0.0.1",
            remote_target_port=9999,
        )
        adapter = create_adapter(profile)
        self.assertIsInstance(adapter, GenericTcpForwardAdapter)
        self.assertEqual(adapter._target_host, "127.0.0.1")
        self.assertEqual(adapter._target_port, 9999)

    def test_factory_unknown_type_raises(self):
        from adapters.factory import create_adapter
        from adapters.profile import GameProfile

        profile = GameProfile(
            profile_id="test_unknown",
            display_name="Unknown",
            exe_path="",
            adapter_type="unknown_type",
        )
        with self.assertRaises(ValueError) as ctx:
            create_adapter(profile)
        self.assertIn("unknown_type", str(ctx.exception))

    def test_factory_launch_only(self):
        from adapters.factory import create_adapter
        from adapters.launch_adapter import LaunchAdapter
        from adapters.profile import GameProfile

        profile = GameProfile(
            profile_id="test_launch",
            display_name="Launch",
            exe_path="notepad.exe",
            adapter_type="launch_only",
        )
        adapter = create_adapter(profile)
        self.assertIsInstance(adapter, LaunchAdapter)


if __name__ == "__main__":
    unittest.main()
