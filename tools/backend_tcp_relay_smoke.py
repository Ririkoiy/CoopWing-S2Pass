#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local TCP relay adapter smoke using FakePairTransport.

Topology:
  TCP client -> joiner TcpRelayAdapter -> FakePairTransport
    -> creator TcpRelayAdapter -> local TCP echo server -> back
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from adapters.profile import GameProfile
from adapters.tcp_relay_adapter import TcpRelayAdapter
from adapters.transport import make_fake_pair

HOST = "127.0.0.1"


class SmokeFailure(Exception):
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

    def stop(self) -> None:
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


def _profile(profile_id: str, target_port: Optional[int]) -> GameProfile:
    return GameProfile(
        profile_id=profile_id,
        display_name=profile_id,
        exe_path="",
        adapter_type="tcp_relay",
        local_bind_host=HOST,
        local_bind_port=0,
        remote_target_host=HOST,
        remote_target_port=target_port,
    )


def _tcp_send_recv(host: str, port: int, payload: bytes) -> bytes:
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.settimeout(5.0)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks: List[bytes] = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def run_smoke() -> int:
    print("S2Pass TCP relay adapter smoke")
    target = EchoServer()
    creator = None
    joiner = None
    try:
        target.start()
        creator_transport, joiner_transport = make_fake_pair()
        creator = TcpRelayAdapter(
            _profile("creator", target.port),
            creator_transport,
        )
        joiner = TcpRelayAdapter(_profile("joiner", None), joiner_transport)
        creator.start()
        joiner.start()
        host, port = joiner.get_local_addr()
        if host is None or port is None or port <= 0:
            raise SmokeFailure(f"joiner listener invalid: {host}:{port}")
        payload = bytes(range(256)) * 4
        result = _tcp_send_recv(host, port, payload)
        if result != payload:
            raise SmokeFailure(
                f"payload mismatch: sent={len(payload)} received={len(result)}"
            )
        print(f"creator_target: {target.host}:{target.port}")
        print(f"joiner_listener: {host}:{port}")
        print(f"payload_bytes: {len(payload)}")
        print("RESULT: PASS")
        return 0
    except Exception as exc:
        print("RESULT: FAIL")
        print(f"reason: {exc}")
        return 1
    finally:
        if joiner is not None:
            joiner.stop()
        if creator is not None:
            creator.stop()
        target.stop()


def main() -> None:
    sys.exit(run_smoke())


if __name__ == "__main__":
    main()
