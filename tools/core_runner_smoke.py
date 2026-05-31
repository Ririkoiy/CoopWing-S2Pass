#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local deterministic smoke for CoreSessionRunner with real network_core.

Starts a local server.py process, drives one creator and one joiner
CoreSessionRunner, verifies backend lifecycle events, then cleans up.
"""
from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import queue
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.core_session_runner import CoreSessionRunner
from backend.models import SessionInfo, SessionStats

HOST = "127.0.0.1"
TCP_PORT = 9000
UDP_PORT = 9001
READY_TIMEOUT = 8.0
ROOM_TIMEOUT = 8.0
RELAY_TIMEOUT = 15.0
STOP_TIMEOUT = 10.0


class SmokeFailure(Exception):
    """A smoke check failed."""


class EventCollector:
    def __init__(self, name: str) -> None:
        self.name = name
        self.events: List[Tuple[str, str, Dict[str, Any]]] = []
        self._cond = threading.Condition()

    def emit(
        self,
        event_type: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._cond:
            self.events.append((event_type, message, data or {}))
            self._cond.notify_all()

    def wait_for(self, event_type: str, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for seen_type, _message, data in self.events:
                    if seen_type == event_type:
                        return data
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SmokeFailure(
                        f"{self.name}: timed out waiting for {event_type}"
                    )
                self._cond.wait(timeout=remaining)

    def count(self, event_type: str) -> int:
        with self._cond:
            return sum(1 for seen_type, _message, _data in self.events
                       if seen_type == event_type)

    def types(self) -> List[str]:
        with self._cond:
            return [event_type for event_type, _message, _data in self.events]

    def has_event(self, event_type: str) -> bool:
        with self._cond:
            return any(seen_type == event_type
                       for seen_type, _message, _data in self.events)

    def relay_token_exposed(self) -> bool:
        with self._cond:
            return any(_contains_relay_token(data)
                       for _event_type, _message, data in self.events)


class ServerProcess:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen[str]] = None
        self.logs: List[str] = []
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        _assert_ports_available()

        env = os.environ.copy()
        env["SERVER_IP"] = HOST
        env["S2PASS_ADVERTISE_HOST"] = HOST

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "server.py",
                "--advertise-host",
                HOST,
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(
            target=self._read_output,
            name="S2PassCoreRunnerSmokeServerLog",
            daemon=True,
        )
        self._reader_thread.start()
        self._wait_until_ready()

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

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            self._drain_log_queue()
            if self.proc is not None and self.proc.poll() is not None:
                raise SmokeFailure(
                    f"server.py exited early with code {self.proc.returncode}"
                )
            if _can_connect_tcp(HOST, TCP_PORT):
                return
            time.sleep(0.05)
        raise SmokeFailure(
            "server.py did not open 127.0.0.1:9000 in time. "
            "Manual command: python -u server.py --advertise-host 127.0.0.1"
        )

    def _drain_log_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


def _assert_ports_available() -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind((HOST, TCP_PORT))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind((HOST, UDP_PORT))
    except OSError as exc:
        raise SmokeFailure(
            f"Default local server ports are not available: "
            f"{HOST}:{TCP_PORT}/{UDP_PORT}. "
            f"Manual command after freeing ports: "
            f"python -u server.py --advertise-host 127.0.0.1 ({exc})"
        )


def _can_connect_tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _make_info(session_id: str, role: str, player_name: str,
               room_id: Optional[str] = None) -> SessionInfo:
    now = time.time()
    return SessionInfo(
        session_id=session_id,
        role=role,
        status="starting",
        room_id=room_id,
        player_name=player_name,
        server_host=HOST,
        server_port=TCP_PORT,
        server_udp_port=UDP_PORT,
        created_at=now,
        updated_at=now,
        stats=SessionStats(),
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


def _stop_runners(
    creator: CoreSessionRunner,
    creator_info: SessionInfo,
    creator_events: EventCollector,
    joiner: CoreSessionRunner,
    joiner_info: SessionInfo,
    joiner_events: EventCollector,
) -> List[str]:
    errors: List[str] = []
    barrier = threading.Barrier(3)

    def stop_one(
        runner: CoreSessionRunner,
        info: SessionInfo,
        events: EventCollector,
    ) -> None:
        try:
            barrier.wait(timeout=2.0)
            runner.stop(info, events.emit)
        except Exception as exc:
            errors.append(f"{events.name}: stop raised {exc!r}")

    threads = [
        threading.Thread(
            target=stop_one,
            args=(creator, creator_info, creator_events),
            name="SmokeStopCreator",
        ),
        threading.Thread(
            target=stop_one,
            args=(joiner, joiner_info, joiner_events),
            name="SmokeStopJoiner",
        ),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2.0)
    for thread in threads:
        thread.join(timeout=STOP_TIMEOUT)
        if thread.is_alive():
            errors.append(f"{thread.name}: stop thread did not exit")
    return errors


def _verify_stop_events(collectors: Iterable[EventCollector]) -> None:
    for collector in collectors:
        stopping = collector.count("session_stopping")
        stopped = collector.count("session_stopped")
        if stopping != 1 or stopped != 1:
            raise SmokeFailure(
                f"{collector.name}: expected one session_stopping and one "
                f"session_stopped, got {stopping}/{stopped}"
            )


def _verify_no_failures(collectors: Iterable[EventCollector],
                        server_logs: List[str]) -> None:
    for collector in collectors:
        if collector.has_event("session_failed"):
            raise SmokeFailure(f"{collector.name}: session_failed was emitted")
        if collector.relay_token_exposed():
            raise SmokeFailure(f"{collector.name}: relay_token exposed in event data")

    bad_log_markers = ("Traceback", "Unhandled", "UnboundLocalError")
    for line in server_logs:
        if any(marker in line for marker in bad_log_markers):
            raise SmokeFailure(f"server.py log contains unexpected error: {line}")


def _print_events(label: str, collector: EventCollector) -> None:
    print(f"{label} events: {', '.join(collector.types())}")


def _print_failure_details(label: str, collector: EventCollector) -> None:
    for event_type, message, data in collector.events:
        if event_type == "session_failed":
            print(f"{label} failure: {message} {data}")


def run_smoke() -> int:
    print("S2Pass CoreSessionRunner local real-core smoke")
    print(f"server: {HOST}:{TCP_PORT}/{UDP_PORT}")

    server = ServerProcess()
    creator = CoreSessionRunner(stop_timeout=STOP_TIMEOUT)
    joiner = CoreSessionRunner(stop_timeout=STOP_TIMEOUT)
    creator_events = EventCollector("creator")
    joiner_events = EventCollector("joiner")
    creator_info = _make_info("s_smoke_create", "create", "SmokeCreator")
    joiner_info: Optional[SessionInfo] = None
    cleanup_result = "not_started"

    try:
        server.start()
        print("server_start: OK")

        creator.start_create(creator_info, creator_events.emit)
        room_data = creator_events.wait_for("room_created", ROOM_TIMEOUT)
        room_id = room_data.get("room_id")
        if not isinstance(room_id, str) or not room_id:
            raise SmokeFailure("creator: room_created did not include room_id")
        creator_info.room_id = room_id

        joiner_info = _make_info("s_smoke_join", "join", "SmokeJoiner", room_id)
        joiner.start_join(joiner_info, joiner_events.emit)

        joiner_events.wait_for("room_joined", ROOM_TIMEOUT)
        creator_events.wait_for("relay_ready", RELAY_TIMEOUT)
        creator_events.wait_for("session_running", RELAY_TIMEOUT)
        joiner_events.wait_for("relay_ready", RELAY_TIMEOUT)
        joiner_events.wait_for("session_running", RELAY_TIMEOUT)

        stop_errors = _stop_runners(
            creator,
            creator_info,
            creator_events,
            joiner,
            joiner_info,
            joiner_events,
        )
        if stop_errors:
            raise SmokeFailure("; ".join(stop_errors))

        _verify_stop_events([creator_events, joiner_events])

        if creator.is_running or joiner.is_running:
            raise SmokeFailure("runner thread still alive after stop")
        if not creator.wait(timeout=1.0) or not joiner.wait(timeout=1.0):
            raise SmokeFailure("runner done event was not set after stop")

        _verify_no_failures([creator_events, joiner_events], server.logs)

        cleanup_result = server.stop()
        _print_events("creator", creator_events)
        _print_events("joiner", joiner_events)
        print(f"room_id: {room_id}")
        print(f"stop_cleanup: runners_stopped, server_{cleanup_result}")
        print("relay_token_exposed: no")
        print("RESULT: PASS")
        return 0

    except Exception as exc:
        if joiner_info is not None:
            try:
                joiner.stop(joiner_info, joiner_events.emit)
            except Exception:
                pass
        try:
            creator.stop(creator_info, creator_events.emit)
        except Exception:
            pass
        cleanup_result = server.stop()
        print(f"RESULT: FAIL")
        print(f"reason: {exc}")
        _print_events("creator", creator_events)
        _print_events("joiner", joiner_events)
        _print_failure_details("creator", creator_events)
        _print_failure_details("joiner", joiner_events)
        print(f"stop_cleanup: server_{cleanup_result}")
        if server.logs:
            print("server_log_tail:")
            for line in server.logs[-20:]:
                print(line)
        return 1


def main() -> None:
    sys.exit(run_smoke())


if __name__ == "__main__":
    main()
