# -*- coding: utf-8 -*-
"""Co-opWinG v0.4 LAN Discovery — pure stdlib UDP broadcast peer discovery.

This module is independent of the S2Pass protocol. It does NOT construct or
consume protocol_lock.md message types, fields, or error codes.
"""
from __future__ import annotations

import json
import secrets
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

_MAX_PAYLOAD_BYTES = 1500
_DEFAULT_BROADCAST_PORT = 21521
_DEFAULT_ANNOUNCE_INTERVAL = 5.0   # seconds
_DEFAULT_PEER_TIMEOUT = 30.0        # seconds

# peer_id uses "ld_" prefix (distinct from protocol "p_<hex12>" player_id)
_PEER_ID_PREFIX = "ld_"
_PEER_ID_BYTES = 6  # 12 hex chars


def _generate_peer_id() -> str:
    return f"{_PEER_ID_PREFIX}{secrets.token_hex(_PEER_ID_BYTES)}"


def _now() -> float:
    return time.monotonic()


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------


@dataclass
class LanPeer:
    """A discovered LAN peer."""
    peer_id: str
    name: str
    host: str
    port: int
    version: str
    last_seen: float = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "version": self.version,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], host: str) -> "LanPeer":
        peer_id = str(raw.get("peer_id", ""))
        name = str(raw.get("name", ""))
        port = _safe_int(raw.get("port"), 0)
        version = str(raw.get("version", ""))
        return cls(
            peer_id=peer_id,
            name=name,
            host=host,
            port=port,
            version=version,
        )


@dataclass
class LanDiscoveryConfig:
    """Configuration for a LAN discovery instance."""
    service_port: int = 21520
    broadcast_port: int = _DEFAULT_BROADCAST_PORT
    announce_interval_seconds: float = _DEFAULT_ANNOUNCE_INTERVAL
    peer_timeout_seconds: float = _DEFAULT_PEER_TIMEOUT
    product_name: str = "Co-opWinG"
    version: str = "0.4.0"
    instance_name: str = ""


# ---------------------------------------------------------------------------
# core discovery engine
# ---------------------------------------------------------------------------


class LanDiscovery:
    """Broadcast-based LAN peer discovery.

    Usage::

        disco = LanDiscovery(LanDiscoveryConfig(instance_name="MyPC"))
        disco.start()
        # ... later ...
        peers = disco.get_peers()
        disco.stop()
    """

    def __init__(self, config: LanDiscoveryConfig) -> None:
        self._config = config
        self._peer_id = _generate_peer_id()
        self._peers: Dict[str, LanPeer] = {}
        self._lock = threading.Lock()
        self._running = False
        self._announce_sock: Optional[socket.socket] = None
        self._listen_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._last_announce = 0.0

    # -- public API -------------------------------------------------------

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def start(self) -> None:
        """Start announcing and listening. Idempotent."""
        if self._running:
            return
        try:
            self._setup_sockets()
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                            name="lan-discovery")
            self._thread.start()
        except Exception:
            self._running = False
            self._close_sockets()
            self._thread = None
            raise

    def stop(self) -> None:
        """Stop announcing and listening. Repeated calls are safe."""
        self._running = False
        self._close_sockets()

        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        with self._lock:
            self._peers.clear()

    def get_peers(self) -> List[LanPeer]:
        """Return a snapshot of currently-known peers (excluding self).
        Peer timeout is applied lazily here — stale entries are removed.
        """
        threshold = _now() - self._config.peer_timeout_seconds
        with self._lock:
            stale = [
                pid for pid, p in self._peers.items()
                if p.last_seen < threshold
            ]
            for pid in stale:
                del self._peers[pid]
            return sorted(
                self._peers.values(),
                key=lambda p: p.name.lower(),
            )

    # -- internals --------------------------------------------------------

    def _setup_sockets(self) -> None:
        self._announce_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._announce_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._announce_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listen_sock.bind(("0.0.0.0", self._config.broadcast_port))
        self._listen_sock.settimeout(None)

    def _close_sockets(self) -> None:
        for attr in ("_announce_sock", "_listen_sock"):
            s = getattr(self, attr, None)
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
                setattr(self, attr, None)

    def _run_loop(self) -> None:
        listen = self._listen_sock
        if listen is None:
            return
        announce_interval = max(0.5, self._config.announce_interval_seconds)
        check_timeout = 1.0

        while self._running:
            try:
                readable, _, _ = select.select([listen], [], [], check_timeout)
            except (OSError, ValueError):
                break

            if readable:
                try:
                    data, addr = listen.recvfrom(_MAX_PAYLOAD_BYTES + 1024)
                except OSError:
                    continue
                self._handle_packet(data, addr[0])

            now_ = _now()
            if now_ - self._last_announce >= announce_interval:
                self._send_announce()
                self._last_announce = now_

    def _send_announce(self) -> None:
        sock = self._announce_sock
        if sock is None:
            return
        payload = self._build_announce_payload()
        try:
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception:
            return
        if len(data) > _MAX_PAYLOAD_BYTES:
            return
        try:
            sock.sendto(data, ("255.255.255.255", self._config.broadcast_port))
        except OSError:
            pass

    def _build_announce_payload(self) -> Dict[str, Any]:
        return {
            "peer_id": self._peer_id,
            "name": self._config.instance_name,
            "port": self._config.service_port,
            "version": self._config.version,
            "product": self._config.product_name,
        }

    def _handle_packet(self, data: bytes, source_host: str) -> None:
        if len(data) > _MAX_PAYLOAD_BYTES:
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return
        line = text.split("\n")[0].strip()
        if not line:
            return
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(raw, dict):
            return
        peer_id = raw.get("peer_id", "")
        if not isinstance(peer_id, str) or not peer_id:
            return
        if peer_id == self._peer_id:
            return

        if raw.get("product", "") != self._config.product_name:
            return

        peer = LanPeer.from_dict(raw, source_host)
        if peer.port < 1 or peer.port > 65535:
            return

        with self._lock:
            existing = self._peers.get(peer_id)
            if existing is not None:
                existing.name = peer.name or existing.name
                existing.host = peer.host
                existing.port = peer.port
                existing.version = peer.version or existing.version
                existing.last_seen = _now()
            else:
                peer.last_seen = _now()
                self._peers[peer_id] = peer


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return default
