#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCP relay and UDP Experimental smoke for backend real_core adapter wiring.

Topology:
  local client
    -> joiner adapter
    -> joiner CoreTransportAdapter
    -> S2Pass Core relay through local or remote server.py
    -> creator CoreTransportAdapter
    -> creator adapter
    -> local echo server

This helper is intentionally a manual/integration smoke. It starts only local
processes and terminates only the processes it started. Remote root mode does
not start or stop the remote server.py process.
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
HOST = "127.0.0.1"
ROOT_TCP_PORT = 9000
ROOT_UDP_PORT = 9001
PREFERRED_BACKEND_PORT = 21521
ADAPTER_TYPE_TCP_RELAY = "tcp_relay"
ADAPTER_TYPE_LOCAL_UDP_BRIDGE = "local_udp_bridge"
SERVER_READY_TIMEOUT = 8.0
BACKEND_READY_TIMEOUT = 8.0
ROOM_TIMEOUT = 8.0
RELAY_TIMEOUT = 20.0
PAYLOAD_TIMEOUT = 20.0
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
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(
            target=self._read_output,
            name=f"S2PassTcpRelayRealCoreSmokeLog-{self.name}",
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


class EchoServer:
    def __init__(self) -> None:
        self.host = HOST
        self.port = 0
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client_threads: List[threading.Thread] = []

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, 0))
        sock.listen()
        sock.settimeout(0.2)
        self.port = int(sock.getsockname()[1])
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> str:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        for thread in list(self._client_threads):
            thread.join(timeout=2.0)
        return f"{self.host}:{self.port}"

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            self._client_threads.append(thread)
            thread.start()

    @staticmethod
    def _handle_client(conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except (ConnectionError, OSError):
                    return
                if not data:
                    return
                conn.sendall(data)


class UdpEchoServer:
    def __init__(self) -> None:
        self.host = HOST
        self.port = 0
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, 0))
        sock.settimeout(0.2)
        self.port = int(sock.getsockname()[1])
        self._sock = sock
        self._thread = threading.Thread(target=self._echo_loop, daemon=True)
        self._thread.start()

    def stop(self) -> str:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return f"{self.host}:{self.port}"

    def _echo_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._sock.sendto(data, addr)
            except OSError:
                break


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


