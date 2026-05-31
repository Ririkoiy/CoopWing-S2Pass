# -*- coding: utf-8 -*-
"""S2Pass Backend — models for session management."""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, List, Optional


ADAPTER_TYPE_LOCAL_UDP_BRIDGE = "local_udp_bridge"
ADAPTER_STATUS_DISABLED = "disabled"
ADAPTER_STATUS_INITIALIZING = "initializing"
ADAPTER_STATUS_READY = "ready"
ADAPTER_STATUS_ERROR = "error"
ADAPTER_STATUS_STOPPED = "stopped"
ADAPTER_STATUSES = {
    ADAPTER_STATUS_DISABLED,
    ADAPTER_STATUS_INITIALIZING,
    ADAPTER_STATUS_READY,
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_STOPPED,
}


@dataclasses.dataclass
class SessionEvent:
    """A single lifecycle or log event for a session."""
    type: str
    message: str
    timestamp: float = dataclasses.field(default_factory=time.time)
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "message": self.message,
            "timestamp": self.timestamp,
        }
        if self.data is not None:
            d["data"] = self.data
        else:
            d["data"] = {}
        return d


@dataclasses.dataclass
class AdapterConfig:
    """Passive backend config for a future local UDP bridge adapter."""
    enabled: bool = False
    adapter_type: str = ADAPTER_TYPE_LOCAL_UDP_BRIDGE
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    target_host: str = "127.0.0.1"
    # P5.0B follows the P4.7C status examples: 40200 is the default local
    # game target port, while bind_port=0 remains OS-assigned for future bind.
    target_port: int = 40200

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AdapterConfig":
        if not isinstance(raw, dict):
            raise BackendError(
                code="INVALID_REQUEST",
                message="Field adapter_config must be a JSON object",
                details={"field": "adapter_config"},
            )

        enabled = _adapter_bool(raw, "enabled", cls.enabled)
        adapter_type = _adapter_str(raw, "adapter_type", cls.adapter_type)
        if adapter_type != ADAPTER_TYPE_LOCAL_UDP_BRIDGE:
            raise BackendError(
                code="INVALID_REQUEST",
                message=f"Unsupported adapter_config.adapter_type: {adapter_type}",
                details={
                    "field": "adapter_config.adapter_type",
                    "value": adapter_type,
                    "expected": ADAPTER_TYPE_LOCAL_UDP_BRIDGE,
                },
            )

        return cls(
            enabled=enabled,
            adapter_type=adapter_type,
            bind_host=_adapter_str(raw, "bind_host", cls.bind_host),
            bind_port=_adapter_port(raw, "bind_port", cls.bind_port),
            target_host=_adapter_str(raw, "target_host", cls.target_host),
            target_port=_adapter_port(raw, "target_port", cls.target_port),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "adapter_type": self.adapter_type,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "target_host": self.target_host,
            "target_port": self.target_port,
        }


@dataclasses.dataclass
class AdapterCounters:
    packets_from_game: int = 0
    packets_to_transport: int = 0
    packets_from_transport: int = 0
    packets_to_game: int = 0
    bytes_from_game: int = 0
    bytes_to_transport: int = 0
    bytes_from_transport: int = 0
    bytes_to_game: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "packets_from_game": self.packets_from_game,
            "packets_to_transport": self.packets_to_transport,
            "packets_from_transport": self.packets_from_transport,
            "packets_to_game": self.packets_to_game,
            "bytes_from_game": self.bytes_from_game,
            "bytes_to_transport": self.bytes_to_transport,
            "bytes_from_transport": self.bytes_from_transport,
            "bytes_to_game": self.bytes_to_game,
        }


@dataclasses.dataclass
class AdapterStatus:
    enabled: bool = False
    status: str = ADAPTER_STATUS_DISABLED
    adapter_type: str = ADAPTER_TYPE_LOCAL_UDP_BRIDGE
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: int = 40200
    counters: AdapterCounters = dataclasses.field(default_factory=AdapterCounters)
    error: Optional[Dict[str, str]] = None

    @classmethod
    def disabled(cls) -> "AdapterStatus":
        return cls(enabled=False, status=ADAPTER_STATUS_DISABLED)

    @classmethod
    def from_config(
        cls,
        config: AdapterConfig,
        status: str = ADAPTER_STATUS_STOPPED,
        error: Optional[Dict[str, str]] = None,
    ) -> "AdapterStatus":
        return cls(
            enabled=config.enabled,
            status=status if config.enabled else ADAPTER_STATUS_DISABLED,
            adapter_type=config.adapter_type,
            bind_host=config.bind_host,
            bind_port=config.bind_port,
            target_host=config.target_host,
            target_port=config.target_port,
            error=error,
        )

    def __post_init__(self) -> None:
        if self.status not in ADAPTER_STATUSES:
            raise ValueError(f"Invalid adapter status: {self.status}")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "enabled": self.enabled,
            "status": self.status,
        }
        if self.enabled:
            d.update({
                "adapter_type": self.adapter_type,
                "bind_host": self.bind_host,
                "bind_port": self.bind_port,
                "target_host": self.target_host,
                "target_port": self.target_port,
                "counters": self.counters.to_dict(),
                "error": self.error,
            })
        return d


@dataclasses.dataclass
class BackendError(Exception):
    """Stable backend error passed to HTTP layer."""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            d["details"] = self.details
        return d


@dataclasses.dataclass
class SessionStats:
    packets_from_game: int = 0
    packets_to_transport: int = 0
    packets_from_transport: int = 0
    packets_to_game: int = 0
    has_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packets_from_game": self.packets_from_game,
            "packets_to_transport": self.packets_to_transport,
            "packets_from_transport": self.packets_from_transport,
            "packets_to_game": self.packets_to_game,
            "has_error": self.has_error,
        }


@dataclasses.dataclass
class SessionInfo:
    session_id: str
    role: str                       # "create" or "join"
    status: str
    room_id: Optional[str] = None
    player_name: str = ""
    server_host: str = ""
    server_port: int = 0
    server_udp_port: int = 0
    adapter_host: str = "127.0.0.1"
    adapter_port: int = 0
    game_server_host: str = "127.0.0.1"
    game_server_port: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    error: Optional[Dict[str, Any]] = None
    stats: Optional[SessionStats] = None
    adapter_config: Optional[AdapterConfig] = None
    adapter_status: Optional[AdapterStatus] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "session_id": self.session_id,
            "role": self.role,
            "status": self.status,
            "room_id": self.room_id,
            "player_name": self.player_name,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "server_udp_port": self.server_udp_port,
            "adapter_host": self.adapter_host,
            "adapter_port": self.adapter_port,
            "game_server_host": self.game_server_host,
            "game_server_port": self.game_server_port,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.error is not None:
            d["error"] = self.error
        if self.stats is not None:
            d["stats"] = self.stats.to_dict()
        if self.adapter_status is not None:
            d["adapter_status"] = self.adapter_status.to_dict()
        return d


def _adapter_bool(raw: Dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field adapter_config.{key} must be a boolean",
            details={"field": f"adapter_config.{key}", "value": str(value)},
        )
    return value


def _adapter_str(raw: Dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field adapter_config.{key} must be a non-empty string",
            details={"field": f"adapter_config.{key}"},
        )
    return value.strip()


def _adapter_port(raw: Dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if type(value) is not int:
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field adapter_config.{key} must be an integer",
            details={"field": f"adapter_config.{key}", "value": str(value)},
        )
    if value < 0 or value > 65535:
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field adapter_config.{key} must be in range 0-65535, got {value}",
            details={"field": f"adapter_config.{key}", "value": value},
        )
    return value
