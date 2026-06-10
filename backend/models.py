# -*- coding: utf-8 -*-
"""S2Pass Backend — models for session management."""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, List, Optional

from secondary_ip_manager import SecondaryIpRequest


ADAPTER_TYPE_LOCAL_UDP_BRIDGE = "local_udp_bridge"
ADAPTER_TYPE_TCP_FORWARD = "tcp_forward"
ADAPTER_TYPE_TCP_RELAY = "tcp_relay"
ADAPTER_TYPE_BUNDLE = "bundle"
_VALID_ADAPTER_TYPES = {
    ADAPTER_TYPE_LOCAL_UDP_BRIDGE,
    ADAPTER_TYPE_TCP_FORWARD,
    ADAPTER_TYPE_TCP_RELAY,
    ADAPTER_TYPE_BUNDLE,
}
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
BUNDLE_RULE_TCP_FORWARD = "tcp_forward"
BUNDLE_RULE_UDP_FORWARD = "udp_forward"
BUNDLE_RULE_UDP_BROADCAST_FORWARD = "udp_broadcast_forward"
BUNDLE_RULE_TCP_RELAY = "tcp_relay"
BUNDLE_RULE_UDP_RAW_BRIDGE = "udp_raw_bridge"
BUNDLE_RULE_KINDS = {
    BUNDLE_RULE_TCP_FORWARD,
    BUNDLE_RULE_UDP_FORWARD,
    BUNDLE_RULE_UDP_BROADCAST_FORWARD,
    BUNDLE_RULE_TCP_RELAY,
    BUNDLE_RULE_UDP_RAW_BRIDGE,
}
BUNDLE_STATUS_RUNNING = "running"
BUNDLE_STATUS_STOPPED = "stopped"
BUNDLE_STATUS_FAILED = "failed"


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
    adapter_type: str = ADAPTER_TYPE_BUNDLE
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: Optional[int] = None
    secondary_ip_enabled: bool = False
    secondary_ip_request: Optional[SecondaryIpRequest] = None
    secondary_ip_warning: Optional[str] = None

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
        if adapter_type not in _VALID_ADAPTER_TYPES:
            raise BackendError(
                code="INVALID_REQUEST",
                message=f"Unsupported adapter_config.adapter_type: {adapter_type}",
                details={
                    "field": "adapter_config.adapter_type",
                    "value": adapter_type,
                    "valid": sorted(_VALID_ADAPTER_TYPES),
                },
            )

        return cls(
            enabled=enabled,
            adapter_type=adapter_type,
            bind_host=_adapter_str(raw, "bind_host", cls.bind_host),
            bind_port=_adapter_port(raw, "bind_port", cls.bind_port),
            target_host=_adapter_str(raw, "target_host", cls.target_host),
            target_port=_optional_adapter_port(raw, "target_port"),
            secondary_ip_enabled=_adapter_bool(
                raw,
                "secondary_ip_enabled",
                cls.secondary_ip_enabled,
            ),
            secondary_ip_request=_optional_secondary_ip_request(raw),
            secondary_ip_warning=_optional_adapter_str(raw, "secondary_ip_warning"),
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "enabled": self.enabled,
            "adapter_type": self.adapter_type,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "target_host": self.target_host,
        }
        if self.target_port is not None:
            d["target_port"] = self.target_port
        if self.secondary_ip_enabled:
            d["secondary_ip_enabled"] = True
        if self.secondary_ip_request is not None:
            request: Dict[str, Any] = {
                "ip_address": self.secondary_ip_request.ip_address,
            }
            if self.secondary_ip_request.interface_hint is not None:
                request["interface_hint"] = self.secondary_ip_request.interface_hint
            if self.secondary_ip_request.prefix_length is not None:
                request["prefix_length"] = self.secondary_ip_request.prefix_length
            d["secondary_ip_request"] = request
        if self.secondary_ip_warning is not None:
            d["secondary_ip_warning"] = self.secondary_ip_warning
        return d


@dataclasses.dataclass
class BundleRule:
    """One adapter-backed forwarding rule in a local bundle."""
    id: str
    kind: str
    enabled: bool = True
    config: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BundleRule":
        if not isinstance(raw, dict):
            raise ValueError("Bundle rule must be an object")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("Bundle rule id must be a non-empty string")
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("Bundle rule kind must be a non-empty string")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Bundle rule {rule_id!r} enabled must be a boolean")
        config = raw.get("config", {})
        if not isinstance(config, dict):
            raise ValueError(f"Bundle rule {rule_id!r} config must be an object")
        return cls(
            id=rule_id.strip(),
            kind=kind.strip(),
            enabled=enabled,
            config=dict(config),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "enabled": self.enabled,
            "config": dict(self.config),
        }