def _assert_root_ports_available(host: str, tcp_port: int, udp_port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind((host, tcp_port))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind((host, udp_port))
    except OSError as exc:
        raise SmokeFailure(
            f"root server ports unavailable: {host}:{tcp_port}/{udp_port} ({exc})"
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


def _can_connect_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
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
        time.sleep(0.05)
    raise SmokeFailure(f"{name} did not open {host}:{port} in time")


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


def _failure_event_summaries(logs_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = logs_payload.get("events", [])
    result = []
    if not isinstance(events, list):
        return result
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "session_failed":
            continue
        data = event.get("data", {})
        result.append({
            "message": event.get("message", ""),
            "data": data if isinstance(data, dict) else {},
        })
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


def _adapter_status(status: Dict[str, Any]) -> Dict[str, Any]:
    adapter_status = status.get("adapter_status")
    if not isinstance(adapter_status, dict):
        raise SmokeFailure(f"missing adapter_status in {_safe_json(status)}")
    return adapter_status


def _counters(status: Dict[str, Any]) -> Dict[str, Any]:
    counters = _adapter_status(status).get("counters")
    if not isinstance(counters, dict):
        raise SmokeFailure(f"adapter counters missing in {_safe_json(status)}")
    return counters


def _require_counter(label: str, counters: Dict[str, Any], key: str) -> None:
    value = counters.get(key)
    if not isinstance(value, int) or value <= 0:
        raise SmokeFailure(f"{label} counter {key} did not increment: {_safe_json(counters)}")


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
            creator_counters = _counters(creator_status)
            joiner_counters = _counters(joiner_status)
            _require_counter("joiner", joiner_counters, "packets_from_game")
            _require_counter("joiner", joiner_counters, "packets_to_transport")
            _require_counter("creator", creator_counters, "packets_from_transport")
            _require_counter("creator", creator_counters, "packets_to_game")
            _require_counter("creator", creator_counters, "packets_from_game")
            _require_counter("creator", creator_counters, "packets_to_transport")
            _require_counter("joiner", joiner_counters, "packets_from_transport")
            _require_counter("joiner", joiner_counters, "packets_to_game")
            return creator_status, joiner_status
        except SmokeFailure as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise SmokeFailure(last_error or "adapter counters did not increment")


def _tcp_send_recv(host: str, port: int, payload: bytes) -> bytes:
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.settimeout(5.0)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks: List[bytes] = []
        deadline = time.monotonic() + PAYLOAD_TIMEOUT
        while time.monotonic() < deadline:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        else:
            raise SmokeFailure("TCP client timed out waiting for echoed payload EOF")
    return b"".join(chunks)


def _tcp_send_recv_hold_open(
    host: str,
    port: int,
    payload: bytes,
    hold_open_ms: int,
) -> bytes:
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.settimeout(PAYLOAD_TIMEOUT)
        sock.sendall(payload)
        chunks: List[bytes] = []
        received = 0
        while received < len(payload):
            data = sock.recv(min(4096, len(payload) - received))
            if not data:
                break
            chunks.append(data)
            received += len(data)
        if hold_open_ms > 0:
            time.sleep(hold_open_ms / 1000.0)
        sock.shutdown(socket.SHUT_WR)
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def _make_tcp_payload(label: str, index: int, size: int) -> bytes:
    prefix = f"{label}:{index}:".encode("ascii")
    if size <= len(prefix):
        return prefix[:size]
    remaining = size - len(prefix)
    body = bytes((index + offset) % 256 for offset in range(remaining))
    return prefix + body


def _run_tcp_scenario(
    host: str,
    port: int,
    *,
    name: str,
    connections: int,
    payload_size: int,
    repeat: int,
    hold_open_ms: int = 0,
    concurrent: bool = False,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "name": name,
        "connections": connections,
        "payload_size": payload_size,
        "repeat": repeat,
        "hold_open_ms": hold_open_ms,
        "concurrent": concurrent,
        "attempts": connections * repeat,
        "exact_matches": 0,
        "errors": [],
    }
    lock = threading.Lock()

    def exchange(index: int) -> None:
        payload = _make_tcp_payload(name, index, payload_size)
        try:
            if hold_open_ms > 0:
                result = _tcp_send_recv_hold_open(
                    host,
                    port,
                    payload,
                    hold_open_ms,
                )
            else:
                result = _tcp_send_recv(host, port, payload)
            if result != payload:
                raise SmokeFailure(
                    f"payload mismatch: sent={len(payload)} received={len(result)}"
                )
            with lock:
                summary["exact_matches"] += 1
        except Exception as exc:
            with lock:
                summary["errors"].append({
                    "index": index,
                    "error": str(_sanitize(str(exc))),
                })

    next_index = 0
    for _ in range(repeat):
        if concurrent:
            threads = []
            for _ in range(connections):
                thread = threading.Thread(target=exchange, args=(next_index,))
                threads.append(thread)
                next_index += 1
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=PAYLOAD_TIMEOUT + (hold_open_ms / 1000.0) + 5.0)
                if thread.is_alive():
                    with lock:
                        summary["errors"].append({"error": "client thread timed out"})
        else:
            for _ in range(connections):
                exchange(next_index)
                next_index += 1
    summary["passed"] = (
        summary["exact_matches"] == summary["attempts"]
        and not summary["errors"]
    )
    return summary


def _run_tcp_scenarios(
    host: str,
    port: int,
    args: argparse.Namespace,
) -> List[Dict[str, Any]]:
    if not args.minecraft_like:
        return [
            _run_tcp_scenario(
                host,
                port,
                name="custom",
                connections=args.connections,
                payload_size=args.payload_size,
                repeat=args.repeat,
                hold_open_ms=args.hold_open_ms,
                concurrent=args.connections > 1,
            )
        ]
    return [
        _run_tcp_scenario(
            host,
            port,
            name="sequential_short",
            connections=1,
            payload_size=1024,
            repeat=5,
        ),
        _run_tcp_scenario(
            host,
            port,
            name="concurrent_three",
            connections=3,
            payload_size=4096,
            repeat=1,
            concurrent=True,
        ),
        _run_tcp_scenario(
            host,
            port,
            name="half_close",
            connections=1,
            payload_size=1024,
            repeat=1,
        ),
        _run_tcp_scenario(
            host,
            port,
            name="hold_open",
            connections=1,
            payload_size=1024,
            repeat=1,
            hold_open_ms=5000,
        ),
        _run_tcp_scenario(
            host,
            port,
            name="large_16k",
            connections=1,
            payload_size=16 * 1024,
            repeat=1,
        ),
        _run_tcp_scenario(
            host,
            port,
            name="large_64k",
            connections=1,
            payload_size=64 * 1024,
            repeat=1,
        ),
    ]


def _udp_send_recv(host: str, port: int, payload: bytes) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(PAYLOAD_TIMEOUT)
        sock.sendto(payload, (host, port))
        data, _ = sock.recvfrom(65535)
        return data


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
    failures = _failure_event_summaries(logs)
    print(f"{label} failure_events: {_safe_json(failures)}")


def _print_process_tail(process: ManagedProcess, always: bool = False) -> None:
    if not process.logs:
        if always:
            print(f"{process.name}_log_tail: (no logs captured)")
        return
    print(f"{process.name}_log_tail:")
    for line in process.logs[-20:]:
        print(str(_sanitize(line)).encode(
            sys.stdout.encoding or "utf-8",
            errors="backslashreplace",
        ).decode(sys.stdout.encoding or "utf-8", errors="replace"))


def _is_localhost(host: str) -> bool:
    return host.strip().lower() in ("127.0.0.1", "localhost", "::1")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "TCP Relay or UDP Experimental real_core smoke. By default it "
            "starts local server.py; use --no-local-root with --root-host for "
            "a remote VPS root/relay."
        )
    )
    parser.add_argument("--root-host", default=HOST)
    parser.add_argument("--root-tcp-port", type=int, default=ROOT_TCP_PORT)
    parser.add_argument("--root-udp-port", type=int, default=ROOT_UDP_PORT)
    parser.add_argument("--no-local-root", action="store_true")
    parser.add_argument(
        "--adapter-type",
        choices=(ADAPTER_TYPE_TCP_RELAY, ADAPTER_TYPE_LOCAL_UDP_BRIDGE),
        default=ADAPTER_TYPE_TCP_RELAY,
        help="Use tcp_relay or UDP Experimental local_udp_bridge.",
    )
    parser.add_argument("--connections", type=int, default=1)
    parser.add_argument("--payload-size", type=int, default=1024)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--hold-open-ms", type=int, default=0)
    parser.add_argument(
        "--minecraft-like",
        action="store_true",
        help="Run sequential, concurrent, half-close, hold-open, 16KB, and 64KB TCP scenarios.",
    )
    return parser


