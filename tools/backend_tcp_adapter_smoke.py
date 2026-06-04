#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backend TCP adapter smoke validation tool.

Verifies that a tcp_forward session created through the backend HTTP API
can forward binary TCP payloads byte-for-byte using GenericTcpForwardAdapter.

Starts:
  1. A local TCP echo server (threaded, 127.0.0.1:0)
  2. Backend server in fake mode (no root server.py needed)

Then:
  3. Creates a tcp_forward session via POST /sessions/create
  4. Gets the actual adapter listen port from session status
  5. Connects to the adapter, sends binary payload, verifies echo
  6. Stops the session, verifies the port is closed
  7. Cleans up all subprocesses and servers
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import http.client
import json
import os
import queue
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 21520
BACKEND_READY_TIMEOUT = 8.0
ADAPTER_READY_TIMEOUT = 10.0
STOP_TIMEOUT = 10.0
DEFAULT_PAYLOAD_SIZE = 1024
DEFAULT_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SmokeFailure(Exception):
    """A smoke check failed."""


# ---------------------------------------------------------------------------
# TCP Echo Server (threaded, no asyncio)
# ---------------------------------------------------------------------------


class TcpEchoServer:
    """Simple threaded TCP echo server.

    Each accepted connection is handled on its own daemon thread.
    Received bytes are echoed back verbatim until the client closes.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> int:
        """Start the echo server.  Returns the actual bound port."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen(5)
        self._socket.settimeout(0.5)
        self.port = self._socket.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        """Stop accepting connections; wait for active handlers to drain."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _accept_loop(self) -> None:
        sock = self._socket
        while self._running and sock is not None:
            try:
                conn, _addr = sock.accept()
                t = threading.Thread(target=self._handle, args=(conn,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except OSError:
                break

    @staticmethod
    def _handle(conn: socket.socket) -> None:
        try:
            conn.settimeout(5.0)
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                conn.sendall(data)
        except (ConnectionError, OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Managed subprocess (same pattern as backend_real_core_http_smoke.py)
# ---------------------------------------------------------------------------


class ManagedProcess:
    """Start a subprocess, capture its output, and cleanly stop it."""

    def __init__(self, name: str, args: List[str], env: Dict[str, str]) -> None:
        self.name = name
        self.args = args
        self.env = env
        self.proc: Optional[subprocess.Popen[str]] = None
        self.logs: List[str] = []
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(
            self.args,
            cwd=str(REPO_ROOT),
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(
            target=self._read_output,
            name=f"S2PassSmokeLog-{self.name}",
            daemon=True,
        )
        self._reader_thread.start()

    def stop(self) -> str:
        proc = self.proc
        if proc is None:
            return "not_started"
        if proc.poll() is None:
            if sys.platform == "win32":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3.0)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        return f"exit_code={proc.returncode}"

    def poll(self) -> Optional[int]:
        if self.proc is None:
            return None
        return self.proc.poll()

    def _read_output(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                text = line.rstrip("\r\n")
                self.logs.append(text)
                self._queue.put(text)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _can_connect_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _tcp_send_recv(host: str, port: int, payload: bytes, timeout: float = 10.0) -> bytes:
    """Connect, send *payload*, shutdown write, read all response, close."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        chunks: List[bytes] = []
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _wait_for_tcp(name: str, process: ManagedProcess, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SmokeFailure(f"{name} exited early with code {code}")
        if _can_connect_tcp(HOST, port):
            return
        time.sleep(0.05)
    raise SmokeFailure(f"{name} did not open {HOST}:{port} in time")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_json(
    method: str,
    port: int,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Tuple[int, Dict[str, Any]]:
    encoded = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request(method, path, body=encoded, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        data = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, data
    finally:
        conn.close()


def _request_ok(
    method: str,
    port: int,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    expected: int = 200,
) -> Dict[str, Any]:
    status, data = _http_json(method, port, path, body=body)
    if status != expected:
        raise SmokeFailure(f"{method} {path} returned {status}: {data}")
    return data


def _wait_health(port: int, backend: ManagedProcess) -> Dict[str, Any]:
    deadline = time.monotonic() + BACKEND_READY_TIMEOUT
    last_error = ""
    while time.monotonic() < deadline:
        code = backend.poll()
        if code is not None:
            raise SmokeFailure(f"backend exited early with code {code}")
        try:
            status, data = _http_json("GET", port, "/health", timeout=1.0)
            if status == 200 and data.get("status") == "ok":
                return data
            last_error = f"status={status}, data={data}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(0.1)
    raise SmokeFailure(f"backend /health did not become ready: {last_error}")


def _get_adapter_listen_port(
    backend_port: int, session_id: str, timeout: float
) -> Tuple[str, int]:
    """Poll session status until adapter_status shows ready with bind_port > 0."""
    deadline = time.monotonic() + timeout
    last_status: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _request_ok("GET", backend_port, f"/sessions/{session_id}/status")
        adapter = last_status.get("adapter_status")
        if isinstance(adapter, dict):
            if adapter.get("status") == "error":
                raise SmokeFailure(
                    f"Adapter error: {adapter.get('error')} "
                    f"(status={last_status})"
                )
            if adapter.get("status") == "ready":
                bind_host = str(adapter.get("bind_host", ""))
                bind_port = int(adapter.get("bind_port", 0))
                if bind_host and bind_port > 0:
                    return bind_host, bind_port
        session_status = last_status.get("status")
        if session_status == "failed":
            raise SmokeFailure(f"Session failed: {last_status}")
        time.sleep(0.1)
    raise SmokeFailure(
        f"Adapter did not reach ready within {timeout}s. "
        f"last_status={last_status}"
    )


# ---------------------------------------------------------------------------
# Main smoke logic
# ---------------------------------------------------------------------------


def run_smoke(
    backend_host: str = HOST,
    backend_port: int = 0,
    payload_size: int = DEFAULT_PAYLOAD_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    """Run the complete TCP adapter smoke test.  Returns 0 on PASS, 1 on FAIL."""

    # --- resolve backend port ---
    if backend_port == 0:
        backend_port = _find_free_tcp_port()

    # --- step tracking for failure reporting ---
    step = "init"
    echo_server: Optional[TcpEchoServer] = None
    backend: Optional[ManagedProcess] = None
    session_id = ""
    actual_listen_host = ""
    actual_listen_port = 0
    stop_verified = False
    backend_cleanup = "not_started"

    try:
        # --------------------------------------------------------------
        # Step 1: Start TCP echo server
        # --------------------------------------------------------------
        step = "echo_server_start"
        echo_server = TcpEchoServer(HOST, 0)
        echo_port = echo_server.start()
        print(f"echo_server: {HOST}:{echo_port}")

        # --------------------------------------------------------------
        # Step 2: Start backend server (fake mode — no root server.py)
        # --------------------------------------------------------------
        step = "backend_start"
        base_env = os.environ.copy()
        backend = ManagedProcess(
            "backend_http",
            [
                sys.executable,
                "-u",
                "-m",
                "backend.server",
                "--host",
                backend_host,
                "--port",
                str(backend_port),
            ],
            base_env,
        )
        backend.start()
        _wait_for_tcp("backend", backend, backend_port, BACKEND_READY_TIMEOUT)
        health = _wait_health(backend_port, backend)
        print(f"backend: {backend_host}:{backend_port} mode={health.get('mode', 'unknown')}")

        # --------------------------------------------------------------
        # Step 3: Create tcp_forward session via backend HTTP API
        # --------------------------------------------------------------
        step = "session_create"
        create_body: Dict[str, Any] = {
            "server_host": HOST,
            "server_port": 9000,
            "server_udp_port": 9001,
            "player_name": "TcpSmoke",
            "game_server_port": echo_port,
            "adapter_config": {
                "enabled": True,
                "adapter_type": "tcp_forward",
                "bind_host": HOST,
                "bind_port": 0,
                "target_host": HOST,
                "target_port": echo_port,
            },
        }
        create_response = _request_ok(
            "POST", backend_port, "/sessions/create", body=create_body, expected=201
        )
        session_id = str(create_response.get("session_id", ""))
        if not session_id:
            raise SmokeFailure("create response did not include session_id")
        print(f"session_id: {session_id}")

        # --------------------------------------------------------------
        # Step 4: Get actual adapter listen port from status
        # --------------------------------------------------------------
        step = "adapter_listen_port"
        # The create response may already have the adapter_status (fake runner
        # emits session_running synchronously, which starts the adapter).
        # But poll anyway as a robustness measure.
        adapter_from_create = create_response.get("adapter_status")
        if (
            isinstance(adapter_from_create, dict)
            and adapter_from_create.get("status") == "ready"
            and adapter_from_create.get("bind_port", 0) > 0
        ):
            actual_listen_host = str(adapter_from_create.get("bind_host", HOST))
            actual_listen_port = int(adapter_from_create["bind_port"])
        else:
            actual_listen_host, actual_listen_port = _get_adapter_listen_port(
                backend_port, session_id, ADAPTER_READY_TIMEOUT
            )

        if not actual_listen_host or actual_listen_port <= 0:
            raise SmokeFailure(
                f"Could not determine adapter listen port. "
                f"adapter_status from response: {create_response.get('adapter_status')}"
            )
        print(f"adapter_listen: {actual_listen_host}:{actual_listen_port}")

        # --------------------------------------------------------------
        # Step 5: TCP forwarding verification
        # --------------------------------------------------------------
        step = "tcp_forward_verify"
        payload = bytes(range(256)) * (max(1, payload_size // 256))
        # Trim to exact payload_size
        payload = payload[:payload_size]
        actual_payload_len = len(payload)

        echo_result = _tcp_send_recv(
            actual_listen_host, actual_listen_port, payload, timeout=timeout
        )
        if echo_result != payload:
            raise SmokeFailure(
                f"TCP echo mismatch: sent {actual_payload_len} bytes, "
                f"received {len(echo_result)} bytes"
            )
        print(f"payload_bytes: {actual_payload_len}")
        print(f"echoed_bytes: {len(echo_result)}")

        # --------------------------------------------------------------
        # Step 6: Stop session and verify cleanup
        # --------------------------------------------------------------
        step = "session_stop"
        stop_response = _request_ok(
            "POST", backend_port, f"/sessions/{session_id}/stop", expected=200
        )
        stop_body_status = stop_response.get("status") if isinstance(stop_response, dict) else None
        if stop_body_status not in ("stopped", "failed"):
            raise SmokeFailure(f"Unexpected stop result: {stop_response}")

        # Verify port is closed after stop
        step = "stop_verify"
        time.sleep(0.3)  # let OS release the port
        try:
            with socket.create_connection((actual_listen_host, actual_listen_port), timeout=1.0):
                raise SmokeFailure(
                    f"Port {actual_listen_host}:{actual_listen_port} still accepting "
                    f"connections after stop"
                )
        except (ConnectionRefusedError, OSError, socket.timeout):
            pass  # expected
        stop_verified = True
        print("stop_verified: yes")

        # --------------------------------------------------------------
        # Cleanup
        # --------------------------------------------------------------
        step = "cleanup"
        backend_cleanup = backend.stop()
        if echo_server is not None:
            echo_server.stop()

        # --------------------------------------------------------------
        # Final output
        # --------------------------------------------------------------
        print(f"echo_server: {HOST}:{echo_port}")
        print(f"backend: {backend_host}:{backend_port}")
        print(f"adapter_type: tcp_forward")
        print(f"adapter_listen: {actual_listen_host}:{actual_listen_port}")
        print(f"payload_bytes: {actual_payload_len}")
        print(f"echoed_bytes: {len(echo_result)}")
        print(f"stop_verified: yes")
        print("Backend TCP adapter smoke PASS")
        return 0

    except Exception as exc:
        print(f"Backend TCP adapter smoke FAIL")
        print(f"failing_step: {step}")
        print(f"reason: {exc}")

        # Best-effort cleanup
        if session_id and backend_port:
            try:
                _http_json(
                    "POST", backend_port, f"/sessions/{session_id}/stop", timeout=3.0
                )
            except Exception:
                pass
        if backend is not None:
            backend_cleanup = backend.stop()
        if echo_server is not None:
            echo_server.stop()

        print(f"cleanup: backend_{backend_cleanup}")
        if backend is not None and backend.logs:
            print("backend_log_tail:")
            for line in backend.logs[-20:]:
                print(line)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backend TCP adapter smoke validation tool"
    )
    parser.add_argument(
        "--backend-host",
        default=HOST,
        help=f"Backend HTTP host (default: {HOST})",
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=DEFAULT_BACKEND_PORT,
        help=f"Backend HTTP port; 0 = auto-select (default: {DEFAULT_BACKEND_PORT})",
    )
    parser.add_argument(
        "--payload-size",
        type=int,
        default=DEFAULT_PAYLOAD_SIZE,
        help=f"Binary payload size in bytes (default: {DEFAULT_PAYLOAD_SIZE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Connection timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()
    sys.exit(
        run_smoke(
            backend_host=args.backend_host,
            backend_port=args.backend_port,
            payload_size=args.payload_size,
            timeout=args.timeout,
        )
    )


if __name__ == "__main__":
    main()