@dataclasses.dataclass
class BundleConfig:
    """Ordered collection of forwarding rules started and stopped together."""
    id: str
    rules: List[BundleRule] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BundleConfig":
        if not isinstance(raw, dict):
            raise ValueError("Bundle config must be an object")
        bundle_id = raw.get("id", raw.get("name"))
        if not isinstance(bundle_id, str) or not bundle_id.strip():
            raise ValueError("Bundle id or name must be a non-empty string")
        rules = raw.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("Bundle rules must be a list")
        return cls(
            id=bundle_id.strip(),
            rules=[BundleRule.from_dict(rule) for rule in rules],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "rules": [rule.to_dict() for rule in self.rules],
        }


@dataclasses.dataclass
class BundleResult:
    """Structured outcome for a bundle start or stop operation."""
    bundle_id: str
    status: str
    started_rule_ids: List[str] = dataclasses.field(default_factory=list)
    stopped_rule_ids: List[str] = dataclasses.field(default_factory=list)
    failed_rule_id: Optional[str] = None
    failed_rule_kind: Optional[str] = None
    error_detail: Optional[str] = None
    cleanup_errors: List[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != BUNDLE_STATUS_FAILED

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "bundle_id": self.bundle_id,
            "status": self.status,
            "started_rule_ids": list(self.started_rule_ids),
            "stopped_rule_ids": list(self.stopped_rule_ids),
            "cleanup_errors": list(self.cleanup_errors),
        }
        if self.failed_rule_id is not None:
            result["failed_rule_id"] = self.failed_rule_id
        if self.failed_rule_kind is not None:
            result["failed_rule_kind"] = self.failed_rule_kind
        if self.error_detail is not None:
            result["error_detail"] = self.error_detail
        return result


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
    adapter_type: str = ADAPTER_TYPE_BUNDLE
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: Optional[int] = None
    counters: AdapterCounters = dataclasses.field(default_factory=AdapterCounters)
    error: Optional[Dict[str, str]] = None
    payload_diagnostics: Optional[Dict[str, Any]] = None

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
                "counters": self.counters.to_dict(),
                "error": self.error,
            })
            if self.target_port is not None:
                d["target_port"] = self.target_port
            if self.payload_diagnostics is not None:
                d["payload_diagnostics"] = self.payload_diagnostics
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
class LanPeerDto:
    peer_id: str
    name: str
    host: str
    port: int
    version: str
    last_seen_age_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "version": self.version,
            "last_seen_age_seconds": self.last_seen_age_seconds,
        }


@dataclasses.dataclass
class LanDiscoveryStatusResponse:
    running: bool
    peer_id: Optional[str]
    instance_name: str
    service_port: int
    broadcast_port: int
    peer_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "peer_id": self.peer_id,
            "instance_name": self.instance_name,
            "service_port": self.service_port,
            "broadcast_port": self.broadcast_port,
            "peer_count": self.peer_count,
        }


