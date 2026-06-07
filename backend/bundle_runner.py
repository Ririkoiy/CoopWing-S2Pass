"""Local orchestration for starting and stopping forwarding adapter bundles."""
from __future__ import annotations

import socket
from typing import Callable, Dict, List, Optional, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile
from adapters.tcp_adapter import GenericTcpForwardAdapter
from adapters.transport import Transport
from adapters.udp_adapter import GenericUdpForwardAdapter
from adapters.udp_broadcast_forward_adapter import (
    GenericUdpBroadcastForwardAdapter,
)
from backend.models import (
    BUNDLE_RULE_KINDS,
    BUNDLE_RULE_TCP_FORWARD,
    BUNDLE_RULE_UDP_BROADCAST_FORWARD,
    BUNDLE_RULE_UDP_FORWARD,
    BUNDLE_STATUS_FAILED,
    BUNDLE_STATUS_RUNNING,
    BUNDLE_STATUS_STOPPED,
    BundleConfig,
    BundleResult,
    BundleRule,
)


AdapterFactory = Callable[[BundleRule], AdapterBase]
TransportFactory = Callable[[BundleRule], Transport]


def allocate_tcp_udp_port(bind_host: str) -> int:
    """Select one currently available numeric port for both TCP and UDP."""
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tcp_sock.bind((bind_host, 0))
        port = int(tcp_sock.getsockname()[1])
        udp_sock.bind((bind_host, port))
        return port
    finally:
        udp_sock.close()
        tcp_sock.close()


def allocate_udp_port(bind_host: str, excluded_port: Optional[int] = None) -> int:
    """Select an available UDP port distinct from *excluded_port*."""
    for _ in range(10):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.bind((bind_host, 0))
            port = int(udp_sock.getsockname()[1])
            if port != excluded_port:
                return port
        finally:
            udp_sock.close()
    raise RuntimeError("Unable to allocate a distinct UDP broadcast port")


