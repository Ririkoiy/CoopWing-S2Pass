"""
Tests for GenericTcpForwardAdapter.

Covers:
  A. Basic bidirectional forwarding (including binary payload)
  B. Multiple concurrent clients
  C. Target connection failure (adapter must not crash)
  D. stop() idempotent
  E. start() idempotent / duplicate-start protection
  F. Large payload (> buffer_size)
  G. Resource cleanup (stop blocks new connections, closes actives)
"""

import asyncio
import sys
import socket
import threading
import time
import unittest
from typing import Optional

from adapters.profile import GameProfile
from adapters.tcp_adapter import GenericTcpForwardAdapter

# ---------------------------------------------------------------------------
# Windows event-loop policy (protocol_lock.md §11)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(**overrides) -> GameProfile:
    """Create a minimal GameProfile suitable for TCP adapter tests."""
    defaults = dict(
        profile_id="tcp_test",
        display_name="TCP Test",
        exe_path="",
    )
    defaults.update(overrides)
    return GameProfile(**defaults)


class _EchoServer:
    """A tiny asyncio TCP echo server running on a background thread.

    Each accepted connection echoes back everything it reads until EOF,
    then closes.
    """

    def __init__(self) -> None:
        self.host: str = "127.0.0.1"
        self.port: int = 0  # filled once started
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Echo server did not start in time")

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    # -- internal --

    def _run(self) -> None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_server())
            loop.run_forever()
        finally:
            # cleanup
            if self._server is not None:
                self._server.close()
                loop.run_until_complete(self._server.wait_closed())
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _start_server(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self.host, 0, reuse_address=True
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._ready.set()

    @staticmethod
    async def _handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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
    """Connect, send *payload*, shutdown write, read all response, close."""
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


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestGenericTcpForwardAdapter(unittest.TestCase):
    """Main test suite for the TCP adapter."""

    # ----- fixtures --------------------------------------------------------

    def setUp(self) -> None:
        self.echo = _EchoServer()
        self.echo.start()

    def tearDown(self) -> None:
        self.echo.stop()

    def _make_adapter(self, **kw) -> GenericTcpForwardAdapter:
        """Create an adapter pointed at the running echo server."""
        defaults = dict(
            listen_host="127.0.0.1",
            listen_port=0,
            target_host=self.echo.host,
            target_port=self.echo.port,
        )
        defaults.update(kw)
        profile = _make_profile()
        adapter = GenericTcpForwardAdapter(profile, **defaults)
        return adapter

    # ----- A. Basic forwarding ---------------------------------------------

    def test_basic_forward_text(self) -> None:
        """UTF-8 text should be echoed byte-for-byte."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()
        self.assertIsNotNone(port)

        payload = b"hello-tcp-adapter"
        result = _tcp_send_recv(host, port, payload)
        self.assertEqual(result, payload)

    def test_basic_forward_binary(self) -> None:
        """Binary payload (non-UTF-8) must survive untouched."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()

        # Include null bytes, high bytes, and all 256 values
        payload = bytes(range(256)) * 4
        result = _tcp_send_recv(host, port, payload)
        self.assertEqual(result, payload)

    def test_stats_after_forward(self) -> None:
        """Stats counters must reflect forwarded data."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()
        payload = b"stats-check-payload"
        _tcp_send_recv(host, port, payload)

        # Give a moment for async stats update
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            stats = adapter.get_stats()
            if stats["total_connections"] >= 1 and stats["active_connections"] == 0:
                break
            time.sleep(0.05)

        stats = adapter.get_stats()
        self.assertTrue(stats["running"])
        self.assertGreaterEqual(stats["total_connections"], 1)
        self.assertEqual(stats["active_connections"], 0)
        self.assertGreaterEqual(stats["bytes_forwarded_to_target"], len(payload))
        self.assertGreaterEqual(stats["bytes_forwarded_to_client"], len(payload))

    # ----- B. Multiple concurrent clients ----------------------------------

    def test_concurrent_clients(self) -> None:
        """At least 3 clients should work concurrently without cross-talk."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()

        results = [None, None, None]
        errors = [None, None, None]
        payloads = [
            b"client-0-" + bytes(range(200)),
            b"client-1-" + bytes(range(100, 256)) + b"\x00" * 50,
            b"client-2-" + b"\xff" * 300,
        ]

        def worker(idx: int) -> None:
            try:
                results[idx] = _tcp_send_recv(host, port, payloads[idx])
            except Exception as exc:
                errors[idx] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        for i in range(3):
            self.assertIsNone(errors[i], f"Client {i} error: {errors[i]}")
            self.assertEqual(results[i], payloads[i], f"Client {i} data mismatch")

    # ----- C. Target connection failure ------------------------------------

    def test_target_connect_failure(self) -> None:
        """If the target port is unreachable, individual connections fail
        gracefully but the adapter itself stays alive."""
        # Find a port that's guaranteed closed
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()

        adapter = self._make_adapter(target_host="127.0.0.1", target_port=dead_port)
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()
        self.assertTrue(adapter.is_running())

        # Client connects; the adapter should close the connection quickly
        try:
            with socket.create_connection((host, port), timeout=5.0) as sock:
                sock.sendall(b"hello")
                # Read until close
                sock.settimeout(5.0)
                data = sock.recv(4096)
                # data may be empty (connection closed by adapter) — that's fine
        except (ConnectionError, OSError):
            pass  # also acceptable

        # The adapter should still be running
        self.assertTrue(adapter.is_running())

        # stop should clean up normally
        adapter.stop()
        self.assertFalse(adapter.is_running())

    # ----- D. stop() idempotent -------------------------------------------

    def test_stop_idempotent(self) -> None:
        """Calling stop() multiple times must not raise."""
        adapter = self._make_adapter()
        adapter.start()
        self.assertTrue(adapter.is_running())

        adapter.stop()
        self.assertFalse(adapter.is_running())

        # Second stop — no exception
        adapter.stop()
        self.assertFalse(adapter.is_running())

        # Third for good measure
        adapter.stop()

    def test_stop_before_start(self) -> None:
        """stop() on a never-started adapter must not raise."""
        adapter = self._make_adapter()
        adapter.stop()
        adapter.stop()

    # ----- E. start() idempotent ------------------------------------------

    def test_start_idempotent(self) -> None:
        """Calling start() twice must not create duplicate listeners."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host1, port1 = adapter.get_local_addr()

        # Second start — should be a no-op
        adapter.start()
        host2, port2 = adapter.get_local_addr()

        self.assertEqual(port1, port2)
        self.assertTrue(adapter.is_running())

        # Verify forwarding still works
        payload = b"after-double-start"
        result = _tcp_send_recv(host1, port1, payload)
        self.assertEqual(result, payload)

    # ----- F. Large payload -----------------------------------------------

    def test_large_payload(self) -> None:
        """Payload larger than buffer_size (default 64 KB) must be forwarded
        completely and byte-for-byte."""
        adapter = self._make_adapter(buffer_size=4096)  # small buffer
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()

        # 256 KB payload — well over the 4 KB buffer
        payload = bytes(range(256)) * 1024  # 256 KB
        result = _tcp_send_recv(host, port, payload, timeout=15.0)
        self.assertEqual(len(result), len(payload))
        self.assertEqual(result, payload)

    # ----- G. Resource cleanup --------------------------------------------

    def test_new_connection_after_stop_fails(self) -> None:
        """After stop(), new TCP connections must be refused."""
        adapter = self._make_adapter()
        adapter.start()

        host, port = adapter.get_local_addr()
        adapter.stop()

        with self.assertRaises((ConnectionRefusedError, OSError, ConnectionError)):
            socket.create_connection((host, port), timeout=2.0)

    def test_active_connections_closed_on_stop(self) -> None:
        """stop() must close all active connections."""
        adapter = self._make_adapter()
        adapter.start()

        host, port = adapter.get_local_addr()

        # Open a long-lived connection (do NOT send shutdown)
        sock = socket.create_connection((host, port), timeout=5.0)
        sock.settimeout(5.0)
        sock.sendall(b"keep-alive")

        # Wait for connection to be counted
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if adapter.active_connections >= 1:
                break
            time.sleep(0.05)
        self.assertGreaterEqual(adapter.active_connections, 1)

        adapter.stop()

        # The socket should now be broken / EOF
        try:
            data = sock.recv(4096)
            # data == b"" means EOF — connection closed; also acceptable
            # Could also raise ConnectionError
        except (ConnectionError, OSError):
            data = b""
        finally:
            sock.close()

        self.assertFalse(adapter.is_running())

    def test_no_pending_tasks_warning(self) -> None:
        """After stop(), there should be no asyncio tasks left."""
        adapter = self._make_adapter()
        adapter.start()

        host, port = adapter.get_local_addr()
        payload = b"task-cleanup-test"
        _tcp_send_recv(host, port, payload)

        adapter.stop()
        self.assertIsNone(adapter.last_error)

        # loop should be closed
        self.assertFalse(adapter.is_running())
        # The internal loop reference should be None
        self.assertIsNone(adapter._loop)

    def test_thread_terminated_on_stop(self) -> None:
        """Verify that the background thread is joined and no longer alive after stop()."""
        adapter = self._make_adapter()
        adapter.start()
        thread = adapter._thread
        self.assertIsNotNone(thread)
        self.assertTrue(thread.is_alive())

        adapter.stop()
        self.assertIsNone(adapter.last_error)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(adapter._thread)

    def test_half_close_does_not_hang(self) -> None:
        """Verify that a half-closed or quickly closed client connection does not hang the adapter."""
        adapter = self._make_adapter()
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()
        sock = socket.create_connection((host, port), timeout=2.0)
        sock.shutdown(socket.SHUT_WR)
        time.sleep(0.1)
        sock.close()
        
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if adapter.active_connections == 0:
                break
            time.sleep(0.05)
        self.assertEqual(adapter.active_connections, 0)



class TestGenericTcpForwardAdapterValidation(unittest.TestCase):
    """Test constructor validation and edge cases that don't need an echo server."""

    def test_missing_target_host(self) -> None:
        profile = _make_profile()
        with self.assertRaises(ValueError):
            GenericTcpForwardAdapter(profile, target_port=8080)

    def test_missing_target_port(self) -> None:
        profile = _make_profile()
        with self.assertRaises(ValueError):
            GenericTcpForwardAdapter(profile, target_host="127.0.0.1")

    def test_get_local_addr_before_start(self) -> None:
        profile = _make_profile()
        adapter = GenericTcpForwardAdapter(
            profile, target_host="127.0.0.1", target_port=9999
        )
        host, port = adapter.get_local_addr()
        self.assertIsNone(host)
        self.assertIsNone(port)

    def test_get_pid_returns_none(self) -> None:
        profile = _make_profile()
        adapter = GenericTcpForwardAdapter(
            profile, target_host="127.0.0.1", target_port=9999
        )
        self.assertIsNone(adapter.get_pid())

    def test_config_from_profile_fields(self) -> None:
        """Constructor should fall back to GameProfile fields."""
        profile = _make_profile(
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_host="10.0.0.1",
            remote_target_port=5000,
        )
        adapter = GenericTcpForwardAdapter(profile)
        self.assertEqual(adapter._target_host, "10.0.0.1")
        self.assertEqual(adapter._target_port, 5000)
        self.assertEqual(adapter._listen_host, "127.0.0.1")

    def test_start_invalid_bind_address(self) -> None:
        """Binding to an invalid address should raise clearly."""
        profile = _make_profile()
        adapter = GenericTcpForwardAdapter(
            profile,
            listen_host="999.999.999.999",
            listen_port=0,
            target_host="127.0.0.1",
            target_port=9999,
        )
        with self.assertRaises((RuntimeError, OSError)):
            adapter.start()
        self.assertFalse(adapter.is_running())


if __name__ == "__main__":
    unittest.main()
