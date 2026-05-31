#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parameterized adapter payload smoke for localhost and VPS-lite validation.

P5.2F scope:
  - localhost, role=both
  - vps, role=both, single local PC dual-session against a remote server.py

This helper starts and stops only processes it owns. It does not modify the
S2Pass protocol, backend, Core, server, adapters, or Flutter code.
"""
from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 9000
DEFAULT_UDP_PORT = 9001
DEFAULT_TIMEOUT = 30.0
DEFAULT_GAME_SERVER_TIMEOUT = 300.0
GAME_SERVER_READY_TIMEOUT = 5.0
STOP_TIMEOUT = 10.0
COUNTER_TIMEOUT = 5.0
RELAY_TOKEN_RE = re.compile(r"rtk_[0-9a-fA-F]+")


class SmokeFailure(Exception):
    """A smoke check failed."""


class ManagedProcess:
    def __init__(self, name: str, args: Sequence[str], env: Dict[str, str]) -> None:
        self.name = name
        self.args = list(args)
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
            name=f"S2PassNetSmokeLog-{self.name}",
            daemon=True,
        )
        self._reader_thread.start()

    def poll(self) -> Optional[int]:
        if self.proc is None:
            return None
        return self.proc.poll()

    def wait(self, timeout: float) -> int:
        if self.proc is None:
            raise SmokeFailure(f"{self.name} was not started")
        try:
            return self.proc.wait(timeout=timeout)
        finally:
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1.0)

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

    def wait_for_log_any(self, patterns: Sequence[str], timeout: float) -> str:
        lowered_patterns = [pattern.lower() for pattern in patterns]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code = self.poll()
            if code is not None:
                raise SmokeFailure(f"{self.name} exited early with code {code}")
            try:
                line = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            lowered_line = line.lower()
            if any(pattern in lowered_line for pattern in lowered_patterns):
                return line
        raise SmokeFailure(
            f"{self.name} did not emit one of {list(patterns)!r} in time"
        )

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
        text = RELAY_TOKEN_RE.sub("rtk_[redacted]", text)
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


def _is_localhost(host: str) -> bool:
    return host.lower() in {"127.0.0.1", "localhost", "::1"}


def _find_free_tcp_port(host: str = LOCAL_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _find_free_udp_port(host: str = LOCAL_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _assert_local_ports_available(host: str, tcp_port: int, udp_port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind((host, tcp_port))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind((host, udp_port))
    except OSError as exc:
        raise SmokeFailure(
            f"local server ports unavailable: {host}:{tcp_port}/{udp_port} ({exc})"
        )


def _can_connect_tcp(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_tcp(
    name: str,
    host: str,
    port: int,
    timeout: float,
    process: Optional[ManagedProcess] = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            code = process.poll()
            if code is not None:
                raise SmokeFailure(f"{name} exited early with code {code}")
        if _can_connect_tcp(host, port):
            return
        time.sleep(0.1)
    raise SmokeFailure(f"{name} did not open {host}:{port} in time")


def _http_json(
    method: str,
    host: str,
    port: int,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Tuple[int, Dict[str, Any]]:
    encoded = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
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
    host: str,
    port: int,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    expected: int = 200,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    status, data = _http_json(method, host, port, path, body=body, timeout=timeout)
    if _contains_relay_token(data):
        raise SmokeFailure(f"{method} {path} exposed relay_token")
    if status != expected:
        raise SmokeFailure(f"{method} {path} returned {status}: {_safe_json(data)}")
    return data


def _wait_health(
    host: str,
    port: int,
    timeout: float,
    backend: Optional[ManagedProcess],
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if backend is not None:
            code = backend.poll()
            if code is not None:
                raise SmokeFailure(f"backend exited early with code {code}")
        try:
            status, data = _http_json("GET", host, port, "/health", timeout=1.0)
            if _contains_relay_token(data):
                raise SmokeFailure("GET /health exposed relay_token")
            if status == 200 and data.get("mode") == "real_core":
                return data
            last_error = f"status={status}, data={_safe_json(data)}"
        except Exception as exc:
            last_error = repr(exc)
        time.sleep(0.1)
    raise SmokeFailure(f"backend /health did not become ready as real_core: {last_error}")


def _get_status(host: str, port: int, session_id: str) -> Dict[str, Any]:
    return _request_ok("GET", host, port, f"/sessions/{session_id}/status")


def _get_logs(host: str, port: int, session_id: str) -> Dict[str, Any]:
    return _request_ok("GET", host, port, f"/sessions/{session_id}/logs")


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
    result: List[Dict[str, Any]] = []
    if not isinstance(events, list):
        return result
    for event in events:
        if isinstance(event, dict) and event.get("type") == event_type:
            data = event.get("data", {})
            if isinstance(data, dict):
                result.append(data)
    return result


def _adapter_status(status: Dict[str, Any]) -> Dict[str, Any]:
    adapter_status = status.get("adapter_status")
    if not isinstance(adapter_status, dict):
        raise SmokeFailure(f"missing adapter_status in {_safe_json(status)}")
    return adapter_status


def _adapter_counters(status: Dict[str, Any]) -> Dict[str, int]:
    counters = _adapter_status(status).get("counters")
    if not isinstance(counters, dict):
        raise SmokeFailure(f"missing adapter counters in {_safe_json(status)}")
    result: Dict[str, int] = {}
    for key in (
        "packets_from_game",
        "packets_to_transport",
        "packets_from_transport",
        "packets_to_game",
    ):
        value = counters.get(key)
        if not isinstance(value, int):
            raise SmokeFailure(f"counter {key} missing/non-int: {_safe_json(counters)}")
        result[key] = value
    return result


def _validate_confirmed_room_id(
    create_room_id: str,
    status_payload: Dict[str, Any],
    logs_payload: Dict[str, Any],
) -> None:
    status_room_id = str(status_payload.get("room_id", ""))
    room_datas = _event_data(logs_payload, "room_created")
    log_room_id = str(room_datas[-1].get("room_id", "")) if room_datas else ""
    if not create_room_id:
        raise SmokeFailure("create response did not include confirmed room_id")
    if status_room_id != create_room_id or log_room_id != create_room_id:
        raise SmokeFailure(
            "confirmed room_id mismatch: "
            f"create={create_room_id}, status={status_room_id}, room_created={log_room_id}"
        )
    starting_datas = _event_data(logs_payload, "session_starting")
    for data in starting_datas:
        if "room_id" in data:
            raise SmokeFailure("session_starting unexpectedly exposed preliminary room_id")


def _wait_session_ready(
    host: str,
    port: int,
    session_id: str,
    label: str,
    required_event: str,
    timeout: float,
    fail_on_session_failed: bool = True,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_status: Dict[str, Any] = {}
    last_logs: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_status = _get_status(host, port, session_id)
        last_logs = _get_logs(host, port, session_id)
        if _contains_relay_token([last_status, last_logs]):
            raise SmokeFailure(f"{label} status/logs exposed relay_token")
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
        if fail_on_session_failed and (
            last_status.get("status") == "failed" or "session_failed" in types
        ):
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


def _parse_payload_stats(output: str, count: int) -> Dict[str, Any]:
    joined = "Received JOIN confirmation: WELCOME" in output
    pongs = len(re.findall(r"response='PONG \d+'", output))
    patterns = {
        "sent": r"^Sent:\s*(\d+)\s*$",
        "received": r"^Received:\s*(\d+)\s*$",
        "lost": r"^Lost:\s*(\d+)\s*$",
        "unexpected": r"^Unexpected:\s*(\d+)\s*$",
        "loss_percent": r"^Loss Percent:\s*([0-9.]+)%\s*$",
        "avg_rtt": r"^Average RTT:\s*([0-9.]+)\s*ms\s*$",
    }
    stats: Dict[str, Any] = {"joined": joined, "pongs": pongs}
    for key, pattern in patterns.items():
        match = re.search(pattern, output, re.MULTILINE)
        if match:
            text = match.group(1)
            stats[key] = float(text) if key in {"loss_percent", "avg_rtt"} else int(text)
    missing = [key for key in ("sent", "received", "lost", "unexpected") if key not in stats]
    if missing:
        raise SmokeFailure(f"udp_game_client output missing stats: {missing}")
    if not joined:
        raise SmokeFailure("udp_game_client did not observe JOIN/WELCOME")
    if pongs != count:
        raise SmokeFailure(f"udp_game_client observed {pongs}/{count} PONG responses")
    if stats["sent"] != count or stats["received"] != count:
        raise SmokeFailure(f"payload count mismatch: {_safe_json(stats)}")
    if stats["lost"] != 0 or stats["unexpected"] != 0:
        raise SmokeFailure(f"payload loss/unexpected packets: {_safe_json(stats)}")
    if "Exit status: 0" not in output:
        raise SmokeFailure("udp_game_client output missing successful exit status")
    return stats


def _run_payload_client(
    python_exe: str,
    host: str,
    port: int,
    count: int,
    topology: str,
    timeout: float,
) -> Tuple[ManagedProcess, Dict[str, Any]]:
    client_timeout = "10.0" if topology == "vps" else "5.0"
    client = ManagedProcess(
        "udp_game_client",
        [
            python_exe,
            "-u",
            "tools/udp_game_client.py",
            "--host",
            host,
            "--port",
            str(port),
            "--client-id",
            "net_smoke_client",
            "--count",
            str(count),
            "--interval",
            "0.1" if topology == "vps" else "0.05",
            "--timeout",
            client_timeout,
        ],
        os.environ.copy(),
    )
    client.start()
    code = client.wait(timeout=max(timeout, count * 2.0 + 10.0))
    output = "\n".join(client.logs)
    if _contains_relay_token(output):
        raise SmokeFailure("udp_game_client output exposed relay_token")
    if code != 0:
        raise SmokeFailure(f"udp_game_client exited with code {code}")
    stats = _parse_payload_stats(output, count)
    stats["exit_code"] = code
    return client, stats


def _wait_counters(
    host: str,
    port: int,
    creator_id: str,
    joiner_id: str,
    expected_minimum: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    deadline = time.monotonic() + COUNTER_TIMEOUT
    creator_status: Dict[str, Any] = {}
    joiner_status: Dict[str, Any] = {}
    last_error = ""
    while time.monotonic() < deadline:
        creator_status = _get_status(host, port, creator_id)
        joiner_status = _get_status(host, port, joiner_id)
        try:
            for label, status in (("creator", creator_status), ("joiner", joiner_status)):
                counters = _adapter_counters(status)
                for key, value in counters.items():
                    if value < expected_minimum:
                        raise SmokeFailure(
                            f"{label} counter {key}={value} < {expected_minimum}: "
                            f"{_safe_json(counters)}"
                        )
            return creator_status, joiner_status
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise SmokeFailure(last_error or "adapter counters did not reach expected minimum")


def _stop_one(host: str, port: int, label: str, session_id: str) -> Dict[str, Any]:
    status, data = _http_json(
        "POST",
        host,
        port,
        f"/sessions/{session_id}/stop",
        timeout=STOP_TIMEOUT,
    )
    if _contains_relay_token(data):
        raise SmokeFailure(f"{label} stop response exposed relay_token")
    body_status = data.get("status") if isinstance(data, dict) else None
    if status != 200 or body_status not in ("stopped", "failed"):
        raise SmokeFailure(f"{label} unexpected stop result: {_safe_json(data)}")
    return {"http_status": status, "body": data}


def _print_process_tail(process: Optional[ManagedProcess]) -> None:
    if process is None or not process.logs:
        return
    print(f"{process.name}_log_tail:")
    for line in process.logs[-20:]:
        print(str(_sanitize(line)))


def _write_json_out(path: Optional[str], summary: Dict[str, Any]) -> None:
    if not path:
        return
    sanitized = _sanitize(summary)
    if _contains_relay_token(sanitized):
        raise SmokeFailure("summary JSON still contains relay_token after sanitization")
    target = Path(path)
    target.write_text(json.dumps(sanitized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P5.2F adapter payload net smoke. Supports localhost and VPS "
            "single-PC dual-session role=both only. VPS does not start server.py."
        )
    )
    parser.add_argument("--topology", choices=("localhost", "vps"), required=True)
    parser.add_argument("--role", default="both")
    parser.add_argument("--server-host", default=None)
    parser.add_argument("--server-tcp-port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument("--server-udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--advertise-host", default=None)
    parser.add_argument("--backend-host", default=LOCAL_HOST)
    parser.add_argument("--backend-port", type=int, default=None)
    parser.add_argument("--python-path", default=None)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--game-server-timeout", type=float, default=DEFAULT_GAME_SERVER_TIMEOUT)
    parser.add_argument("--no-start-server", action="store_true")
    parser.add_argument("--no-start-backend", action="store_true")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--json-out", default=None)
    return parser


def _normalize_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> argparse.Namespace:
    if args.role != "both":
        parser.error("Split creator/joiner mode is not implemented in P5.2F.")
    if not _is_localhost(args.backend_host):
        parser.error("backend_host must be localhost/127.0.0.1 for safety.")
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")
    if args.game_server_timeout <= 0:
        parser.error("--game-server-timeout must be positive.")
    if args.count is not None and args.count < 0:
        parser.error("--count must be >= 0.")
    if args.topology == "localhost":
        args.server_host = args.server_host or LOCAL_HOST
        if (
            not args.no_start_server
            and (
                args.server_tcp_port != DEFAULT_TCP_PORT
                or args.server_udp_port != DEFAULT_UDP_PORT
            )
        ):
            parser.error(
                "local server.py uses fixed 9000/9001 ports; use default ports "
                "or pass --no-start-server for an already-running custom setup."
            )
    elif not args.server_host or _is_localhost(args.server_host):
        parser.error("--topology vps requires --server-host with a non-localhost VPS IP/host.")
    args.advertise_host = args.advertise_host or args.server_host
    args.count = args.count if args.count is not None else (10 if args.topology == "vps" else 5)
    args.python_path = args.python_path or sys.executable
    if args.backend_port is None:
        args.backend_port = 21520 if args.no_start_backend else _find_free_tcp_port(args.backend_host)
    return args


def _create_summary(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "result": "fail",
        "topology": args.topology,
        "role": args.role,
        "server_host": args.server_host,
        "server_tcp_port": args.server_tcp_port,
        "server_udp_port": args.server_udp_port,
        "room_id": "",
        "creator_session_id": "",
        "joiner_session_id": "",
        "adapter_ports": {},
        "payload_stats": {},
        "creator_counters": {},
        "joiner_counters": {},
        "cleanup": {},
        "relay_token_exposed": False,
        "errors": [],
    }


def run_smoke(args: argparse.Namespace) -> int:
    summary = _create_summary(args)
    root: Optional[ManagedProcess] = None
    backend: Optional[ManagedProcess] = None
    game_server: Optional[ManagedProcess] = None
    client: Optional[ManagedProcess] = None
    creator_id = ""
    joiner_id = ""
    phase = "startup"
    cleanup: Dict[str, Any] = {}

    print("S2Pass adapter payload net smoke")
    print(f"topology: {args.topology}")
    print(f"role: {args.role}")
    print(f"server: {args.server_host}:{args.server_tcp_port}/{args.server_udp_port}")
    print(f"advertise_host: {args.advertise_host}")
    print(f"backend_http: {args.backend_host}:{args.backend_port}")
    print(f"python: {args.python_path}")
    if args.topology == "vps":
        print("vps_server_start: skipped (P5.2F assumes remote server.py is already running)")

    base_env = os.environ.copy()

    try:
        if args.topology == "localhost" and not args.no_start_server:
            phase = "start local server"
            _assert_local_ports_available(LOCAL_HOST, args.server_tcp_port, args.server_udp_port)
            root_env = base_env.copy()
            root_env["SERVER_IP"] = args.server_host
            root_env["S2PASS_ADVERTISE_HOST"] = args.advertise_host
            root = ManagedProcess(
                "root_server",
                [args.python_path, "-u", "server.py", "--advertise-host", args.advertise_host],
                root_env,
            )
            root.start()
            _wait_for_tcp(
                "local server.py",
                args.server_host,
                args.server_tcp_port,
                min(args.timeout, 8.0),
                root,
            )
            print("root_server_start: OK")
        else:
            phase = "check server TCP"
            _wait_for_tcp(
                "server.py",
                args.server_host,
                args.server_tcp_port,
                min(args.timeout, 8.0),
                None,
            )
            print("server_tcp_reachable: OK")

        if not args.no_start_backend:
            phase = "start backend"
            backend_env = base_env.copy()
            backend_env["S2PASS_BACKEND_RUNNER"] = "real_core"
            backend = ManagedProcess(
                "backend_http",
                [
                    args.python_path,
                    "-u",
                    "-m",
                    "backend.server",
                    "--host",
                    args.backend_host,
                    "--port",
                    str(args.backend_port),
                ],
                backend_env,
            )
            backend.start()
        phase = "backend health"
        health = _wait_health(args.backend_host, args.backend_port, min(args.timeout, 8.0), backend)
        print(f"backend_health: OK mode={health.get('mode')}")

        phase = "start udp game server"
        game_server_port = _find_free_udp_port(LOCAL_HOST)
        game_server = ManagedProcess(
            "udp_game_server",
            [
                args.python_path,
                "-u",
                "tools/udp_game_server.py",
                "--host",
                LOCAL_HOST,
                "--port",
                str(game_server_port),
                "--timeout",
                str(args.game_server_timeout),
            ],
            base_env.copy(),
        )
        game_server.start()
        game_server.wait_for_log_any(
            ("UDP Server listening", "Listening on", "listening"),
            GAME_SERVER_READY_TIMEOUT,
        )
        print(f"udp_game_server: {LOCAL_HOST}:{game_server_port}")

        phase = "create creator session"
        creator = _request_ok(
            "POST",
            args.backend_host,
            args.backend_port,
            "/sessions/create",
            {
                "server_host": args.server_host,
                "server_port": args.server_tcp_port,
                "server_udp_port": args.server_udp_port,
                "player_name": "NetSmokeCreator",
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": "local_udp_bridge",
                    "bind_host": LOCAL_HOST,
                    "bind_port": 0,
                    "target_host": LOCAL_HOST,
                    "target_port": 0,
                },
            },
            expected=201,
        )
        creator_id = str(creator.get("session_id", ""))
        room_id = str(creator.get("room_id", ""))
        if not creator_id:
            raise SmokeFailure("create response did not include session_id")
        if not room_id:
            raise SmokeFailure("create response did not include confirmed room_id")
        summary["creator_session_id"] = creator_id
        summary["room_id"] = room_id
        print(f"room_id: {room_id}")
        print(f"creator_session_id: {creator_id}")

        phase = "join session"
        joiner = _request_ok(
            "POST",
            args.backend_host,
            args.backend_port,
            "/sessions/join",
            {
                "server_host": args.server_host,
                "server_port": args.server_tcp_port,
                "server_udp_port": args.server_udp_port,
                "room_id": room_id,
                "player_name": "NetSmokeJoiner",
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": "local_udp_bridge",
                    "bind_host": LOCAL_HOST,
                    "bind_port": 0,
                    "target_host": LOCAL_HOST,
                    "target_port": game_server_port,
                },
            },
            expected=201,
        )
        joiner_id = str(joiner.get("session_id", ""))
        if not joiner_id:
            raise SmokeFailure("join response did not include session_id")
        summary["joiner_session_id"] = joiner_id
        print(f"joiner_session_id: {joiner_id}")

        phase = "wait sessions running and adapters ready"
        creator_status, creator_logs = _wait_session_ready(
            args.backend_host,
            args.backend_port,
            creator_id,
            "creator",
            "room_created",
            args.timeout,
        )
        _validate_confirmed_room_id(room_id, creator_status, creator_logs)
        joiner_status, joiner_logs = _wait_session_ready(
            args.backend_host,
            args.backend_port,
            joiner_id,
            "joiner",
            "room_joined",
            args.timeout,
        )
        creator_adapter = _adapter_status(creator_status)
        joiner_adapter = _adapter_status(joiner_status)
        creator_port = int(creator_adapter.get("bind_port", 0))
        joiner_port = int(joiner_adapter.get("bind_port", 0))
        if creator_port <= 0 or joiner_port <= 0:
            raise SmokeFailure(
                "adapter bind_port invalid: "
                f"creator={_safe_json(creator_adapter)}, joiner={_safe_json(joiner_adapter)}"
            )
        summary["adapter_ports"] = {"creator": creator_port, "joiner": joiner_port}
        print(f"creator_adapter: {LOCAL_HOST}:{creator_port}")
        print(f"joiner_adapter: {LOCAL_HOST}:{joiner_port}")

        phase = "payload client"
        client, payload_stats = _run_payload_client(
            args.python_path,
            LOCAL_HOST,
            creator_port,
            args.count,
            args.topology,
            args.timeout,
        )
        summary["payload_stats"] = payload_stats
        print("payload_client: JOIN/WELCOME and PING/PONG OK")
        print(f"payload_stats: {_safe_json(payload_stats)}")

        phase = "counter validation"
        expected_minimum = args.count + 1
        creator_status, joiner_status = _wait_counters(
            args.backend_host,
            args.backend_port,
            creator_id,
            joiner_id,
            expected_minimum,
        )
        creator_logs = _get_logs(args.backend_host, args.backend_port, creator_id)
        joiner_logs = _get_logs(args.backend_host, args.backend_port, joiner_id)
        if "session_failed" in _event_types(creator_logs) or "session_failed" in _event_types(joiner_logs):
            raise SmokeFailure("session_failed appeared before cleanup")
        if _contains_relay_token([creator_status, creator_logs, joiner_status, joiner_logs]):
            raise SmokeFailure("backend status/logs exposed relay_token")
        creator_counters = _adapter_counters(creator_status)
        joiner_counters = _adapter_counters(joiner_status)
        summary["creator_counters"] = creator_counters
        summary["joiner_counters"] = joiner_counters
        print(f"expected_counter_minimum: {expected_minimum}")
        print(f"creator_counters: {_safe_json(creator_counters)}")
        print(f"joiner_counters: {_safe_json(joiner_counters)}")

        summary["result"] = "pass"

    except Exception as exc:
        summary["result"] = "fail"
        reason = str(exc)
        if "relay_token" in reason or "rtk_" in reason:
            summary["relay_token_exposed"] = True
        summary["errors"].append(str(_sanitize(reason)))
        print(f"phase: {phase}")
        print(f"reason: {_sanitize(reason)}")

    finally:
        if not args.no_cleanup:
            if creator_id:
                try:
                    cleanup["creator_stop"] = _stop_one(
                        args.backend_host, args.backend_port, "creator", creator_id
                    )
                except Exception as exc:
                    cleanup["creator_stop"] = {"error": str(_sanitize(str(exc)))}
            if joiner_id:
                try:
                    cleanup["joiner_stop"] = _stop_one(
                        args.backend_host, args.backend_port, "joiner", joiner_id
                    )
                except Exception as exc:
                    cleanup["joiner_stop"] = {"error": str(_sanitize(str(exc)))}
            if game_server is not None:
                cleanup["udp_game_server"] = game_server.stop()
            if backend is not None:
                cleanup["backend"] = backend.stop()
            if root is not None:
                cleanup["root_server"] = root.stop()
        else:
            cleanup["skipped"] = True
        summary["cleanup"] = cleanup
        print(f"cleanup: {_safe_json(cleanup)}")
        try:
            _write_json_out(args.json_out, summary)
        except Exception as exc:
            print(f"json_out_error: {_sanitize(str(exc))}")
        if summary["result"] != "pass":
            for process in (client, game_server, backend, root):
                _print_process_tail(process)
        print(f"relay_token_exposed: {'yes' if summary['relay_token_exposed'] else 'no'}")
        print(f"RESULT: {summary['result'].upper()}")

    return 0 if summary["result"] == "pass" else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = _normalize_args(parser.parse_args(argv), parser)
    return run_smoke(args)


if __name__ == "__main__":
    sys.exit(main())