class BundleRunner:
    """Compose existing forwarding adapters into one ordered lifecycle."""

    def __init__(
        self,
        adapter_factory: Optional[AdapterFactory] = None,
        transport_factory: Optional[TransportFactory] = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._adapter_factory = adapter_factory or self._create_adapter
        self._started: List[Tuple[BundleRule, AdapterBase]] = []
        self._bundle_id: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return bool(self._started)

    def start(self, bundle: BundleConfig) -> BundleResult:
        """Start enabled rules in config order with rollback on failure."""
        validation_error = self._validate(bundle)
        if validation_error is not None:
            return validation_error
        if self._started:
            return BundleResult(
                bundle_id=bundle.id,
                status=BUNDLE_STATUS_FAILED,
                error_detail="BundleRunner is already running",
            )

        enabled_rules = [rule for rule in bundle.rules if rule.enabled]
        if not enabled_rules:
            self._bundle_id = bundle.id
            return BundleResult(
                bundle_id=bundle.id,
                status=BUNDLE_STATUS_STOPPED,
            )

        started_ids: List[str] = []
        for rule in enabled_rules:
            try:
                adapter = self._adapter_factory(rule)
                adapter.start()
            except Exception as exc:
                stopped_ids, cleanup_errors = self._stop_started()
                self._bundle_id = None
                return BundleResult(
                    bundle_id=bundle.id,
                    status=BUNDLE_STATUS_FAILED,
                    started_rule_ids=started_ids,
                    stopped_rule_ids=stopped_ids,
                    failed_rule_id=rule.id,
                    failed_rule_kind=rule.kind,
                    error_detail=f"{exc.__class__.__name__}: {exc}",
                    cleanup_errors=cleanup_errors,
                )
            self._started.append((rule, adapter))
            started_ids.append(rule.id)

        self._bundle_id = bundle.id
        return BundleResult(
            bundle_id=bundle.id,
            status=BUNDLE_STATUS_RUNNING,
            started_rule_ids=started_ids,
        )

    def stop(self) -> BundleResult:
        """Stop successfully started rules in reverse order."""
        bundle_id = self._bundle_id or ""
        stopped_ids, cleanup_errors = self._stop_started()
        self._bundle_id = None
        return BundleResult(
            bundle_id=bundle_id,
            status=BUNDLE_STATUS_STOPPED,
            stopped_rule_ids=stopped_ids,
            cleanup_errors=cleanup_errors,
        )

    def _validate(self, bundle: BundleConfig) -> Optional[BundleResult]:
        if not isinstance(bundle, BundleConfig):
            return BundleResult(
                bundle_id="",
                status=BUNDLE_STATUS_FAILED,
                error_detail="bundle must be a BundleConfig",
            )
        if not isinstance(bundle.id, str) or not bundle.id.strip():
            return BundleResult(
                bundle_id="",
                status=BUNDLE_STATUS_FAILED,
                error_detail="Bundle id must be a non-empty string",
            )

        seen_ids = set()
        for rule in bundle.rules:
            if rule.id in seen_ids:
                return BundleResult(
                    bundle_id=bundle.id,
                    status=BUNDLE_STATUS_FAILED,
                    failed_rule_id=rule.id,
                    failed_rule_kind=rule.kind,
                    error_detail=f"Duplicate bundle rule id: {rule.id}",
                )
            seen_ids.add(rule.id)
            if rule.kind not in BUNDLE_RULE_KINDS:
                return BundleResult(
                    bundle_id=bundle.id,
                    status=BUNDLE_STATUS_FAILED,
                    failed_rule_id=rule.id,
                    failed_rule_kind=rule.kind,
                    error_detail=f"Unsupported bundle rule kind: {rule.kind}",
                )
        return None

    def _stop_started(self) -> Tuple[List[str], List[str]]:
        stopped_ids: List[str] = []
        cleanup_errors: List[str] = []
        while self._started:
            rule, adapter = self._started.pop()
            try:
                adapter.stop()
                stopped_ids.append(rule.id)
            except Exception as exc:
                cleanup_errors.append(
                    f"{rule.id} ({rule.kind}): {exc.__class__.__name__}: {exc}"
                )
        return stopped_ids, cleanup_errors

    def _create_adapter(self, rule: BundleRule) -> AdapterBase:
        profile = _profile_from_rule(rule)
        if rule.kind == BUNDLE_RULE_TCP_FORWARD:
            options = _selected_options(
                rule.config,
                "buffer_size",
                "connection_timeout",
            )
            return GenericTcpForwardAdapter(profile, **options)
        if rule.kind == BUNDLE_RULE_UDP_FORWARD:
            return GenericUdpForwardAdapter(profile, mode="forward")
        if rule.kind == BUNDLE_RULE_UDP_BROADCAST_FORWARD:
            if self._transport_factory is None:
                raise ValueError(
                    "udp_broadcast_forward requires a transport_factory"
                )
            options = _selected_options(
                rule.config,
                "origin_id",
                "max_payload_size",
                "max_hop_count",
                "recent_ttl_seconds",
                "recent_cache_limit",
            )
            return GenericUdpBroadcastForwardAdapter(
                profile,
                self._transport_factory(rule),
                **options,
            )
        raise ValueError(f"Unsupported bundle rule kind: {rule.kind}")


def _profile_from_rule(rule: BundleRule) -> GameProfile:
    config: Dict[str, object] = rule.config
    return GameProfile(
        profile_id=rule.id,
        display_name=rule.id,
        exe_path="",
        adapter_type=rule.kind,
        protocol="tcp" if rule.kind == BUNDLE_RULE_TCP_FORWARD else "udp",
        local_bind_host=str(config.get("local_bind_host", "127.0.0.1")),
        local_bind_port=_optional_int(config.get("local_bind_port", 0)),
        remote_target_host=str(config.get("remote_target_host", "")),
        remote_target_port=_optional_int(config.get("remote_target_port")),
    )


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"Expected integer port, got {value!r}")
    return value


def _selected_options(config: Dict[str, object], *keys: str) -> Dict[str, object]:
    return {key: config[key] for key in keys if key in config}