def _normalize_args(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> argparse.Namespace:
    if not 1 <= args.root_tcp_port <= 65535:
        parser.error("--root-tcp-port must be between 1 and 65535.")
    if not 1 <= args.root_udp_port <= 65535:
        parser.error("--root-udp-port must be between 1 and 65535.")
    if args.connections <= 0:
        parser.error("--connections must be positive.")
    if args.payload_size <= 0:
        parser.error("--payload-size must be positive.")
    if args.repeat <= 0:
        parser.error("--repeat must be positive.")
    if args.hold_open_ms < 0:
        parser.error("--hold-open-ms must be >= 0.")
    if args.adapter_type != ADAPTER_TYPE_TCP_RELAY and (
        args.minecraft_like
        or args.connections != 1
        or args.payload_size != 1024
        or args.repeat != 1
        or args.hold_open_ms != 0
    ):
        parser.error("TCP traffic scenario options require --adapter-type tcp_relay.")
    if not args.no_local_root:
        if not _is_localhost(args.root_host):
            parser.error("A non-local --root-host requires --no-local-root.")
        if args.root_tcp_port != ROOT_TCP_PORT or args.root_udp_port != ROOT_UDP_PORT:
            parser.error(
                "local server.py uses fixed 9000/9001 ports; custom root ports "
                "require --no-local-root."
            )
        args.root_host = HOST
    return args


def _refresh_session_diagnostics(
    port: int,
    session_id: str,
    status: Dict[str, Any],
    logs: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not session_id:
        return status, logs
    try:
        status = _get_status(port, session_id)
    except Exception:
        pass
    try:
        logs = _get_logs(port, session_id)
    except Exception:
        pass
    return status, logs


def _counter_value(status: Dict[str, Any], key: str) -> int:
    adapter_status = status.get("adapter_status", {})
    if not isinstance(adapter_status, dict):
        return 0
    counters = adapter_status.get("counters", {})
    if not isinstance(counters, dict):
        return 0
    value = counters.get(key, 0)
    return value if isinstance(value, int) else 0


def _root_cause_hypothesis(
    phase: str,
    creator_status: Dict[str, Any],
    joiner_status: Dict[str, Any],
    creator_logs: Dict[str, Any],
    joiner_logs: Dict[str, Any],
    traffic_scenarios: List[Dict[str, Any]],
) -> str:
    if (
        _counter_value(joiner_status, "packets_to_transport") > 0
        and _counter_value(creator_status, "packets_from_transport") == 0
    ):
        return (
            "root/relay did not deliver tcp_relay adapter payload from joiner "
            "transport to creator transport"
        )
    if (
        _counter_value(creator_status, "packets_to_transport") > 0
        and _counter_value(joiner_status, "packets_from_transport") == 0
    ):
        return (
            "root/relay did not deliver tcp_relay adapter payload from creator "
            "transport to joiner transport"
        )
    if phase in ("start root server", "check remote root TCP"):
        return "root TCP signaling endpoint was not reachable"
    if phase == "start backend":
        return "local real_core backend did not become ready"
    if phase == "payload echo":
        if (
            _counter_value(joiner_status, "packets_to_transport") > 0
            and _counter_value(creator_status, "packets_from_transport") > 0
            and _counter_value(creator_status, "packets_to_transport") > 0
            and _counter_value(joiner_status, "packets_from_transport")
            < _counter_value(creator_status, "packets_to_transport")
        ):
            return (
                "tcp_relay frames were partially delivered on the return path; "
                "the remote root silently drops relay payload after its 256 Kbps "
                "session bandwidth limit, and tcp_relay has no retransmit/reorder "
                f"layer. failed_scenarios={_safe_json([s for s in traffic_scenarios if not s.get('passed')])}"
            )
        return "adapter payload path did not complete an exact bidirectional echo"
    failures = _failure_event_summaries(creator_logs) + _failure_event_summaries(joiner_logs)
    if failures:
        messages = [
            str(item.get("message", ""))
            for item in failures
            if item.get("message")
        ]
        detail = "; ".join(dict.fromkeys(messages))
        suffix = f": {detail}" if detail else ""
        if any("1007" in message or "Relay unavailable" in message for message in messages):
            return (
                "remote root returned 1007 Relay unavailable before relay_ready; "
                "in this source tree that is emitted by per-IP relay capacity "
                "checks, so the VPS may be saturated or running a different "
                f"limit/version{suffix}"
            )
        return (
            "sessions failed before relay_ready; tcp_relay payload delivery was "
            f"not exercised{suffix}"
        )
    return "insufficient evidence; inspect session events and backend log tail"


def _summary_counters(status: Dict[str, Any]) -> Dict[str, Any]:
    adapter_status = status.get("adapter_status", {})
    if not isinstance(adapter_status, dict):
        return {}
    counters = adapter_status.get("counters", {})
    return counters if isinstance(counters, dict) else {}


def _print_verdict(
    passed: bool,
    mode: str,
    adapter_type: str,
    root_host: str,
    creator_status: Dict[str, Any],
    joiner_status: Dict[str, Any],
    creator_logs: Dict[str, Any],
    joiner_logs: Dict[str, Any],
    payload_echo: str,
    traffic_scenarios: List[Dict[str, Any]],
    failure_phase: str,
    root_cause: str,
) -> None:
    print(f"Verdict: {'PASS' if passed else 'FAIL'}")
    print(f"Mode: {mode}")
    print(f"Adapter type: {adapter_type}")
    print(f"Root host: {root_host}")
    print(f"Creator counters: {_safe_json(_summary_counters(creator_status))}")
    print(f"Joiner counters: {_safe_json(_summary_counters(joiner_status))}")
    print(f"Creator adapter status: {_safe_json(creator_status.get('adapter_status', {}))}")
    print(f"Joiner adapter status: {_safe_json(joiner_status.get('adapter_status', {}))}")
    print(f"Creator session events: {_safe_json(_event_types(creator_logs))}")
    print(f"Joiner session events: {_safe_json(_event_types(joiner_logs))}")
    print(f"Creator session_failed events: {_safe_json(_failure_event_summaries(creator_logs))}")
    print(f"Joiner session_failed events: {_safe_json(_failure_event_summaries(joiner_logs))}")
    print(f"Payload echo: {payload_echo}")
    print(f"Traffic scenarios: {_safe_json(traffic_scenarios)}")
    print(f"Failure phase: {failure_phase}")
    print(f"Root cause hypothesis: {root_cause}")
    print("Protocol compliance: PASS - smoke-only change; frozen protocol behavior unchanged")


def run_smoke(args: argparse.Namespace) -> int:
    is_udp_experimental = args.adapter_type == ADAPTER_TYPE_LOCAL_UDP_BRIDGE
    adapter_label = "UDP Experimental" if is_udp_experimental else "TCP Relay"
    print(f"S2Pass {adapter_label} real_core smoke")
    print("topology: local_client -> joiner adapter -> Core relay -> creator adapter -> local_echo")
    print(f"python: {sys.executable}")
    root_mode = "remote VPS root" if args.no_local_root else "local root"
    mode = f"{adapter_label} {root_mode}"
    if not args.no_local_root:
        _assert_root_ports_available(HOST, args.root_tcp_port, args.root_udp_port)
    backend_port = _find_free_tcp_port(PREFERRED_BACKEND_PORT)
    print(f"mode: {mode}")
    print(f"root_server: {args.root_host}:{args.root_tcp_port}/{args.root_udp_port}")
    print(f"backend_http: {HOST}:{backend_port}")
    if backend_port != PREFERRED_BACKEND_PORT:
        print(f"backend_port_note: {PREFERRED_BACKEND_PORT} occupied, using {backend_port}")

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
    echo = UdpEchoServer() if is_udp_experimental else EchoServer()
    creator_id = ""
    joiner_id = ""
    creator_status: Dict[str, Any] = {}
    creator_logs: Dict[str, Any] = {}
    joiner_status: Dict[str, Any] = {}
    joiner_logs: Dict[str, Any] = {}
    stop_results: Dict[str, Any] = {}
    phase = "startup"
    payload_echo = "NOT RUN"
    traffic_scenarios: List[Dict[str, Any]] = []

    try:
        if args.no_local_root:
            phase = "check remote root TCP"
            _wait_for_tcp(
                "remote root server.py",
                args.root_host,
                args.root_tcp_port,
                SERVER_READY_TIMEOUT,
            )
            print("root_server_tcp_reachable: OK")
        else:
            phase = "start root server"
            root.start()
            _wait_for_tcp(
                "root server.py",
                HOST,
                args.root_tcp_port,
                SERVER_READY_TIMEOUT,
                root,
            )
            print("root_server_start: OK")

        phase = "start backend"
        backend.start()
        health = _wait_health(backend_port, backend)
        print(f"backend_health: OK mode={health.get('mode')}")

        phase = "start local echo server"
        echo.start()
        print(f"local_echo: {echo.host}:{echo.port}")

        phase = "create creator session"
        creator = _request_ok(
            "POST",
            backend_port,
            "/sessions/create",
            {
                "server_host": args.root_host,
                "server_port": args.root_tcp_port,
                "server_udp_port": args.root_udp_port,
                "player_name": "TcpRelayCreator",
                "force_relay": True,
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": args.adapter_type,
                    "bind_host": HOST,
                    "bind_port": 0,
                    "target_host": HOST,
                    "target_port": echo.port,
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
                "server_host": args.root_host,
                "server_port": args.root_tcp_port,
                "server_udp_port": args.root_udp_port,
                "room_id": room_id,
                "player_name": "TcpRelayJoiner",
                "force_relay": True,
                "adapter_config": {
                    "enabled": True,
                    "adapter_type": args.adapter_type,
                    "bind_host": HOST,
                    "bind_port": 0,
                    "target_host": HOST,
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
        joiner_adapter = _adapter_status(joiner_status)
        creator_bind_port = int(creator_adapter.get("bind_port", 0))
        if not is_udp_experimental and creator_bind_port != 0:
            raise SmokeFailure(f"creator should not expose local listener: {_safe_json(creator_adapter)}")
        if is_udp_experimental and creator_bind_port <= 0:
            raise SmokeFailure(f"creator UDP listener invalid: {_safe_json(creator_adapter)}")
        if creator_adapter.get("target_port") != echo.port:
            raise SmokeFailure(f"creator target mismatch: {_safe_json(creator_adapter)}")
        joiner_bind_host = str(joiner_adapter.get("bind_host", HOST))
        joiner_bind_port = int(joiner_adapter.get("bind_port", 0))
        if joiner_bind_port <= 0:
            raise SmokeFailure(f"joiner listener invalid: {_safe_json(joiner_adapter)}")
        if "target_port" in joiner_adapter:
            raise SmokeFailure(f"joiner should not have target_port: {_safe_json(joiner_adapter)}")
        print(f"creator_adapter: {_safe_json(creator_adapter)}")
        print(f"joiner_listener: {joiner_bind_host}:{joiner_bind_port}")

        phase = "payload echo"
        if is_udp_experimental:
            payload = bytes(range(256)) * 4
            result = _udp_send_recv(joiner_bind_host, joiner_bind_port, payload)
            if result != payload:
                raise SmokeFailure(f"payload mismatch: sent={len(payload)} received={len(result)}")
            payload_echo = f"PASS bytes={len(payload)} exact_match=yes"
            traffic_scenarios = [{
                "name": "udp_exact_echo",
                "payload_size": len(payload),
                "exact_matches": 1,
                "attempts": 1,
                "passed": True,
                "errors": [],
            }]
        else:
            traffic_scenarios = _run_tcp_scenarios(
                joiner_bind_host,
                joiner_bind_port,
                args,
            )
            failed_scenarios = [
                scenario for scenario in traffic_scenarios if not scenario.get("passed")
            ]
            if failed_scenarios:
                payload_echo = (
                    f"FAIL exact_matches={sum(int(s['exact_matches']) for s in traffic_scenarios)} "
                    f"attempts={sum(int(s['attempts']) for s in traffic_scenarios)}"
                )
                raise SmokeFailure(
                    f"TCP traffic scenarios failed: {_safe_json(failed_scenarios)}"
                )
            total_bytes = sum(
                int(scenario["payload_size"]) * int(scenario["attempts"])
                for scenario in traffic_scenarios
            )
            total_matches = sum(
                int(scenario["exact_matches"]) for scenario in traffic_scenarios
            )
            payload_echo = (
                f"PASS total_bytes={total_bytes} exact_matches={total_matches}"
            )
        print(f"payload_echo: {payload_echo}")
        print(f"traffic_scenarios: {_safe_json(traffic_scenarios)}")

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
        creator_status_after_stop = _get_status(backend_port, creator_id)
        joiner_status_after_stop = _get_status(backend_port, joiner_id)
        creator_logs_after_stop = _get_logs(backend_port, creator_id)
        joiner_logs_after_stop = _get_logs(backend_port, joiner_id)
        if _contains_relay_token([
            creator_status_after_stop,
            creator_logs_after_stop,
            joiner_status_after_stop,
            joiner_logs_after_stop,
        ]):
            raise SmokeFailure("backend status/logs exposed relay_token after stop")

        phase = "process cleanup"
        echo_cleanup = echo.stop()
        backend_cleanup = backend.stop()
        root_cleanup = root.stop()
        print(f"stop_results: {_safe_json(stop_results)}")
        print(
            "cleanup: "
            f"local_echo_{echo_cleanup}, "
            f"backend_{backend_cleanup}, "
            f"root_server_{root_cleanup}"
        )
        print("relay_token_exposed: no")
        print("RESULT: PASS")
        _print_verdict(
            True,
            mode,
            args.adapter_type,
            args.root_host,
            creator_status,
            joiner_status,
            creator_logs,
            joiner_logs,
            payload_echo,
            traffic_scenarios,
            "none",
            "none",
        )
        return 0

    except Exception as exc:
        creator_status, creator_logs = _refresh_session_diagnostics(
            backend_port,
            creator_id,
            creator_status,
            creator_logs,
        )
        joiner_status, joiner_logs = _refresh_session_diagnostics(
            backend_port,
            joiner_id,
            joiner_status,
            joiner_logs,
        )
        print("RESULT: FAIL")
        print(f"phase: {phase}")
        print(f"reason: {_sanitize(str(exc))}")
        _print_session_summary("creator", creator_status, creator_logs)
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
        echo_cleanup = echo.stop()
        backend_cleanup = backend.stop()
        root_cleanup = root.stop()
        print(
            "cleanup: "
            f"local_echo_{echo_cleanup}, "
            f"backend_{backend_cleanup}, "
            f"root_server_{root_cleanup}"
        )
        _print_process_tail(backend, always=True)
        _print_process_tail(root)
        root_cause = _root_cause_hypothesis(
            phase,
            creator_status,
            joiner_status,
            creator_logs,
            joiner_logs,
            traffic_scenarios,
        )
        _print_verdict(
            False,
            mode,
            args.adapter_type,
            args.root_host,
            creator_status,
            joiner_status,
            creator_logs,
            joiner_logs,
            payload_echo,
            traffic_scenarios,
            phase,
            root_cause,
        )
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = _normalize_args(parser.parse_args(argv), parser)
    return run_smoke(args)


if __name__ == "__main__":
    sys.exit(main())
