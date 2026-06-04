# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from typing import Callable, List, Optional

from adapters.profile import GameProfile
from adapters.tcp_relay_adapter import (
    TcpRelayAdapter,
    decode_tcp_relay_frame,
    encode_tcp_relay_frame,
)
from adapters.transport import Transport, make_fake_pair


class _TcpServer:
    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self.host = "127.0.0.1"
        self.port = 0
        self._handler = handler
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
            thread = threading.Thread(
                target=self._handle_client,
                args=(conn,),
                daemon=True,
            )
            self._client_threads.append(thread)
            thread.start()

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            self._handler(conn)


class _HookTransport(Transport):
    def __init__(
        self,
        hook: Optional[Callable[[bytes, Callable[[bytes], None]], None]] = None,
    ) -> None:
        self.peer: Optional["_HookTransport"] = None
        self.callback: Optional[Callable[[bytes], None]] = None
        self.hook = hook
        self._lock = threading.RLock()

    def set_peer(self, peer: "_HookTransport") -> None:
        self.peer = peer

    def send(self, payload: bytes) -> None:
        peer = self.peer
        if peer is None:
            raise RuntimeError("peer is not set")
        with peer._lock:
            callback = peer.callback
        if callback is None:
            return
        if self.hook is None:
            callback(payload)
        else:
            self.hook(payload, callback)

    def set_receive_callback(self, callback: Callable[[bytes], None]) -> None:
        with self._lock:
            self.callback = callback


def _make_hook_pair(
    *,
    creator_hook: Optional[Callable[[bytes, Callable[[bytes], None]], None]] = None,
    joiner_hook: Optional[Callable[[bytes, Callable[[bytes], None]], None]] = None,
) -> tuple[_HookTransport, _HookTransport]:
    creator = _HookTransport(creator_hook)
    joiner = _HookTransport(joiner_hook)
    creator.set_peer(joiner)
    joiner.set_peer(creator)
    return creator, joiner


def _drop_first_data_seq(seq: int) -> Callable[[bytes, Callable[[bytes], None]], None]:
    dropped = False
    lock = threading.Lock()

    def hook(payload: bytes, deliver: Callable[[bytes], None]) -> None:
        nonlocal dropped
        frame = decode_tcp_relay_frame(payload)
        with lock:
            if frame["kind"] == "data" and frame["seq"] == seq and not dropped:
                dropped = True
                return
        deliver(payload)

    return hook


def _drop_first_kind(kind: str) -> Callable[[bytes, Callable[[bytes], None]], None]:
    dropped = False
    lock = threading.Lock()

    def hook(payload: bytes, deliver: Callable[[bytes], None]) -> None:
        nonlocal dropped
        frame = decode_tcp_relay_frame(payload)
        with lock:
            if frame["kind"] == kind and not dropped:
                dropped = True
                return
        deliver(payload)

    return hook


def _reorder_data_seq(
    first_seq: int,
    second_seq: int,
) -> Callable[[bytes, Callable[[bytes], None]], None]:
    held: Optional[bytes] = None
    lock = threading.Lock()

    def hook(payload: bytes, deliver: Callable[[bytes], None]) -> None:
        nonlocal held
        frame = decode_tcp_relay_frame(payload)
        to_deliver: List[bytes] = []
        with lock:
            if frame["kind"] == "data" and frame["seq"] == first_seq and held is None:
                held = payload
                return
            to_deliver.append(payload)
            if frame["kind"] == "data" and frame["seq"] == second_seq and held is not None:
                to_deliver.append(held)
                held = None
        for item in to_deliver:
            deliver(item)

    return hook


def _duplicate_first_data() -> Callable[[bytes, Callable[[bytes], None]], None]:
    duplicated = False
    lock = threading.Lock()

    def hook(payload: bytes, deliver: Callable[[bytes], None]) -> None:
        nonlocal duplicated
        frame = decode_tcp_relay_frame(payload)
        deliver(payload)
        with lock:
            if frame["kind"] == "data" and not duplicated:
                duplicated = True
                deliver(payload)

    return hook


def _drop_all_acks(payload: bytes, deliver: Callable[[bytes], None]) -> None:
    if decode_tcp_relay_frame(payload)["kind"] != "ack":
        deliver(payload)


def _echo_handler(conn: socket.socket) -> None:
    while True:
        data = conn.recv(4096)
        if not data:
            break
        conn.sendall(data)


def _half_close_handler(conn: socket.socket) -> None:
    chunks = []
    while True:
        data = conn.recv(4096)
        if not data:
            break
        chunks.append(data)
    conn.sendall(b"response:" + b"".join(chunks))


def _profile(
    profile_id: str,
    *,
    bind_port: Optional[int] = 0,
    target_port: Optional[int] = None,
) -> GameProfile:
    return GameProfile(
        profile_id=profile_id,
        display_name=profile_id,
        exe_path="",
        adapter_type="tcp_relay",
        local_bind_host="127.0.0.1",
        local_bind_port=bind_port,
        remote_target_host="127.0.0.1",
        remote_target_port=target_port,
    )


