#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP smoke for backend real_core mode against local server.py.

This helper starts:
  1. root server.py on 127.0.0.1:9000/9001
  2. backend.server with S2PASS_BACKEND_RUNNER=real_core

All session control then goes through the backend HTTP API.
"""
from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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
ROOT_TCP_PORT = 9000
ROOT_UDP_PORT = 9001
SERVER_READY_TIMEOUT = 8.0
BACKEND_READY_TIMEOUT = 8.0
ROOM_TIMEOUT = 8.0
RELAY_TIMEOUT = 18.0
STOP_TIMEOUT = 10.0


class SmokeFailure(Exception):
    """A smoke check failed."""


class ManagedProcess:
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


def _assert_root_ports_available() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind((HOST, ROOT_TCP_PORT))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind((HOST, ROOT_UDP_PORT))
    except OSError as exc:
        raise SmokeFailure(
            f"Root server ports unavailable: {HOST}:{ROOT_TCP_PORT}/"
            f"{ROOT_UDP_PORT} ({exc})"
        )


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
    if _contains_relay_token(data):
        raise SmokeFailure(f"{method} {path} exposed relay_token")
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
            if status == 200 and data.get("mode") == "real_core":
                if _contains_relay_token(data):
                    raise SmokeFailure("GET /health exposed relay_token")
                return data
            last_error = f"status={status}, data={data}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(0.1)
    raise SmokeFailure(f"backend /health did not become ready: {last_error}")


def _get_status(port: int, session_id: str) -> Dict[str, Any]:
    return _request_ok("GET", port, f"/sessions/{session_id}/status")


def _get_logs(port: int, session_id: str) -> Dict[str, Any]:
    return _request_ok("GET", port, f"/sessions/{session_id}/logs")


def _event_types(logs_payload: Dict[str, Any]) -> List[str]:
    events = logs_payload.get("events", [])
    if not isinstance(events, list):
        return []
    result = []
    for event in events:
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            result.append(event["type"])
    return result


def _event_data(logs_payload: Dict[str, Any], event_type: str) -> List[Dict[str, Any]]:
    events = logs_payload.get("events", [])
    result = []
    if not isinstance(events, list):
        return result
    for event in events:
        if isinstance(event, dict) and event.get("type") == event_type:
            data = event.get("data", {})
            if isinstance(data, dict):
                result.append(data)
    return result


def _wait_session(
    port: int,
    session_id: str,
    label: str,
    required_event: str,
    timeout: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_status: Dict[str, Any] = {}
    last_logs: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _get_status(port, session_id)
        last_logs = _get_logs(port, session_id)
        types = _event_types(last_logs)
        if (
            required_event in types
            and "relay_ready" in types
            and "session_running" in types
            and last_status.get("status") == "running"
        ):
            return last_status, last_logs
        if last_status.get("status") == "failed" or "session_failed" in types:
            raise SmokeFailure(f"{label} failed: status={last_status}, events={types}")
        time.sleep(0.1)
    raise SmokeFailure(
        f"{label} did not reach running with {required_event}/relay_ready in time. "
        f"last_status={last_status}, events={_event_types(last_logs)}"
    )


def _contains_relay_token(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "relay_token":
                return True
            if _contains_relay_token(item):
                return True
    elif isinstance(value, list):
        return any(_contains_relay_token(item) for item in value)
    elif isinstance(value, str):
        return "relay_token" in value or "rtk_" in value
    return False


def _stop_sessions(port: int, creator_id: str, joiner_id: str) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(3)

    def stop_one(label: str, session_id: str) -> None:
        try:
            barrier.wait(timeout=2.0)
            status, data = _http_json(
                "POST",
                port,
                f"/sessions/{session_id}/stop",
                timeout=STOP_TIMEOUT,
            )
            with lock:
                results[label] = {"http_status": status, "body": data}
        except Exception as exc:
            with lock:
                results[label] = {"error": repr(exc)}

    threads = [
        threading.Thread(target=stop_one, args=("creator", creator_id)),
        threading.Thread(target=stop_one, args=("joiner", joiner_id)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2.0)
    for thread in threads:
        thread.join(timeout=STOP_TIMEOUT)
        if thread.is_alive():
            raise SmokeFailure("stop request thread did not exit")

    for label, result in results.items():
        if "error" in result:
            raise SmokeFailure(f"{label} stop request failed: {result['error']}")
        body = result.get("body", {})
        if _contains_relay_token(body):
            raise SmokeFailure(f"{label} stop response exposed relay_token")
        http_status = result.get("http_status")
        body_status = body.get("status") if isinstance(body, dict) else None
        if http_status != 200 or body_status not in ("stopped", "failed"):
            raise SmokeFailure(f"{label} unexpected stop result: {result}")
    return results


def _assert_no_session_failed(label: str, logs: Dict[str, Any]) -> None:
    types = _event_types(logs)
    if "session_failed" in types:
        raise SmokeFailure(f"{label} emitted session_failed: {types}")


def _print_snippet(label: str, status: Dict[str, Any], logs: Dict[str, Any]) -> None:
    print(f"{label} status: {status.get('status')}")
    print(f"{label} events: {', '.join(_event_types(logs))}")


def _print_failure_snippets(
    creator_status: Dict[str, Any],
    creator_logs: Dict[str, Any],
    joiner_status: Dict[str, Any],
    joiner_logs: Dict[str, Any],
) -> None:
    if creator_status or creator_logs:
        _print_snippet("creator", creator_status, creator_logs)
    if joiner_status or joiner_logs:
        _print_snippet("joiner", joiner_status, joiner_logs)


def run_smoke() -> int:
    print("S2Pass backend HTTP real_core smoke")
    _assert_root_ports_available()
    backend_port = _find_free_tcp_port()
    print(f"root_server: {HOST}:{ROOT_TCP_PORT}/{ROOT_UDP_PORT}")
    print(f"backend_http: {HOST}:{backend_port}")

    base_env = os.environ.copy()
    root_env = base_env.copy()
    root_env["SERVER_IP"] = HOST
    root_env["S2PASS_ADVERTISE_HOST"] = HOST
    backend_env = base_env.copy()
    backend_env["S2PASS_BACKEND_RUNNER"] = "real_core"

    root = ManagedProcess(
        "root_server",
        [sys.executable, "-u", "server.py", "--advertise-host", HOST],
        root_env,
    )
    backend = ManagedProcess(
        "backend_http",
        [
            sys.executable,
            "-u",
            "-m",
            "backend.server",
            "--host",
            HOST,
            "--port",
            str(backend_port),
        ],
        backend_env,
    )

    creator_status: Dict[str, Any] = {}
    creator_logs: Dict[str, Any] = {}
    joiner_status: Dict[str, Any] = {}
    joiner_logs: Dict[str, Any] = {}
    creator_id = ""
    joiner_id = ""
    root_cleanup = "not_started"
    backend_cleanup = "not_started"

    try:
        root.start()
        _wait_for_tcp("root server.py", root, ROOT_TCP_PORT, SERVER_READY_TIMEOUT)
        print("root_server_start: OK")

        backend.start()
        health = _wait_health(backend_port, backend)
        print(f"backend_health: OK mode={health.get('mode')}")

        creator = _request_ok(
            "POST",
            backend_port,
            "/sessions/create",
            {
                "server_host": HOST,
                "server_port": ROOT_TCP_PORT,
                "server_udp_port": ROOT_UDP_PORT,
                "player_name": "HttpSmokeCreator",
            },
            expected=201,
        )
        creator_id = str(creator.get("session_id", ""))
        if not creator_id:
            raise SmokeFailure("create response did not include session_id")

        deadline = time.monotonic() + ROOM_TIMEOUT
        room_id = ""
        while time.monotonic() < deadline:
            creator_status = _get_status(backend_port, creator_id)
            creator_logs = _get_logs(backend_port, creator_id)
            room_datas = _event_data(creator_logs, "room_created")
            if room_datas:
                room_id = str(room_datas[-1].get("room_id", ""))
                if room_id:
                    break
            time.sleep(0.1)
        if not room_id:
            raise SmokeFailure("creator room_created room_id was not observed")

        joiner = _request_ok(
            "POST",
            backend_port,
            "/sessions/join",
            {
                "server_host": HOST,
                "server_port": ROOT_TCP_PORT,
                "server_udp_port": ROOT_UDP_PORT,
                "room_id": room_id,
                "player_name": "HttpSmokeJoiner",
            },
            expected=201,
        )
        joiner_id = str(joiner.get("session_id", ""))
        if not joiner_id:
            raise SmokeFailure("join response did not include session_id")

        creator_status, creator_logs = _wait_session(
            backend_port,
            creator_id,
            "creator",
            "room_created",
            RELAY_TIMEOUT,
        )
        joiner_status, joiner_logs = _wait_session(
            backend_port,
            joiner_id,
            "joiner",
            "room_joined",
            RELAY_TIMEOUT,
        )

        for label, status, logs in (
            ("creator", creator_status, creator_logs),
            ("joiner", joiner_status, joiner_logs),
        ):
            if _contains_relay_token(status) or _contains_relay_token(logs):
                raise SmokeFailure(f"{label} HTTP status/logs exposed relay_token")
            _assert_no_session_failed(label, logs)

        stop_results = _stop_sessions(backend_port, creator_id, joiner_id)

        creator_status = _get_status(backend_port, creator_id)
        creator_logs = _get_logs(backend_port, creator_id)
        joiner_status = _get_status(backend_port, joiner_id)
        joiner_logs = _get_logs(backend_port, joiner_id)
        if _contains_relay_token([creator_status, creator_logs, joiner_status, joiner_logs]):
            raise SmokeFailure("HTTP status/logs exposed relay_token after stop")

        backend_cleanup = backend.stop()
        root_cleanup = root.stop()

        _print_snippet("creator", creator_status, creator_logs)
        _print_snippet("joiner", joiner_status, joiner_logs)
        print(f"room_id: {room_id}")
        print(f"stop_results: {stop_results}")
        print(f"cleanup: backend_{backend_cleanup}, root_server_{root_cleanup}")
        print("relay_token_exposed: no")
        print("RESULT: PASS")
        return 0

    except Exception as exc:
        print("RESULT: FAIL")
        print(f"reason: {exc}")
        _print_failure_snippets(
            creator_status,
            creator_logs,
            joiner_status,
            joiner_logs,
        )
        if creator_id:
            try:
                _http_json("POST", backend_port, f"/sessions/{creator_id}/stop", timeout=3.0)
            except Exception:
                pass
        if joiner_id:
            try:
                _http_json("POST", backend_port, f"/sessions/{joiner_id}/stop", timeout=3.0)
            except Exception:
                pass
        backend_cleanup = backend.stop()
        root_cleanup = root.stop()
        print(f"cleanup: backend_{backend_cleanup}, root_server_{root_cleanup}")
        if backend.logs:
            print("backend_log_tail:")
            for line in backend.logs[-20:]:
                print(line)
        if root.logs:
            print("root_server_log_tail:")
            for line in root.logs[-20:]:
                print(line)
        return 1


def main() -> None:
    sys.exit(run_smoke())


if __name__ == "__main__":
    main()
