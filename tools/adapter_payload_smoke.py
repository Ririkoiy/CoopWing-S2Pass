#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local payload smoke for backend real_core adapter wiring.

Topology:
  udp_game_client
    -> creator LocalUdpBridgeAdapter
    -> creator CoreTransportAdapter
    -> S2Pass Core relay through local server.py
    -> joiner CoreTransportAdapter
    -> joiner LocalUdpBridgeAdapter
    -> udp_game_server

This helper is intentionally a manual/integration smoke. It starts only local
processes and terminates only the processes it started.
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
import re
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
PREFERRED_BACKEND_PORT = 21520
SERVER_READY_TIMEOUT = 8.0
BACKEND_READY_TIMEOUT = 8.0
GAME_SERVER_READY_TIMEOUT = 5.0
ROOM_TIMEOUT = 8.0
RELAY_TIMEOUT = 20.0
PAYLOAD_TIMEOUT = 20.0
STOP_TIMEOUT = 10.0
CLIENT_COUNT = 5


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
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(
            target=self._read_output,
            name=f"S2PassAdapterPayloadSmokeLog-{self.name}",
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

    def wait(self, timeout: float) -> int:
        proc = self.proc
        if proc is None:
            raise SmokeFailure(f"{self.name} was not started")
        try:
            return proc.wait(timeout=timeout)
        finally:
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1.0)

    def wait_for_log(self, pattern: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.poll()
            if code is not None:
                raise SmokeFailure(f"{self.name} exited early with code {code}")
            try:
                line = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if pattern in line:
                return line
        raise SmokeFailure(f"{self.name} did not emit {pattern!r} in time")

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


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key == "relay_token" else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        text = value.replace("relay_token", "relay_[redacted]")
        text = re.sub(r"rtk_[0-9a-fA-F]+", "rtk_[redacted]", text)
        return text.replace("\ufffd", "?")
    return value


def _safe_json(value: Any) -> str:
    return json.dumps(_sanitize(value), ensure_ascii=False, separators=(",", ":"))


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


def _assert_root_ports_available() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind((HOST, ROOT_TCP_PORT))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind((HOST, ROOT_UDP_PORT))
    except OSError as exc:
        raise SmokeFailure(
            f"root server ports unavailable: {HOST}:{ROOT_TCP_PORT}/"
            f"{ROOT_UDP_PORT} ({exc})"
        )


def _find_free_tcp_port(preferred: Optional[int] = None) -> int:
    if preferred is not None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _find_free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
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
    if _contains_relay_token(data):
        raise SmokeFailure(f"{method} {path} exposed relay_token")
    if status != expected:
        raise SmokeFailure(f"{method} {path} returned {status}: {_safe_json(data)}")
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
            last_error = f"status={status}, data={_safe_json(data)}"
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
    return [
        event["type"]
        for event in events
        if isinstance(event, dict) and isinstance(event.get("type"), str)
    ]


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


def _wait_for_room_id(port: int, session_id: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + ROOM_TIMEOUT
    last_status: Dict[str, Any] = {}
    last_logs: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _get_status(port, session_id)
        last_logs = _get_logs(port, session_id)
        room_datas = _event_data(last_logs, "room_created")
        if room_datas:
            room_id = str(room_datas[-1].get("room_id", ""))
            if room_id:
                return room_id, last_status, last_logs
        if last_status.get("status") == "failed":
            raise SmokeFailure(f"creator failed before room_id: {_safe_json(last_status)}")
        time.sleep(0.1)
    raise SmokeFailure(
        "creator room_created room_id was not observed. "
        f"last_status={_safe_json(last_status)}, events={_event_types(last_logs)}"
    )


def _wait_session_ready(
    port: int,
    session_id: str,
    label: str,
    required_event: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + RELAY_TIMEOUT
    last_status: Dict[str, Any] = {}
    last_logs: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _get_status(port, session_id)
        last_logs = _get_logs(port, session_id)
        types = _event_types(last_logs)
        adapter_status = last_status.get("adapter_status", {})
        if (
            required_event in types
            and "relay_ready" in types
            and "session_running" in types
            and "adapter_ready" in types
            and last_status.get("status") == "running"
            and isinstance(adapter_status, dict)
            and adapter_status.get("status") == "ready"
        ):
            return last_status, last_logs
        if last_status.get("status") == "failed" or "session_failed" in types:
            raise SmokeFailure(
                f"{label} failed: status={_safe_json(last_status)}, events={types}"
            )
        if isinstance(adapter_status, dict) and adapter_status.get("status") == "error":
            raise SmokeFailure(f"{label} adapter_error: {_safe_json(adapter_status)}")
        time.sleep(0.1)
    raise SmokeFailure(
        f"{label} did not reach running+adapter_ready in time. "
        f"last_status={_safe_json(last_status)}, events={_event_types(last_logs)}"
    )


def _run_client(port: int) -> ManagedProcess:
    env = os.environ.copy()
    client = ManagedProcess(
        "udp_game_client",
        [
            sys.executable,
            "-u",
            "tools/udp_game_client.py",
            "--host",
            HOST,
            "--port",
            str(port),
            "--client-id",
            "smoke_client",
            "--count",
            str(CLIENT_COUNT),
            "--interval",
            "0.05",
            "--timeout",
            "5.0",
        ],
        env,
    )
    client.start()
    code = client.wait(timeout=PAYLOAD_TIMEOUT)
    if code != 0:
        raise SmokeFailure(f"udp_game_client exited with code {code}")
    output = "\n".join(client.logs)
    expected = (
        "Received JOIN confirmation: WELCOME smoke_client",
        f"Sent: {CLIENT_COUNT}",
        f"Received: {CLIENT_COUNT}",
        "Lost: 0",
        "Unexpected: 0",
        "Exit status: 0",
    )
    for marker in expected:
        if marker not in output:
            raise SmokeFailure(f"udp_game_client output missing {marker!r}")
    return client


def _adapter_status(status: Dict[str, Any]) -> Dict[str, Any]:
    adapter_status = status.get("adapter_status")
    if not isinstance(adapter_status, dict):
        raise SmokeFailure(f"missing adapter_status in {_safe_json(status)}")
    return adapter_status


def _assert_counter_progress(label: str, status: Dict[str, Any]) -> None:
    adapter_status = _adapter_status(status)
    counters = adapter_status.get("counters")
    if not isinstance(counters, dict):
        raise SmokeFailure(f"{label} adapter counters missing")
    required = (
        "packets_from_game",
        "packets_to_transport",
        "packets_from_transport",
        "packets_to_game",
    )
    for key in required:
        value = counters.get(key)
        if not isinstance(value, int) or value <= 0:
            raise SmokeFailure(
                f"{label} counter {key} did not increment: {_safe_json(counters)}"
            )


def _wait_counters(
    port: int,
    creator_id: str,
    joiner_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + 5.0
    creator_status: Dict[str, Any] = {}
    joiner_status: Dict[str, Any] = {}
    last_error = ""
    while time.monotonic() < deadline:
        creator_status = _get_status(port, creator_id)
        joiner_status = _get_status(port, joiner_id)
        try:
            _assert_counter_progress("creator", creator_status)
            _assert_counter_progress("joiner", joiner_status)
            return creator_status, joiner_status
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise SmokeFailure(last_error or "adapter counters did not increment")


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
        if result.get("http_status") != 200 or body.get("status") not in ("stopped", "failed"):
            raise SmokeFailure(f"{label} unexpected stop result: {_safe_json(result)}")
    return results


def _print_session_summary(label: str, status: Dict[str, Any], logs: Dict[str, Any]) -> None:
    adapter_status = status.get("adapter_status", {})
    counters = adapter_status.get("counters", {}) if isinstance(adapter_status, dict) else {}
    print(f"{label} session_id: {status.get('session_id', '')}")
    print(f"{label} status: {status.get('status', '')}")
    print(f"{label} adapter_status: {_safe_json(adapter_status)}")
    print(f"{label} counters: {_safe_json(counters)}")
    print(f"{label} events: {', '.join(_event_types(logs)[-12:])}")


def _print_process_tail(process: ManagedProcess) -> None:
    if not process.logs:
        return
    print(f"{process.name}_log_tail:")
    for line in process.logs[-20:]:
        print(str(_sanitize(line)).encode(
            sys.stdout.encoding or "utf-8",
            errors="backslashreplace",
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"))


def run_smoke() -> int:
    print("S2Pass adapter payload smoke")
    print("topology: udp_game_client -> creator adapter -> Core relay -> joiner adapter -> udp_game_server")
    print(f"python: {sys.executable}")
    _assert_root_ports_available()
    backend_port = _find_free_tcp_port(PREFERRED_BACKEND_PORT)
    game_server_port = _find_free_udp_port()
    creator_placeholder_port = _find_free_udp_port()
    print(f"root_server: {HOST}:{ROOT_TCP_PORT}/{ROOT_UDP_PORT}")
    print(f"backend_http: {HOST}:{backend_port}")
    if backend_port != PREFERRED_BACKEND_PORT:
        print(f"backend_port_note: {PREFERRED_BACKEND_PORT} occupied, using {backend_port}")
    print(f"udp_game_server: {HOST}:{game_server_port}")

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
    game_server = ManagedProcess(
        "udp_game_server",
        [
            sys.executable,
            "-u",
            "tools/udp_game_server.py",
            "--host",
            HOST,
            "--port",
            str(game_server_port),
            "--timeout",
            "120",
        ],
        base_env.copy(),
    )
    client: Optional[ManagedProcess] = None
    creator_id = ""
    joiner_id = ""
    creator_status: Dict[str, Any] = {}
    creator_logs: Dict[str, Any] = {}
    joiner_status: Dict[str, Any] = {}
    joiner_logs: Dict[str, Any] = {}
    stop_results: Dict[str, Any] = {}
    phase = "startup"

    try:
        phase = "start root server"
        root.start()
        _wait_for_tcp("root server.py", root, ROOT_TCP_PORT, SERVER_READY_TIMEOUT)
        print("root_server_start: OK")

        phase = "start backend"
        backend.start()
        health = _wait_health(backend_port, backend)
        print(f"backend_health: OK mode={health.get('mode')}")

        phase = "start udp game server"
        game_server.start()
        game_server.wait_for_log("UDP Server listening", GAME_SERVER_READY_TIMEOUT)
        print("udp_game_server_start: OK")

        phase = "create creator session"
        creator = _request_ok(
            "POST",
            backend_port,
            "/sessions/create",
            {
                "server_host": HOST,
                "server_port": ROOT_TCP_PORT,
                "server_udp_port": ROOT_UDP_PORT,
                "player_name": "SmokeCreator",
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": "local_udp_bridge",
                    "bind_host": HOST,
                    "bind_port": 0,
                    "target_host": HOST,
                    "target_port": creator_placeholder_port,
                },
            },
            expected=201,
        )
        creator_id = str(creator.get("session_id", ""))
        if not creator_id:
            raise SmokeFailure("create response did not include session_id")

        phase = "wait creator room_id"
        room_id, creator_status, creator_logs = _wait_for_room_id(backend_port, creator_id)
        print(f"room_id: {room_id}")

        phase = "join session"
        joiner = _request_ok(
            "POST",
            backend_port,
            "/sessions/join",
            {
                "server_host": HOST,
                "server_port": ROOT_TCP_PORT,
                "server_udp_port": ROOT_UDP_PORT,
                "room_id": room_id,
                "player_name": "SmokeJoiner",
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": "local_udp_bridge",
                    "bind_host": HOST,
                    "bind_port": 0,
                    "target_host": HOST,
                    "target_port": game_server_port,
                },
            },
            expected=201,
        )
        joiner_id = str(joiner.get("session_id", ""))
        if not joiner_id:
            raise SmokeFailure("join response did not include session_id")

        phase = "wait sessions running and adapters ready"
        creator_status, creator_logs = _wait_session_ready(
            backend_port,
            creator_id,
            "creator",
            "room_created",
        )
        joiner_status, joiner_logs = _wait_session_ready(
            backend_port,
            joiner_id,
            "joiner",
            "room_joined",
        )
        creator_adapter = _adapter_status(creator_status)
        creator_bind_host = str(creator_adapter.get("bind_host", HOST))
        creator_bind_port = int(creator_adapter.get("bind_port", 0))
        if creator_bind_port <= 0:
            raise SmokeFailure(f"creator adapter bind_port invalid: {_safe_json(creator_adapter)}")
        print(f"creator_adapter: {creator_bind_host}:{creator_bind_port}")
        print(f"joiner_adapter: {_safe_json(_adapter_status(joiner_status))}")

        phase = "payload client"
        client = _run_client(creator_bind_port)
        print("payload_client: JOIN/WELCOME and PING/PONG OK")

        phase = "counter validation"
        creator_status, joiner_status = _wait_counters(backend_port, creator_id, joiner_id)
        creator_logs = _get_logs(backend_port, creator_id)
        joiner_logs = _get_logs(backend_port, joiner_id)
        if _contains_relay_token([creator_status, creator_logs, joiner_status, joiner_logs]):
            raise SmokeFailure("backend status/logs exposed relay_token")
        _print_session_summary("creator", creator_status, creator_logs)
        _print_session_summary("joiner", joiner_status, joiner_logs)

        phase = "stop sessions"
        stop_results = _stop_sessions(backend_port, creator_id, joiner_id)
        creator_status = _get_status(backend_port, creator_id)
        joiner_status = _get_status(backend_port, joiner_id)
        creator_logs = _get_logs(backend_port, creator_id)
        joiner_logs = _get_logs(backend_port, joiner_id)
        if _contains_relay_token([creator_status, creator_logs, joiner_status, joiner_logs]):
            raise SmokeFailure("backend status/logs exposed relay_token after stop")

        phase = "process cleanup"
        game_cleanup = game_server.stop()
        backend_cleanup = backend.stop()
        root_cleanup = root.stop()
        client_code = client.poll() if client is not None else None

        print(f"stop_results: {_safe_json(stop_results)}")
        print(
            "cleanup: "
            f"udp_game_server_{game_cleanup}, "
            f"backend_{backend_cleanup}, "
            f"root_server_{root_cleanup}, "
            f"udp_game_client_exit_code={client_code}"
        )
        print("relay_token_exposed: no")
        print("RESULT: PASS")
        return 0

    except Exception as exc:
        print("RESULT: FAIL")
        print(f"phase: {phase}")
        print(f"reason: {_sanitize(str(exc))}")
        if creator_status or creator_logs:
            _print_session_summary("creator", creator_status, creator_logs)
        if joiner_status or joiner_logs:
            _print_session_summary("joiner", joiner_status, joiner_logs)
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
        print(f"stop_results: {_safe_json(stop_results)}")
        game_cleanup = game_server.stop()
        backend_cleanup = backend.stop()
        root_cleanup = root.stop()
        print(
            "cleanup: "
            f"udp_game_server_{game_cleanup}, "
            f"backend_{backend_cleanup}, "
            f"root_server_{root_cleanup}"
        )
        for process in (client, game_server, backend, root):
            if process is not None:
                _print_process_tail(process)
        return 1


def main() -> None:
    sys.exit(run_smoke())


if __name__ == "__main__":
    main()