def _tcp_send_recv(host: str, port: int, payload: bytes, timeout: float = 15.0) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


class TcpRelayFrameTests(unittest.TestCase):
    def test_data_frame_seq_round_trip_with_binary_payload(self) -> None:
        payload = bytes(range(256))

        decoded = decode_tcp_relay_frame(
            encode_tcp_relay_frame("data", "c1", payload, seq=7)
        )

        self.assertEqual(decoded["adapter"], "tcp_relay")
        self.assertEqual(decoded["kind"], "data")
        self.assertEqual(decoded["conn_id"], "c1")
        self.assertEqual(decoded["seq"], 7)
        self.assertEqual(decoded["data"], payload)

    def test_ack_frame_round_trip(self) -> None:
        decoded = decode_tcp_relay_frame(
            encode_tcp_relay_frame("ack", "c2", ack=11)
        )

        self.assertEqual(decoded["kind"], "ack")
        self.assertEqual(decoded["ack"], 11)

    def test_control_frame_kinds_round_trip(self) -> None:
        self.assertEqual(
            decode_tcp_relay_frame(encode_tcp_relay_frame("open", "c2"))["kind"],
            "open",
        )
        close = decode_tcp_relay_frame(
            encode_tcp_relay_frame("close", "c2", seq=4)
        )
        self.assertEqual(close["kind"], "close")
        self.assertEqual(close["seq"], 4)
        decoded = decode_tcp_relay_frame(
            encode_tcp_relay_frame("error", "c2", message="boom")
        )
        self.assertEqual(decoded["kind"], "error")
        self.assertEqual(decoded["message"], "boom")

    def test_invalid_seq_ack_and_base64_are_rejected(self) -> None:
        invalid_frames = [
            {"adapter": "tcp_relay", "kind": "data", "conn_id": "c1", "data_b64": ""},
            {"adapter": "tcp_relay", "kind": "data", "conn_id": "c1", "seq": -1, "data_b64": ""},
            {"adapter": "tcp_relay", "kind": "ack", "conn_id": "c1", "ack": "1"},
            {"adapter": "tcp_relay", "kind": "ack", "conn_id": "c1", "ack": True},
            {"adapter": "tcp_relay", "kind": "data", "conn_id": "c1", "seq": 0, "data_b64": "***"},
        ]

        for frame in invalid_frames:
            with self.subTest(frame=frame):
                with self.assertRaises(ValueError):
                    decode_tcp_relay_frame(json.dumps(frame).encode("utf-8"))


