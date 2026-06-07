import base64
import json
import socket
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile
from adapters.transport import Transport


ADAPTER_NAME = "udp_broadcast_forward"
ENVELOPE_KIND = "broadcast_packet"
DEFAULT_MAX_PAYLOAD_SIZE = 1500
DEFAULT_MAX_HOP_COUNT = 1
DEFAULT_RECENT_TTL_SECONDS = 5.0
DEFAULT_RECENT_CACHE_LIMIT = 512

CORE_PROTOCOL_FIELDS = {
    "type",
    "room_id",
    "player_id",
    "relay_token",
    "relay_ip",
    "relay_port",
}

class GenericUdpBroadcastForwardAdapter(AdapterBase):
    """
    Experimental game LAN broadcast/discovery UDP forwarding adapter.

    This is adapter-local plumbing only. It wraps selected local UDP packets in
    an adapter-private envelope, sends that envelope over Transport, and sends
    remote envelopes back to a configured local target address.
    """

    def __init__(
        self,
        profile: GameProfile,
        transport: Transport,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        origin_id: Optional[str] = None,
        max_payload_size: int = DEFAULT_MAX_PAYLOAD_SIZE,
        max_hop_count: int = DEFAULT_MAX_HOP_COUNT,
        recent_ttl_seconds: float = DEFAULT_RECENT_TTL_SECONDS,
        recent_cache_limit: int = DEFAULT_RECENT_CACHE_LIMIT,
    ):
        super().__init__(profile)
        self.transport = transport
        self.target_host = target_host or profile.remote_target_host
        self.target_port = target_port if target_port is not None else profile.remote_target_port
        self.origin_id = origin_id or f"ubf_{uuid.uuid4().hex}"
        self.max_payload_size = max_payload_size
        self.max_hop_count = max_hop_count
        self.recent_ttl_seconds = recent_ttl_seconds
        self.recent_cache_limit = recent_cache_limit

        self._validate_config()

        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._is_running = False
        self._lock = threading.Lock()

        self.local_host: Optional[str] = None
        self.local_port: Optional[int] = None
        self.last_local_sender_addr: Optional[Tuple[str, int]] = None
        self.last_error: Optional[str] = None

        self.packets_from_local = 0
        self.packets_to_transport = 0
        self.packets_from_transport = 0
        self.packets_to_local = 0
        self.bytes_from_local = 0
        self.bytes_to_transport = 0
        self.bytes_from_transport = 0
        self.bytes_to_local = 0
        self.dropped_oversize_packets = 0
        self.dropped_invalid_envelopes = 0
        self.dropped_hop_limit = 0
        self.dropped_self_origin = 0
        self.dropped_recent_packet = 0
        self.dropped_local_loop = 0

        self._recent_packet_ids: Dict[str, float] = {}
        self._recent_local_payloads: Dict[Tuple[bytes, int], float] = {}

        self.transport.set_receive_callback(self._on_transport_receive)

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return

            bind_host = self.profile.local_bind_host or "127.0.0.1"
            bind_port = self.profile.local_bind_port

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.settimeout(0.2)

            try:
                self.sock.bind((bind_host, bind_port))
            except OSError as e:
                self._close_socket_locked()
                raise RuntimeError(f"Failed to bind UDP broadcast forward socket to {bind_host}:{bind_port}: {e}") from e

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

        if self.thread:
            try:
                self.thread.join(timeout=1.0)
            finally:
                self.thread = None

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
                "target_host": self.target_host,
                "target_port": self.target_port,
                "origin_id": self.origin_id,
                "packets_from_local": self.packets_from_local,
                "packets_to_transport": self.packets_to_transport,
                "packets_from_transport": self.packets_from_transport,
                "packets_to_local": self.packets_to_local,
                "bytes_from_local": self.bytes_from_local,
                "bytes_to_transport": self.bytes_to_transport,
                "bytes_from_transport": self.bytes_from_transport,
                "bytes_to_local": self.bytes_to_local,
                "dropped_oversize_packets": self.dropped_oversize_packets,
                "dropped_invalid_envelopes": self.dropped_invalid_envelopes,
                "dropped_hop_limit": self.dropped_hop_limit,
                "dropped_self_origin": self.dropped_self_origin,
                "dropped_recent_packet": self.dropped_recent_packet,
                "dropped_local_loop": self.dropped_local_loop,
                "last_local_sender_addr": self.last_local_sender_addr,
                "last_error": self.last_error,
            }

    def build_envelope(self, payload: bytes) -> bytes:
        if len(payload) > self.max_payload_size:
            raise ValueError(f"UDP broadcast payload exceeds limit: {len(payload)} > {self.max_payload_size}")

        packet_id = f"ubfp_{uuid.uuid4().hex}"
        envelope = {
            "adapter": ADAPTER_NAME,
            "kind": ENVELOPE_KIND,
            "origin_id": self.origin_id,
            "packet_id": packet_id,
            "hop_count": 1,
            "target_port": self.target_port,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        }
        return json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _validate_config(self) -> None:
        bind_port = self.profile.local_bind_port
        if bind_port is None or not (1 <= int(bind_port) <= 65535):
            raise ValueError("local_bind_port must be explicitly configured between 1 and 65535")
        if not self.target_host:
            raise ValueError("target_host or profile.remote_target_host is required")
        if self.target_port is None or not (1 <= int(self.target_port) <= 65535):
            raise ValueError("target_port or profile.remote_target_port must be explicitly configured between 1 and 65535")
        if self.max_payload_size <= 0 or self.max_payload_size > DEFAULT_MAX_PAYLOAD_SIZE:
            raise ValueError(f"max_payload_size must be between 1 and {DEFAULT_MAX_PAYLOAD_SIZE}")
        if self.max_hop_count < 1:
            raise ValueError("max_hop_count must be at least 1")
        if self.recent_ttl_seconds <= 0:
            raise ValueError("recent_ttl_seconds must be positive")
        if self.recent_cache_limit < 1:
            raise ValueError("recent_cache_limit must be at least 1")

    def _close_socket_locked(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _recv_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self._lock:
                    sock = self.sock
                if sock is None:
                    break

                data, addr = sock.recvfrom(65535)

                with self._lock:
                    self._expire_recent_locked()
                    if self._consume_recent_local_payload_locked(data):
                        self.dropped_local_loop += 1
                        continue
                    if len(data) > self.max_payload_size:
                        self.dropped_oversize_packets += 1
                        continue
                    self.last_local_sender_addr = addr
                    self.packets_from_local += 1
                    self.bytes_from_local += len(data)

                envelope = self.build_envelope(data)
                self.transport.send(envelope)

                with self._lock:
                    self.packets_to_transport += 1
                    self.bytes_to_transport += len(envelope)
            except socket.timeout:
                continue
            except (OSError, ValueError) as e:
                if self.stop_event.is_set():
                    break
                with self._lock:
                    self.last_error = str(e)
                break
            except Exception as e:
                if self.stop_event.is_set():
                    break
                with self._lock:
                    self.last_error = str(e)
                raise

    def _on_transport_receive(self, payload: bytes) -> None:
        envelope = self._decode_envelope(payload)
        if envelope is None:
            return

        packet_id = envelope["packet_id"]
        origin_id = envelope["origin_id"]
        hop_count = envelope["hop_count"]
        raw_payload = envelope["payload"]

        with self._lock:
            self._expire_recent_locked()
            self.packets_from_transport += 1
            self.bytes_from_transport += len(payload)

            if origin_id == self.origin_id:
                self.dropped_self_origin += 1
                return
            if hop_count > self.max_hop_count:
                self.dropped_hop_limit += 1
                return
            if packet_id in self._recent_packet_ids:
                self.dropped_recent_packet += 1
                return

            self._remember_packet_id_locked(packet_id)
            self._remember_local_payload_locked(raw_payload)
            is_running = self._is_running
            sock = self.sock
            target = (self.target_host, int(self.target_port))

        if not is_running or sock is None:
            return

        try:
            sock.sendto(raw_payload, target)
        except OSError as e:
            with self._lock:
                self.last_error = str(e)
            raise

        with self._lock:
            self.packets_to_local += 1
            self.bytes_to_local += len(raw_payload)

    def _decode_envelope(self, payload: bytes) -> Optional[Dict[str, Any]]:
        try:
            envelope = json.loads(payload.decode("utf-8"))
            if not isinstance(envelope, dict):
                raise ValueError("envelope must be an object")
            if CORE_PROTOCOL_FIELDS.intersection(envelope.keys()):
                raise ValueError("core protocol field is not allowed in adapter envelope")
            if envelope.get("adapter") != ADAPTER_NAME:
                raise ValueError("unexpected adapter name")
            if envelope.get("kind") != ENVELOPE_KIND:
                raise ValueError("unexpected envelope kind")

            origin_id = envelope.get("origin_id")
            packet_id = envelope.get("packet_id")
            hop_count = envelope.get("hop_count")
            target_port = envelope.get("target_port")
            payload_b64 = envelope.get("payload_b64")

            if not isinstance(origin_id, str) or not origin_id:
                raise ValueError("origin_id is required")
            if not isinstance(packet_id, str) or not packet_id:
                raise ValueError("packet_id is required")
            if not isinstance(hop_count, int):
                raise ValueError("hop_count must be an integer")
            if not isinstance(target_port, int) or target_port != int(self.target_port):
                raise ValueError("target_port must match configured target_port")
            if not isinstance(payload_b64, str):
                raise ValueError("payload_b64 is required")
            raw_payload = base64.b64decode(payload_b64.encode("ascii"), validate=True)
            if len(raw_payload) > self.max_payload_size:
                raise ValueError("payload exceeds configured max_payload_size")

            return {
                "origin_id": origin_id,
                "packet_id": packet_id,
                "hop_count": hop_count,
                "payload": raw_payload,
            }
        except Exception:
            with self._lock:
                self.dropped_invalid_envelopes += 1
            return None

    def _expire_recent_locked(self) -> None:
        now = time.monotonic()
        expired_ids = [packet_id for packet_id, expires_at in self._recent_packet_ids.items() if expires_at <= now]
        for packet_id in expired_ids:
            self._recent_packet_ids.pop(packet_id, None)

        expired_payloads = [key for key, expires_at in self._recent_local_payloads.items() if expires_at <= now]
        for key in expired_payloads:
            self._recent_local_payloads.pop(key, None)

    def _remember_packet_id_locked(self, packet_id: str) -> None:
        self._recent_packet_ids[packet_id] = time.monotonic() + self.recent_ttl_seconds
        while len(self._recent_packet_ids) > self.recent_cache_limit:
            oldest = min(self._recent_packet_ids, key=self._recent_packet_ids.get)
            self._recent_packet_ids.pop(oldest, None)

    def _remember_local_payload_locked(self, payload: bytes) -> None:
        key = (payload, int(self.target_port))
        self._recent_local_payloads[key] = time.monotonic() + self.recent_ttl_seconds
        while len(self._recent_local_payloads) > self.recent_cache_limit:
            oldest = min(self._recent_local_payloads, key=self._recent_local_payloads.get)
            self._recent_local_payloads.pop(oldest, None)

    def _consume_recent_local_payload_locked(self, payload: bytes) -> bool:
        key = (payload, int(self.local_port or 0))
        expires_at = self._recent_local_payloads.get(key)
        if expires_at is None:
            return False
        if expires_at <= time.monotonic():
            self._recent_local_payloads.pop(key, None)
            return False
        self._recent_local_payloads.pop(key, None)
        return True
