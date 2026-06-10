"""UDP raw bridge adapter with Bundle-private binary framing."""
from __future__ import annotations

import socket
import threading
from typing import Any, Dict, Optional, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile
from adapters.transport import Transport


ADAPTER_NAME = "udp_raw_bridge"
UDP_RAW_BRIDGE_FRAME_MAGIC = b"CWG_URB1\0"


def encode_udp_raw_bridge_frame(payload: bytes) -> bytes:
    """Wrap a raw UDP datagram in the adapter-private Bundle frame."""
    return UDP_RAW_BRIDGE_FRAME_MAGIC + bytes(payload)


def decode_udp_raw_bridge_frame(frame: bytes) -> bytes:
    """Unwrap one adapter-private Bundle frame into raw UDP bytes."""
    if not frame.startswith(UDP_RAW_BRIDGE_FRAME_MAGIC):
        raise ValueError("udp_raw_bridge frame has invalid magic")
    return frame[len(UDP_RAW_BRIDGE_FRAME_MAGIC):]


class UdpRawBridgeAdapter(AdapterBase):
    """Bridge local UDP datagrams through an opaque framed byte transport."""

    def __init__(
        self,
        profile: GameProfile,
        transport: Transport,
        fixed_local_target_addr: Optional[Tuple[str, int]] = None,
    ) -> None:
        super().__init__(profile)
        self.transport = transport
        self.fixed_local_target_addr = (
            fixed_local_target_addr
            if fixed_local_target_addr is not None
            else self._fixed_target_from_profile(profile)
        )
        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()

        self.local_host: Optional[str] = None
        self.local_port: Optional[int] = None
        self.last_local_game_addr: Optional[Tuple[str, int]] = None
        self.last_error: Optional[str] = None

        self.packets_from_game = 0
        self.packets_to_transport = 0
        self.packets_from_transport = 0
        self.packets_to_game = 0
        self.bytes_from_game = 0
        self.bytes_to_transport = 0
        self.bytes_from_transport = 0
        self.bytes_to_game = 0
        self.dropped_invalid_frames = 0

        self.transport.set_receive_callback(self._on_transport_receive)

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return

            bind_host = self.profile.local_bind_host or "127.0.0.1"
            bind_port = (
                self.profile.local_bind_port
                if self.profile.local_bind_port is not None
                else 0
            )
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.settimeout(0.2)

            try:
                self.sock.bind((bind_host, bind_port))
            except OSError as exc:
                self._close_socket_locked()
                raise RuntimeError(
                    f"Failed to bind UDP raw bridge socket to {bind_host}:{bind_port}: {exc}"
                ) from exc

            self.local_host, self.local_port = self.sock.getsockname()
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._is_running = True
            self.thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._is_running:
                return
            self.stop_event.set()
            self._close_socket_locked()

        if self.thread is not None:
            try:
                self.thread.join(timeout=1.0)
            finally:
                self.thread = None

        try:
            self.transport.set_receive_callback(lambda payload: None)
        except Exception:
            pass

        with self._lock:
            self.local_host = None
            self.local_port = None
            self._is_running = False

    def cleanup(self) -> None:
        self.stop()

    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def get_pid(self) -> Optional[int]:
        return None

    def get_local_addr(self) -> Tuple[Optional[str], Optional[int]]:
        with self._lock:
            return self.local_host, self.local_port

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._is_running,
                "local_host": self.local_host,
                "local_port": self.local_port,
                "fixed_local_target_addr": self.fixed_local_target_addr,
                "last_local_game_addr": self.last_local_game_addr,
                "packets_from_game": self.packets_from_game,
                "packets_to_transport": self.packets_to_transport,
                "packets_from_transport": self.packets_from_transport,
                "packets_to_game": self.packets_to_game,
                "bytes_from_game": self.bytes_from_game,
                "bytes_to_transport": self.bytes_to_transport,
                "bytes_from_transport": self.bytes_from_transport,
                "bytes_to_game": self.bytes_to_game,
                "dropped_invalid_frames": self.dropped_invalid_frames,
                "last_error": self.last_error,
            }

    def _recv_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self._lock:
                    sock = self.sock
                if sock is None:
                    break

                data, addr = sock.recvfrom(65535)
                frame = encode_udp_raw_bridge_frame(data)

                with self._lock:
                    self.last_local_game_addr = addr
                    self.packets_from_game += 1
                    self.bytes_from_game += len(data)

                self.transport.send(frame)

                with self._lock:
                    self.packets_to_transport += 1
                    self.bytes_to_transport += len(frame)
            except socket.timeout:
                continue
            except (OSError, ValueError) as exc:
                if self.stop_event.is_set():
                    break
                with self._lock:
                    self.last_error = str(exc)
                break
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                with self._lock:
                    self.last_error = str(exc)
                raise

    def _on_transport_receive(self, payload: bytes) -> None:
        try:
            raw_payload = decode_udp_raw_bridge_frame(payload)
        except ValueError as exc:
            with self._lock:
                self.dropped_invalid_frames += 1
                self.last_error = str(exc)
            return

        with self._lock:
            self.packets_from_transport += 1
            self.bytes_from_transport += len(payload)
            is_running = self._is_running
            sock = self.sock
            addr = self.last_local_game_addr or self.fixed_local_target_addr

        if not is_running or sock is None or addr is None:
            return

        try:
            sock.sendto(raw_payload, addr)
        except OSError as exc:
            with self._lock:
                self.last_error = str(exc)
            raise

        with self._lock:
            self.packets_to_game += 1
            self.bytes_to_game += len(raw_payload)

    def _close_socket_locked(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    @staticmethod
    def _fixed_target_from_profile(
        profile: GameProfile,
    ) -> Optional[Tuple[str, int]]:
        target_port = profile.remote_target_port
        target_host = profile.remote_target_host
        if target_port is None or int(target_port) <= 0:
            return None
        if not target_host:
            return None
        return target_host, int(target_port)