@dataclasses.dataclass
class LanDiscoveryPeersResponse:
    running: bool
    peers: List[LanPeerDto]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "running": self.running,
            "peers": [peer.to_dict() for peer in self.peers],
        }


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
class ParticipantDto:
    player_id: str
    player_name: str
    is_host: bool

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ParticipantDto":
        return cls(
            player_id=str(raw.get("player_id", "")),
            player_name=str(raw.get("player_name", "")),
            is_host=bool(raw.get("is_host", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "is_host": self.is_host,
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
    game_server_port: Optional[int] = None
    force_relay: bool = True
    created_at: float = 0.0
    updated_at: float = 0.0
    error: Optional[Dict[str, Any]] = None
    stats: Optional[SessionStats] = None
    adapter_config: Optional[AdapterConfig] = None
    adapter_status: Optional[AdapterStatus] = None
    player_id: Optional[str] = None
    protocol_version: Optional[int] = None
    max_players: Optional[int] = None
    participant_count: Optional[int] = None
    participants: List[ParticipantDto] = dataclasses.field(default_factory=list)
    host_player_id: Optional[str] = None
    last_room_event: Optional[str] = None
    room_ready: bool = False
    room_closed: bool = False
    relay_ready: bool = False
    relay_token_available: bool = False
    relay_target_host: Optional[str] = None
    relay_target_port: Optional[int] = None
    peer_endpoint_host: Optional[str] = None
    peer_endpoint_port: Optional[int] = None
    peer_endpoint_source: Optional[str] = None
    server_time: Optional[float] = None
    secondary_ip_enabled: bool = False
    secondary_ip_fallback_used: bool = False
    secondary_ip_warning: Optional[str] = None
    backend_admin: bool = False
    secondary_ip_bind_address: Optional[str] = None
    secondary_ip_interface_index: Optional[int] = None
    secondary_ip_interface_alias: Optional[str] = None
    adapter_bind_mode: str = "loopback"

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
            "force_relay": self.force_relay,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "player_id": self.player_id,
            "protocol_version": self.protocol_version,
            "max_players": self.max_players,
            "participant_count": self.participant_count,
            "participants": [participant.to_dict() for participant in self.participants],
            "host_player_id": self.host_player_id,
            "last_room_event": self.last_room_event,
            "room_ready": self.room_ready,
            "room_closed": self.room_closed,
            "relay_ready": self.relay_ready,
            "relay_token_available": self.relay_token_available,
            "relay_target_host": self.relay_target_host,
            "relay_target_port": self.relay_target_port,
            "peer_endpoint_host": self.peer_endpoint_host,
            "peer_endpoint_port": self.peer_endpoint_port,
            "peer_endpoint_source": self.peer_endpoint_source,
            "server_time": self.server_time,
            "secondary_ip_enabled": self.secondary_ip_enabled,
            "secondary_ip_fallback_used": self.secondary_ip_fallback_used,
            "secondary_ip_warning": self.secondary_ip_warning,
            "backend_admin": self.backend_admin,
            "secondary_ip_bind_address": self.secondary_ip_bind_address,
            "secondary_ip_interface_index": self.secondary_ip_interface_index,
            "secondary_ip_interface_alias": self.secondary_ip_interface_alias,
            "adapter_bind_mode": self.adapter_bind_mode,
        }
        if self.game_server_port is not None:
            d["game_server_port"] = self.game_server_port
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


def _optional_adapter_str(raw: Dict[str, Any], key: str) -> Optional[str]:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field adapter_config.{key} must be a string",
            details={"field": f"adapter_config.{key}"},
        )
    trimmed = value.strip()
    return trimmed or None


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


def _optional_adapter_port(raw: Dict[str, Any], key: str) -> Optional[int]:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
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


def _optional_secondary_ip_request(
    raw: Dict[str, Any],
) -> Optional[SecondaryIpRequest]:
    if "secondary_ip_request" not in raw or raw["secondary_ip_request"] is None:
        return None
    value = raw["secondary_ip_request"]
    if not isinstance(value, dict):
        raise BackendError(
            code="INVALID_REQUEST",
            message="Field adapter_config.secondary_ip_request must be a JSON object",
            details={"field": "adapter_config.secondary_ip_request"},
        )
    ip_address = value.get("ip_address")
    if not isinstance(ip_address, str) or not ip_address.strip():
        raise BackendError(
            code="INVALID_REQUEST",
            message="Field adapter_config.secondary_ip_request.ip_address must be a non-empty string",
            details={"field": "adapter_config.secondary_ip_request.ip_address"},
        )
    interface_hint = value.get("interface_hint")
    if interface_hint is not None and not isinstance(interface_hint, str):
        raise BackendError(
            code="INVALID_REQUEST",
            message="Field adapter_config.secondary_ip_request.interface_hint must be a string",
            details={"field": "adapter_config.secondary_ip_request.interface_hint"},
        )
    prefix_length = value.get("prefix_length")
    if prefix_length is not None and type(prefix_length) is not int:
        raise BackendError(
            code="INVALID_REQUEST",
            message="Field adapter_config.secondary_ip_request.prefix_length must be an integer",
            details={"field": "adapter_config.secondary_ip_request.prefix_length"},
        )
    return SecondaryIpRequest(
        ip_address=ip_address.strip(),
        interface_hint=interface_hint.strip() if isinstance(interface_hint, str) else None,
        prefix_length=prefix_length,
    )
