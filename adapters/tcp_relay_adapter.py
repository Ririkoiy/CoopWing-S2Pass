"""TCP-over-room adapter for S2Pass opaque adapter payload transport."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import sys
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile
from adapters.transport import Transport

logger = logging.getLogger(__name__)

ADAPTER_TYPE_TCP_RELAY = "tcp_relay"
FRAME_KINDS = {"open", "data", "ack", "close", "error"}
MAX_FRAME_DATA_BYTES = 700
DEFAULT_MAX_INFLIGHT_FRAMES = 32
DEFAULT_RETRANSMIT_INTERVAL = 0.3
DEFAULT_MAX_PENDING_FRAMES = 256
DEFAULT_MAX_PENDING_BYTES = 1024 * 1024
DEFAULT_MAX_RETRANSMIT_ATTEMPTS = 40


def _validate_sequence(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"tcp_relay frame has invalid {field_name}")
    return value


def encode_tcp_relay_frame(
    kind: str,
    conn_id: str,
    data: bytes = b"",
    message: str = "",
    *,
    seq: Optional[int] = None,
    ack: Optional[int] = None,
) -> bytes:
    """Encode one adapter-private TCP relay frame."""
    if kind not in FRAME_KINDS:
        raise ValueError(f"Unsupported tcp_relay frame kind: {kind!r}")
    if not isinstance(conn_id, str) or not conn_id:
        raise ValueError("tcp_relay frame has invalid conn_id")
    frame: Dict[str, Any] = {
        "adapter": ADAPTER_TYPE_TCP_RELAY,
        "kind": kind,
        "conn_id": conn_id,
    }
    if kind in {"data", "close"}:
        frame["seq"] = _validate_sequence(seq, "seq")
    if kind == "data":
        frame["data_b64"] = base64.b64encode(data).decode("ascii")
    if kind == "ack":
        frame["ack"] = _validate_sequence(ack, "ack")
    if kind == "error":
        frame["message"] = message
    return json.dumps(frame, separators=(",", ":")).encode("utf-8")


def decode_tcp_relay_frame(payload: bytes) -> Dict[str, Any]:
    """Decode and validate one adapter-private TCP relay frame."""
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid tcp_relay frame JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("tcp_relay frame must be a JSON object")
    if raw.get("adapter") != ADAPTER_TYPE_TCP_RELAY:
        raise ValueError("tcp_relay frame has wrong adapter namespace")
    kind = raw.get("kind")
    conn_id = raw.get("conn_id")
    if kind not in FRAME_KINDS:
        raise ValueError("tcp_relay frame has invalid kind")
    if not isinstance(conn_id, str) or not conn_id:
        raise ValueError("tcp_relay frame has invalid conn_id")
    seq: Optional[int] = None
    ack: Optional[int] = None
    data = b""
    if kind in {"data", "close"}:
        seq = _validate_sequence(raw.get("seq"), "seq")
    if kind == "data":
        encoded = raw.get("data_b64", "")
        if not isinstance(encoded, str):
            raise ValueError("tcp_relay data frame has invalid data_b64")
        try:
            data = base64.b64decode(encoded.encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("tcp_relay data frame has invalid base64") from exc
    if kind == "ack":
        ack = _validate_sequence(raw.get("ack"), "ack")
    message = raw.get("message", "")
    if not isinstance(message, str):
        message = str(message)
    return {
        "adapter": ADAPTER_TYPE_TCP_RELAY,
        "kind": kind,
        "conn_id": conn_id,
        "seq": seq,
        "ack": ack,
        "data": data,
        "message": message,
    }


@dataclass
class _PendingFrame:
    payload: bytes
    kind: str
    sent_at: float
    retransmit_count: int = 0


@dataclass
class _RelayConnection:
    writer: asyncio.StreamWriter
    remote_closed: asyncio.Event = field(default_factory=asyncio.Event)
    local_close_acked: asyncio.Event = field(default_factory=asyncio.Event)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    send_condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    reader_task: Optional[asyncio.Task] = None
    retransmit_task: Optional[asyncio.Task] = None
    send_seq: int = 0
    recv_next_seq: int = 0
    pending: "OrderedDict[int, _PendingFrame]" = field(default_factory=OrderedDict)
    pending_bytes: int = 0
    reorder: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    reorder_bytes: int = 0
    local_close_sent: bool = False
    local_close_seq: Optional[int] = None
    remote_close_received: bool = False
    closing: bool = False


class TcpRelayAdapter(AdapterBase):
    """Relay TCP streams through the existing S2Pass adapter payload transport."""

    def __init__(
        self,
        profile: GameProfile,
        transport: Transport,
        *,
        listen_host: Optional[str] = None,
        listen_port: Optional[int] = None,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        chunk_size: int = MAX_FRAME_DATA_BYTES,
        connection_timeout: float = 10.0,
        max_inflight_frames: int = DEFAULT_MAX_INFLIGHT_FRAMES,
        retransmit_interval: float = DEFAULT_RETRANSMIT_INTERVAL,
        max_pending_frames: int = DEFAULT_MAX_PENDING_FRAMES,
        max_pending_bytes: int = DEFAULT_MAX_PENDING_BYTES,
        max_retransmit_attempts: int = DEFAULT_MAX_RETRANSMIT_ATTEMPTS,
    ) -> None:
        super().__init__(profile)
        self.transport = transport
        self._listen_host = listen_host or profile.local_bind_host or "127.0.0.1"
        self._listen_port = (
            listen_port
            if listen_port is not None
            else (profile.local_bind_port if profile.local_bind_port is not None else 0)
        )
        self._target_host = target_host or profile.remote_target_host or "127.0.0.1"
        self._target_port = (
            target_port
            if target_port is not None
            else profile.remote_target_port
        )
        self._side = "creator" if self._target_port is not None else "joiner"
        self._chunk_size = max(1, min(int(chunk_size), MAX_FRAME_DATA_BYTES))
        self._connection_timeout = float(connection_timeout)
        self._max_inflight_frames = max(1, int(max_inflight_frames))
        self._retransmit_interval = max(0.01, float(retransmit_interval))
        self._max_pending_frames = max(
            self._max_inflight_frames,
            int(max_pending_frames),
        )
        self._max_pending_bytes = max(1, int(max_pending_bytes))
        self._max_retransmit_attempts = max(1, int(max_retransmit_attempts))

        self._lock = threading.Lock()
        self._is_running = False
        self._actual_port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._tasks: Set[asyncio.Task] = set()
        self._connections: Dict[str, _RelayConnection] = {}
        self._conn_locks: Dict[str, asyncio.Lock] = {}
        self._ready_event = threading.Event()
        self._start_error: Optional[Exception] = None
        self._stopping = False

        self.packets_from_game = 0
        self.packets_to_transport = 0
        self.packets_from_transport = 0
        self.packets_to_game = 0
        self.bytes_from_game = 0
        self.bytes_to_transport = 0
        self.bytes_from_transport = 0
        self.bytes_to_game = 0
        self.last_error: Optional[str] = None

        self.frames_data_sent = 0
        self.frames_data_received = 0
        self.frames_ack_sent = 0
        self.frames_ack_received = 0
        self.frames_retransmitted = 0
        self.frames_out_of_order = 0
        self.frames_duplicate = 0
        self.pending_frames = 0
        self.max_pending_frames_observed = 0
        self.active_connections = 0
        self.target_connect_attempts = 0
        self.target_connect_failures = 0

    def start(self) -> None:
        if self._is_running:
            return
        self._ready_event.clear()
        self._start_error = None
        self._stopping = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=10.0)
        if self._start_error is not None:
            if self._thread is not None:
                self._thread.join(timeout=5.0)
                self._thread = None
            raise self._start_error
        if not self._is_running:
            if self._thread is not None:
                self._thread.join(timeout=5.0)
                self._thread = None
            raise RuntimeError("TCP relay adapter failed to start")

    def stop(self) -> None:
        if not self._is_running:
            return
        self._stopping = True
        loop = self._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._async_stop(), loop)
            try:
                future.result(timeout=10.0)
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                logger.warning("Error stopping TCP relay adapter: %s", self.last_error)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.transport.set_receive_callback(lambda payload: None)
        self._is_running = False
        self._actual_port = None
        self._loop = None
        self._server = None

    def is_running(self) -> bool:
        return self._is_running

    def get_pid(self) -> Optional[int]:
        return None

    def get_local_addr(self) -> Tuple[Optional[str], Optional[int]]:
        if self._is_running and self._side == "joiner":
            return self._listen_host, self._actual_port
        return None, None

    def _run_loop(self) -> None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_start())
            if self._is_running:
                loop.run_forever()
        except Exception as exc:
            self._start_error = exc
            self._ready_event.set()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()

    async def _async_start(self) -> None:
        self.transport.set_receive_callback(self._on_transport_receive)
        if self._side == "joiner":
            try:
                self._server = await asyncio.start_server(
                    self._handle_local_client,
                    host=self._listen_host,
                    port=self._listen_port,
                    reuse_address=True,
                )
            except Exception as exc:
                self._start_error = RuntimeError(
                    f"Failed to bind TCP relay listener to "
                    f"{self._listen_host}:{self._listen_port}: {exc}"
                )
                self._ready_event.set()
                return
            sockets = self._server.sockets
            self._actual_port = sockets[0].getsockname()[1] if sockets else self._listen_port
        self._is_running = True
        self._ready_event.set()

    async def _async_stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for conn_id in list(self._connections):
            await self._close_connection(conn_id)
        self._conn_locks.clear()
        if self._server is not None:
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    def _on_transport_receive(self, payload: bytes) -> None:
        loop = self._loop
        if loop is None or self._stopping:
            return
        try:
            frame = decode_tcp_relay_frame(payload)
        except ValueError as exc:
            self.last_error = str(exc)
            return
        with self._lock:
            self.packets_from_transport += 1
            self.bytes_from_transport += len(payload)
        try:
            asyncio.run_coroutine_threadsafe(self._handle_frame(frame), loop)
        except RuntimeError:
            pass

    async def _handle_local_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        conn_id = uuid.uuid4().hex
        conn = self._register_connection(conn_id, writer)
        try:
            await self._send_unsequenced_frame("open", conn_id)
            while True:
                data = await reader.read(self._chunk_size)
                if not data:
                    break
                with self._lock:
                    self.packets_from_game += 1
                    self.bytes_from_game += len(data)
                if not await self._send_reliable_frame("data", conn_id, data=data):
                    return
            if not await self._send_reliable_frame("close", conn_id):
                return
            await asyncio.gather(conn.local_close_acked.wait(), conn.remote_closed.wait())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._fail_connection(conn_id, str(exc))
        finally:
            await self._close_connection(conn_id)
            if task is not None:
                self._tasks.discard(task)

    async def _handle_frame(self, frame: Dict[str, Any]) -> None:
        conn_id = frame["conn_id"]
        lock = self._conn_locks.get(conn_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conn_locks[conn_id] = lock
        async with lock:
            await self._handle_frame_locked(frame)

    async def _handle_frame_locked(self, frame: Dict[str, Any]) -> None:
        kind = frame["kind"]
        conn_id = frame["conn_id"]
        if kind == "open":
            if self._side == "creator":
                await self._ensure_target_connection(conn_id)
            return
        if kind == "ack":
            await self._handle_ack(conn_id, int(frame["ack"]))
            return
        if kind in {"data", "close"}:
            await self._handle_sequenced_frame(frame)
            return
        if kind == "error":
            await self._fail_connection(
                conn_id,
                frame.get("message") or "Remote tcp_relay error",
                notify_remote=False,
            )

    async def _handle_sequenced_frame(self, frame: Dict[str, Any]) -> None:
        conn_id = frame["conn_id"]
        if self._side == "creator":
            conn = await self._ensure_target_connection(conn_id)
        else:
            conn = self._connections.get(conn_id)
        if conn is None or conn.closing:
            return

        seq = int(frame["seq"])
        if seq < conn.recv_next_seq:
            with self._lock:
                self.frames_duplicate += 1
            await self._send_ack(conn_id, conn.recv_next_seq)
            return
        if seq > conn.recv_next_seq:
            if seq in conn.reorder:
                with self._lock:
                    self.frames_duplicate += 1
                await self._send_ack(conn_id, conn.recv_next_seq)
                return
            frame_bytes = len(frame.get("data", b""))
            if (
                len(conn.reorder) >= self._max_pending_frames
                or conn.reorder_bytes + frame_bytes > self._max_pending_bytes
            ):
                await self._fail_connection(
                    conn_id,
                    "tcp_relay reorder buffer limit exceeded",
                )
                return
            conn.reorder[seq] = frame
            conn.reorder_bytes += frame_bytes
            with self._lock:
                self.frames_out_of_order += 1
            await self._send_ack(conn_id, conn.recv_next_seq)
            return

        current = frame
        while current is not None:
            if not await self._deliver_sequenced_frame(conn_id, conn, current):
                return
            conn.recv_next_seq += 1
            current = conn.reorder.pop(conn.recv_next_seq, None)
            if current is not None:
                conn.reorder_bytes -= len(current.get("data", b""))
        await self._send_ack(conn_id, conn.recv_next_seq)

    async def _deliver_sequenced_frame(
        self,
        conn_id: str,
        conn: _RelayConnection,
        frame: Dict[str, Any],
    ) -> bool:
        kind = frame["kind"]
        if conn.remote_close_received:
            await self._fail_connection(
                conn_id,
                "tcp_relay received data after close",
            )
            return False
        if kind == "data":
            data = frame["data"]
            async with conn.write_lock:
                try:
                    conn.writer.write(data)
                    await conn.writer.drain()
                except (ConnectionError, OSError) as exc:
                    await self._fail_connection(conn_id, str(exc))
                    return False
            with self._lock:
                self.frames_data_received += 1
                self.packets_to_game += 1
                self.bytes_to_game += len(data)
            return True

        conn.remote_close_received = True
        try:
            if conn.writer.can_write_eof():
                conn.writer.write_eof()
                await conn.writer.drain()
            else:
                raw_socket = conn.writer.get_extra_info("socket")
                if raw_socket is None:
                    raise OSError("TCP socket is unavailable for half-close")
                raw_socket.shutdown(socket.SHUT_WR)
        except (ConnectionError, OSError):
            pass
        conn.remote_closed.set()
        return True

    async def _handle_ack(self, conn_id: str, ack: int) -> None:
        conn = self._connections.get(conn_id)
        if conn is None or conn.closing:
            return
        with self._lock:
            self.frames_ack_received += 1
        if ack > conn.send_seq:
            await self._fail_connection(
                conn_id,
                f"tcp_relay received invalid ack {ack} above send_seq {conn.send_seq}",
            )
            return
        async with conn.send_condition:
            removed = False
            while conn.pending:
                seq = next(iter(conn.pending))
                if seq >= ack:
                    break
                pending = conn.pending.pop(seq)
                conn.pending_bytes -= len(pending.payload)
                removed = True
            if (
                conn.local_close_seq is not None
                and ack > conn.local_close_seq
            ):
                conn.local_close_acked.set()
            if removed:
                self._update_pending_frames()
                conn.send_condition.notify_all()

    async def _send_ack(self, conn_id: str, ack: int) -> None:
        await self._send_unsequenced_frame("ack", conn_id, ack=ack)

    async def _ensure_target_connection(
        self,
        conn_id: str,
    ) -> Optional[_RelayConnection]:
        conn = self._connections.get(conn_id)
        if conn is not None:
            return conn
        if self._target_port is None:
            await self._send_unsequenced_frame(
                "error",
                conn_id,
                message="tcp_relay creator target_port is not configured",
            )
            return None
        with self._lock:
            self.target_connect_attempts += 1
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._target_host, self._target_port),
                timeout=self._connection_timeout,
            )
        except Exception as exc:
            with self._lock:
                self.target_connect_failures += 1
            self.last_error = (
                f"Target connect failed ({self._target_host}:{self._target_port}): {exc}"
            )
            await self._send_unsequenced_frame(
                "error",
                conn_id,
                message=self.last_error,
            )
            return None
        conn = self._register_connection(conn_id, writer)
        conn.reader_task = asyncio.create_task(self._read_target(conn_id, reader))
        self._tasks.add(conn.reader_task)
        return conn

    async def _read_target(
        self,
        conn_id: str,
        reader: asyncio.StreamReader,
    ) -> None:
        task = asyncio.current_task()
        try:
            while True:
                data = await reader.read(self._chunk_size)
                if not data:
                    break
                with self._lock:
                    self.packets_from_game += 1
                    self.bytes_from_game += len(data)
                if not await self._send_reliable_frame("data", conn_id, data=data):
                    return
            conn = self._connections.get(conn_id)
            if conn is None:
                return
            if not await self._send_reliable_frame("close", conn_id):
                return
            await asyncio.gather(conn.local_close_acked.wait(), conn.remote_closed.wait())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._fail_connection(conn_id, str(exc))
        finally:
            await self._close_connection(conn_id)
            if task is not None:
                self._tasks.discard(task)

    def _register_connection(
        self,
        conn_id: str,
        writer: asyncio.StreamWriter,
    ) -> _RelayConnection:
        conn = _RelayConnection(writer=writer)
        self._connections[conn_id] = conn
        with self._lock:
            self.active_connections += 1
        conn.retransmit_task = asyncio.create_task(self._retransmit_loop(conn_id))
        self._tasks.add(conn.retransmit_task)
        return conn

    async def _send_reliable_frame(
        self,
        kind: str,
        conn_id: str,
        *,
        data: bytes = b"",
    ) -> bool:
        conn = self._connections.get(conn_id)
        if conn is None or conn.closing:
            return False
        failure: Optional[str] = None
        async with conn.send_condition:
            while (
                len(conn.pending) >= self._max_inflight_frames
                and not conn.closing
                and not self._stopping
            ):
                await conn.send_condition.wait()
            if conn.closing or self._stopping:
                return False
            if conn.local_close_sent:
                failure = "tcp_relay attempted to send after close"
            else:
                seq = conn.send_seq
                payload = encode_tcp_relay_frame(kind, conn_id, data=data, seq=seq)
                if (
                    len(conn.pending) + 1 > self._max_pending_frames
                    or conn.pending_bytes + len(payload) > self._max_pending_bytes
                ):
                    failure = "tcp_relay pending frame limit exceeded"
                else:
                    conn.send_seq += 1
                    if kind == "close":
                        conn.local_close_sent = True
                        conn.local_close_seq = seq
                    conn.pending[seq] = _PendingFrame(
                        payload=payload,
                        kind=kind,
                        sent_at=time.monotonic(),
                    )
                    conn.pending_bytes += len(payload)
                    self._update_pending_frames()
        if failure is not None:
            await self._fail_connection(conn_id, failure)
            return False
        self._transport_send(payload, kind=kind, retransmit=False)
        return True

    async def _send_unsequenced_frame(
        self,
        kind: str,
        conn_id: str,
        *,
        message: str = "",
        ack: Optional[int] = None,
    ) -> None:
        payload = encode_tcp_relay_frame(
            kind,
            conn_id,
            message=message,
            ack=ack,
        )
        self._transport_send(payload, kind=kind, retransmit=False)

    def _transport_send(self, payload: bytes, *, kind: str, retransmit: bool) -> None:
        self.transport.send(payload)
        with self._lock:
            self.packets_to_transport += 1
            self.bytes_to_transport += len(payload)
            if kind == "data" and not retransmit:
                self.frames_data_sent += 1
            if kind == "ack":
                self.frames_ack_sent += 1
            if retransmit:
                self.frames_retransmitted += 1

    async def _retransmit_loop(self, conn_id: str) -> None:
        task = asyncio.current_task()
        try:
            while not self._stopping:
                await asyncio.sleep(self._retransmit_interval)
                conn = self._connections.get(conn_id)
                if conn is None or conn.closing:
                    return
                now = time.monotonic()
                due = []
                failure: Optional[str] = None
                async with conn.send_condition:
                    for seq, pending in conn.pending.items():
                        if now - pending.sent_at < self._retransmit_interval:
                            continue
                        if pending.retransmit_count >= self._max_retransmit_attempts:
                            failure = (
                                f"tcp_relay frame {seq} retransmit limit exceeded"
                            )
                            break
                        pending.retransmit_count += 1
                        pending.sent_at = now
                        due.append(pending)
                if failure is not None:
                    await self._fail_connection(conn_id, failure)
                    return
                for pending in due:
                    self._transport_send(
                        pending.payload,
                        kind=pending.kind,
                        retransmit=True,
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            await self._fail_connection(conn_id, str(exc))
        finally:
            if task is not None:
                self._tasks.discard(task)

    async def _fail_connection(
        self,
        conn_id: str,
        message: str,
        *,
        notify_remote: bool = True,
    ) -> None:
        self.last_error = message
        if notify_remote and not self._stopping:
            try:
                await self._send_unsequenced_frame("error", conn_id, message=message)
            except Exception:
                pass
        await self._close_connection(conn_id)

    async def _close_connection(self, conn_id: str) -> None:
        conn = self._connections.pop(conn_id, None)
        self._conn_locks.pop(conn_id, None)
        if conn is None:
            return
        conn.closing = True
        conn.remote_closed.set()
        conn.local_close_acked.set()
        async with conn.send_condition:
            conn.pending.clear()
            conn.pending_bytes = 0
            conn.reorder.clear()
            conn.reorder_bytes = 0
            conn.send_condition.notify_all()
        self._update_pending_frames()
        with self._lock:
            self.active_connections = max(0, self.active_connections - 1)
        current = asyncio.current_task()
        for task in (conn.reader_task, conn.retransmit_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        try:
            conn.writer.close()
            await conn.writer.wait_closed()
        except Exception:
            pass

    def _update_pending_frames(self) -> None:
        total = sum(len(conn.pending) for conn in self._connections.values())
        with self._lock:
            self.pending_frames = total
            self.max_pending_frames_observed = max(
                self.max_pending_frames_observed,
                total,
            )