class TcpRelayAdapterTests(unittest.TestCase):
    def _run_pair(
        self,
        payload: bytes,
        *,
        target_handler: Callable[[socket.socket], None] = _echo_handler,
        creator_transport: Optional[Transport] = None,
        joiner_transport: Optional[Transport] = None,
        retransmit_interval: float = 0.05,
    ) -> tuple[bytes, TcpRelayAdapter, TcpRelayAdapter]:
        target = _TcpServer(target_handler)
        target.start()
        if creator_transport is None or joiner_transport is None:
            creator_transport, joiner_transport = make_fake_pair()
        creator = TcpRelayAdapter(
            _profile("creator", target_port=target.port),
            creator_transport,
            retransmit_interval=retransmit_interval,
        )
        joiner = TcpRelayAdapter(
            _profile("joiner"),
            joiner_transport,
            retransmit_interval=retransmit_interval,
        )
        try:
            creator.start()
            joiner.start()
            host, port = joiner.get_local_addr()
            result = _tcp_send_recv(str(host), int(port), payload)
            return result, creator, joiner
        finally:
            joiner.stop()
            creator.stop()
            target.stop()

    def test_single_connection_loopback_with_fake_transport(self) -> None:
        payload = bytes(range(256)) * 4

        result, creator, joiner = self._run_pair(payload)

        self.assertEqual(result, payload)
        self.assertGreater(joiner.packets_to_transport, 0)
        self.assertGreater(creator.packets_from_transport, 0)
        self.assertEqual(joiner.pending_frames, 0)
        self.assertEqual(creator.pending_frames, 0)

    def test_reorder_buffers_until_missing_frame_arrives(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            joiner_hook=_reorder_data_seq(0, 1),
        )
        payload = bytes(range(256)) * 8

        result, creator, _ = self._run_pair(
            payload,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, payload)
        self.assertGreaterEqual(creator.frames_out_of_order, 1)

    def test_duplicate_frame_is_not_written_twice(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            joiner_hook=_duplicate_first_data(),
        )
        payload = bytes(range(256)) * 4

        result, creator, _ = self._run_pair(
            payload,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, payload)
        self.assertGreaterEqual(creator.frames_duplicate, 1)

    def test_lost_data_frame_is_retransmitted(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            joiner_hook=_drop_first_data_seq(1),
        )
        payload = bytes(range(256)) * 8

        result, _, joiner = self._run_pair(
            payload,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, payload)
        self.assertGreaterEqual(joiner.frames_retransmitted, 1)

    def test_lost_ack_is_recovered_by_later_cumulative_ack(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            creator_hook=_drop_first_kind("ack"),
        )
        payload = bytes(range(256)) * 4

        result, _, joiner = self._run_pair(
            payload,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, payload)
        self.assertGreaterEqual(joiner.frames_ack_received, 1)
        self.assertEqual(joiner.pending_frames, 0)

    def test_lost_close_frame_is_retransmitted(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            joiner_hook=_drop_first_kind("close"),
        )
        payload = b"close-retransmit"

        result, _, joiner = self._run_pair(
            payload,
            target_handler=_half_close_handler,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, b"response:" + payload)
        self.assertGreaterEqual(joiner.frames_retransmitted, 1)

    def test_half_close_waits_for_missing_tail_before_target_response(self) -> None:
        creator_transport, joiner_transport = _make_hook_pair(
            joiner_hook=_drop_first_data_seq(1),
        )
        payload = bytes(range(256)) * 8

        result, _, joiner = self._run_pair(
            payload,
            target_handler=_half_close_handler,
            creator_transport=creator_transport,
            joiner_transport=joiner_transport,
        )

        self.assertEqual(result, b"response:" + payload)
        self.assertGreaterEqual(joiner.frames_retransmitted, 1)

    def test_three_concurrent_connections_remain_isolated(self) -> None:
        target = _TcpServer(_echo_handler)
        target.start()
        creator_transport, joiner_transport = make_fake_pair()
        creator = TcpRelayAdapter(
            _profile("creator", target_port=target.port),
            creator_transport,
            retransmit_interval=0.05,
        )
        joiner = TcpRelayAdapter(
            _profile("joiner"),
            joiner_transport,
            retransmit_interval=0.05,
        )
        results: List[Optional[bytes]] = [None, None, None]
        payloads = [
            (f"connection-{index}:".encode("ascii") + bytes([index]) * 8192)
            for index in range(3)
        ]
        try:
            creator.start()
            joiner.start()
            host, port = joiner.get_local_addr()

            def exchange(index: int) -> None:
                results[index] = _tcp_send_recv(str(host), int(port), payloads[index])

            threads = [
                threading.Thread(target=exchange, args=(index,))
                for index in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20.0)
                self.assertFalse(thread.is_alive())

            self.assertEqual(results, payloads)
            self.assertGreaterEqual(joiner.max_pending_frames_observed, 1)
        finally:
            joiner.stop()
            creator.stop()
            target.stop()

    def test_large_payloads_exact_echo(self) -> None:
        for size in (16 * 1024, 64 * 1024):
            with self.subTest(size=size):
                payload = bytes(index % 256 for index in range(size))
                result, _, _ = self._run_pair(payload)
                self.assertEqual(result, payload)

    def test_stop_clears_pending_buffers_and_tasks(self) -> None:
        target = _TcpServer(_echo_handler)
        target.start()
        creator_transport, joiner_transport = _make_hook_pair(
            creator_hook=_drop_all_acks,
        )
        creator = TcpRelayAdapter(
            _profile("creator", target_port=target.port),
            creator_transport,
            retransmit_interval=0.05,
        )
        joiner = TcpRelayAdapter(
            _profile("joiner"),
            joiner_transport,
            retransmit_interval=0.05,
        )
        sock: Optional[socket.socket] = None
        try:
            creator.start()
            joiner.start()
            host, port = joiner.get_local_addr()
            sock = socket.create_connection((str(host), int(port)), timeout=5.0)
            sock.sendall(b"pending")
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and joiner.pending_frames == 0:
                time.sleep(0.01)
            self.assertGreater(joiner.pending_frames, 0)

            joiner.stop()
            creator.stop()

            self.assertEqual(joiner.pending_frames, 0)
            self.assertEqual(creator.pending_frames, 0)
            self.assertEqual(joiner.active_connections, 0)
            self.assertEqual(creator.active_connections, 0)
            self.assertIsNone(joiner.last_error)
        finally:
            if sock is not None:
                sock.close()
            joiner.stop()
            creator.stop()
            target.stop()

    def test_stop_releases_listener_and_keeps_normal_stop_clean(self) -> None:
        creator_transport, joiner_transport = make_fake_pair()
        creator = TcpRelayAdapter(_profile("creator", target_port=9), creator_transport)
        joiner = TcpRelayAdapter(_profile("joiner"), joiner_transport)
        creator.start()
        joiner.start()
        host, port = joiner.get_local_addr()

        joiner.stop()
        creator.stop()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((str(host), int(port)), timeout=0.1):
                    time.sleep(0.05)
                    continue
            except OSError:
                break
        with self.assertRaises(OSError):
            socket.create_connection((str(host), int(port)), timeout=0.1)
        self.assertIsNone(joiner.last_error)


if __name__ == "__main__":
    unittest.main()
