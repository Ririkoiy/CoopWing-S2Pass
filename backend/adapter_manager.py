# -*- coding: utf-8 -*-
"""Backend-local adapter lifecycle manager.

The manager owns local UDP bridge lifecycle and opaque byte transports only.
Unknown session IDs return None; SessionManager owns session existence checks.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

from adapters.base import AdapterBase
from adapters.local_udp_bridge_adapter import LocalUdpBridgeAdapter
from adapters.profile import GameProfile
from adapters.bundle_transport_router import BundleTransportRouter
from adapters.tcp_adapter import GenericTcpForwardAdapter
from adapters.tcp_relay_adapter import TcpRelayAdapter
from adapters.transport import Transport
from backend.bundle_runner import (
    BundleRunner,
    allocate_tcp_udp_port,
    allocate_udp_port,
)
from backend.models import (
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_READY,
    ADAPTER_STATUS_STOPPED,
    ADAPTER_TYPE_BUNDLE,
    ADAPTER_TYPE_TCP_FORWARD,
    ADAPTER_TYPE_TCP_RELAY,
    BUNDLE_RULE_TCP_FORWARD,
    BUNDLE_RULE_TCP_RELAY,
    BUNDLE_RULE_UDP_BROADCAST_FORWARD,
    BUNDLE_RULE_UDP_FORWARD,
    BUNDLE_RULE_UDP_RAW_BRIDGE,
    AdapterConfig,
    AdapterCounters,
    AdapterStatus,
    BundleConfig,
    BundleRule,
)

TransportFactory = Callable[[str, AdapterConfig], Transport]
BundleRunnerFactory = Callable[[], BundleRunner]


class AdapterManager:
    """Manage adapter status and optional test/future transport-backed bridges."""

    def __init__(
        self,
        transport_factory: Optional[TransportFactory] = None,
        bundle_transport_factory: Optional[TransportFactory] = None,
        bundle_runner_factory: Optional[BundleRunnerFactory] = None,
    ) -> None:
        self._configs: Dict[str, AdapterConfig] = {}
        self._statuses: Dict[str, AdapterStatus] = {}
        self._adapters: Dict[str, AdapterBase] = {}
        self._bundle_runners: Dict[str, BundleRunner] = {}
        self._transports: Dict[str, Transport] = {}
        self._transport_factory = transport_factory
        self._bundle_transport_factory = bundle_transport_factory
        self._bundle_runner_factory = bundle_runner_factory
        self._lock = threading.Lock()

    def configure(
        self,
        session_id: str,
        adapter_config: Optional[AdapterConfig],
    ) -> Optional[AdapterStatus]:
        """Store passive config and return the initial passive status."""
        with self._lock:
            adapter = self._adapters.pop(session_id, None)
            bundle_runner = self._bundle_runners.pop(session_id, None)
            transport = self._transports.pop(session_id, None)
            if adapter is not None:
                adapter.stop()
            if bundle_runner is not None:
                bundle_runner.stop()
            self._close_transport(transport)
            if adapter_config is None:
                self._configs.pop(session_id, None)
                self._statuses.pop(session_id, None)
                return None

            if adapter_config.enabled:
                status = AdapterStatus.from_config(adapter_config)
            else:
                status = AdapterStatus.disabled()

            self._configs[session_id] = adapter_config
            self._statuses[session_id] = status
            return status

    def attach_transport(self, session_id: str, transport: Transport) -> None:
        """Attach an opaque transport for a configured enabled session.

        Attaching a transport never starts the UDP bridge. If the session is
        unknown or explicitly disabled, the transport is closed immediately when
        it has a close() method so live handles are not retained.
        """
        old_transport = None
        close_new = False
        with self._lock:
            config = self._configs.get(session_id)
            if config is None or not config.enabled:
                close_new = True
            else:
                old_transport = self._transports.pop(session_id, None)
                self._transports[session_id] = transport

        self._close_transport(old_transport)
        if close_new:
            self._close_transport(transport)

    def start(self, session_id: str) -> Optional[AdapterStatus]:
        """Start the configured adapter (UDP bridge or TCP forward)."""
        with self._lock:
            config = self._configs.get(session_id)
            status = self._statuses.get(session_id)
            if config is None or status is None:
                return None
            if not config.enabled:
                return status
            if (
                session_id in self._adapters
                or session_id in self._bundle_runners
            ):
                return self._snapshot_locked(session_id)
            is_bundle = config.adapter_type == ADAPTER_TYPE_BUNDLE
            is_tcp = config.adapter_type == ADAPTER_TYPE_TCP_FORWARD
            is_tcp_relay = config.adapter_type == ADAPTER_TYPE_TCP_RELAY

        if is_bundle:
            return self._start_bundle(session_id, config)

        # --- TCP forward path (no transport needed) ---
        if is_tcp:
            profile = self._profile_from_config(session_id, config)
            adapter = GenericTcpForwardAdapter(profile)

            try:
                adapter.start()
            except Exception as exc:
                adapter.stop()
                return self._set_error(
                    session_id,
                    config,
                    "ADAPTER_BIND_FAILED",
                    str(exc),
                )

            local_host, local_port = adapter.get_local_addr()
            ready = AdapterStatus(
                enabled=True,
                status=ADAPTER_STATUS_READY,
                adapter_type=config.adapter_type,
                bind_host=local_host or config.bind_host,
                bind_port=local_port if local_port is not None else config.bind_port,
                target_host=config.target_host,
                target_port=config.target_port,
                counters=AdapterCounters(),
                error=None,
            )
            with self._lock:
                self._adapters[session_id] = adapter
                self._statuses[session_id] = ready
                return self._snapshot_locked(session_id)

        # --- Transport-backed paths (UDP bridge or TCP relay) ---
        with self._lock:
            transport = self._transports.get(session_id)
            transport_factory = None if transport is not None else self._transport_factory

        if transport_factory is None:
            if transport is None:
                return self.snapshot(session_id)
        else:
            try:
                transport = transport_factory(session_id, config)
            except Exception as exc:
                return self._set_error(
                    session_id,
                    config,
                    "ADAPTER_TRANSPORT_FAILED",
                    f"Adapter transport unavailable: {exc}",
                )

            with self._lock:
                if session_id not in self._configs or not self._configs[session_id].enabled:
                    self._close_transport(transport)
                    return self._snapshot_locked(session_id)
                old_transport = self._transports.pop(session_id, None)
                self._transports[session_id] = transport
            self._close_transport(old_transport)

        profile = self._profile_from_config(session_id, config)
        if is_tcp_relay:
            adapter = TcpRelayAdapter(profile=profile, transport=transport)
        else:
            fixed_local_target_addr = None
            if config.target_port is not None:
                fixed_local_target_addr = (config.target_host, config.target_port)
            adapter = LocalUdpBridgeAdapter(
                profile=profile,
                transport=transport,
                fixed_local_target_addr=fixed_local_target_addr,
            )

        try:
            adapter.start()
        except Exception as exc:
            adapter.stop()
            with self._lock:
                failed_transport = self._transports.pop(session_id, None)
            self._close_transport(failed_transport)
            return self._set_error(
                session_id,
                config,
                "ADAPTER_BIND_FAILED",
                str(exc),
            )

        local_host, local_port = adapter.get_local_addr()
        ready = AdapterStatus(
            enabled=True,
            status=ADAPTER_STATUS_READY,
            adapter_type=config.adapter_type,
            bind_host=local_host or config.bind_host,
            bind_port=local_port if local_port is not None else config.bind_port,
            target_host=config.target_host,
            target_port=config.target_port,
            counters=AdapterCounters(),
            error=None,
        )
        with self._lock:
            self._adapters[session_id] = adapter
            self._statuses[session_id] = ready
            return self._snapshot_locked(session_id)

    def stop(self, session_id: str) -> Optional[AdapterStatus]:
        """Stop an adapter if it is running; idempotent for all states."""
        with self._lock:
            config = self._configs.get(session_id)
            status = self._statuses.get(session_id)
            adapter = self._adapters.pop(session_id, None)
            bundle_runner = self._bundle_runners.pop(session_id, None)
            transport = self._transports.pop(session_id, None)
        if config is None or status is None:
            self._close_transport(transport)
            return None
        if adapter is not None:
            self._close_transport(transport)
            adapter.stop()
        bundle_result = None
        if bundle_runner is not None:
            bundle_result = bundle_runner.stop()
            self._close_transport(transport)
        elif adapter is None:
            self._close_transport(transport)
        if not config.enabled:
            return self.snapshot(session_id)
        if (
            adapter is None
            and bundle_runner is None
            and status.status == ADAPTER_STATUS_STOPPED
        ):
            return self.snapshot(session_id)

        stopped = AdapterStatus.from_config(config, status=ADAPTER_STATUS_STOPPED)
        if status.enabled:
            stopped.bind_host = status.bind_host
            stopped.bind_port = status.bind_port
            stopped.counters = status.counters
        if bundle_result is not None:
            diagnostics = dict(status.payload_diagnostics or {})
            diagnostics.update(bundle_result.to_dict())
            stopped.payload_diagnostics = diagnostics
            if bundle_result.cleanup_errors:
                stopped.error = {
                    "code": "BUNDLE_STOP_FAILED",
                    "message": "; ".join(bundle_result.cleanup_errors),
                }
        with self._lock:
            self._statuses[session_id] = stopped
            return self._snapshot_locked(session_id)

    def snapshot(self, session_id: str) -> Optional[AdapterStatus]:
        """Return the current passive status, or None for unknown/unconfigured."""
        with self._lock:
            return self._snapshot_locked(session_id)

    def _snapshot_locked(self, session_id: str) -> Optional[AdapterStatus]:
        status = self._statuses.get(session_id)
        adapter = self._adapters.get(session_id)
        bundle_runner = self._bundle_runners.get(session_id)
        if status is None:
            return None
        if adapter is not None and status.enabled:
            adapter_lock = getattr(adapter, "_lock", None)
            if adapter_lock is not None:
                with adapter_lock:
                    status.counters = AdapterCounters(
                        packets_from_game=getattr(adapter, "packets_from_game", 0),
                        packets_to_transport=getattr(adapter, "packets_to_transport", 0),
                        packets_from_transport=getattr(adapter, "packets_from_transport", 0),
                        packets_to_game=getattr(adapter, "packets_to_game", 0),
                        bytes_from_game=getattr(adapter, "bytes_from_game", 0),
                        bytes_to_transport=getattr(adapter, "bytes_to_transport", 0),
                        bytes_from_transport=getattr(adapter, "bytes_from_transport", 0),
                        bytes_to_game=getattr(adapter, "bytes_to_game", 0),
                    )
            # Collect transport-layer payload diagnostics if available
            transport = getattr(adapter, "transport", None)
            if transport is not None:
                diag_fn = getattr(transport, "get_payload_diagnostics", None)
                if diag_fn is not None:
                    status.payload_diagnostics = diag_fn()
        if bundle_runner is not None and status.enabled:
            snapshot = getattr(bundle_runner, "snapshot", None)
            if callable(snapshot):
                rule_statuses = snapshot()
                diagnostics = dict(status.payload_diagnostics or {})
                diagnostics["rules"] = rule_statuses
                status.payload_diagnostics = diagnostics
                status.counters = _bundle_counters(rule_statuses)
        return status

    def _set_error(
        self,
        session_id: str,
        config: AdapterConfig,
        code: str,
        message: str,
    ) -> AdapterStatus:
        error_status = AdapterStatus.from_config(
            config,
            status=ADAPTER_STATUS_ERROR,
            error={"code": code, "message": message},
        )
        with self._lock:
            self._statuses[session_id] = error_status
            return error_status

    def _start_bundle(
        self,
        session_id: str,
        config: AdapterConfig,
    ) -> AdapterStatus:
        if (
            config.target_port is None
            or config.target_port < 0
            or config.target_port > 65535
        ):
            return self._set_error(
                session_id,
                config,
                "BUNDLE_START_FAILED",
                "Bundle target_port is required (1-65535)",
            )

        join_no_target = config.target_port == 0

        try:
            bind_port = (
                config.bind_port
                if config.bind_port != 0
                else allocate_tcp_udp_port(config.bind_host)
            )
            broadcast_bind_port = allocate_udp_port(
                config.bind_host,
                excluded_port=bind_port,
            )
            raw_transport = self._bundle_transport(session_id, config)
            # Wrap in BundleTransportRouter so transport-backed bundle
            # adapters each get their own RoutedTransport and do not
            # overwrite each other's receive callbacks.
            router = BundleTransportRouter(raw_transport)
            bundle = self._bundle_from_config(
                session_id,
                config,
                bind_port,
                broadcast_bind_port,
            )
            runner = (
                self._bundle_runner_factory()
                if self._bundle_runner_factory is not None
                else BundleRunner(
                    transport_factory=lambda rule, _r=router: (
                        _r.get_transport(rule.kind)
                    ),
                )
            )
            result = runner.start(bundle)
        except Exception as exc:
            with self._lock:
                failed_transport = self._transports.pop(session_id, None)
            self._close_transport(failed_transport)
            return self._set_error(
                session_id,
                config,
                "BUNDLE_START_FAILED",
                str(exc),
            )

        if not result.ok:
            with self._lock:
                failed_transport = self._transports.pop(session_id, None)
            self._close_transport(failed_transport)
            error_status = self._set_error(
                session_id,
                config,
                "BUNDLE_START_FAILED",
                result.error_detail or "Bundle failed to start",
            )
            error_status.payload_diagnostics = self._bundle_diagnostics(
                result.to_dict(),
                bundle,
                broadcast_bind_port,
            )
            return error_status

        ready = AdapterStatus(
            enabled=True,
            status=ADAPTER_STATUS_READY,
            adapter_type=ADAPTER_TYPE_BUNDLE,
            bind_host=config.bind_host,
            bind_port=bind_port,
            target_host=config.target_host,
            target_port=config.target_port,
            counters=AdapterCounters(),
            error=None,
            payload_diagnostics=self._bundle_diagnostics(
                result.to_dict(),
                bundle,
                broadcast_bind_port,
            ),
        )
        with self._lock:
            self._bundle_runners[session_id] = runner
            self._statuses[session_id] = ready
            return self._snapshot_locked(session_id)

    @staticmethod
    def _bundle_from_config(
        session_id: str,
        config: AdapterConfig,
        bind_port: int,
        broadcast_bind_port: int,
    ) -> BundleConfig:
        rule_config = {
            "local_bind_host": config.bind_host,
            "local_bind_port": bind_port,
            "remote_target_host": config.target_host,
            "remote_target_port": config.target_port,
        }
        join_no_target = config.target_port == 0
        if join_no_target:
            tcp_relay_config = {
                "local_bind_host": config.bind_host,
                "local_bind_port": bind_port,
            }
            udp_raw_config = {
                "local_bind_host": config.bind_host,
                "local_bind_port": bind_port,
            }
            broadcast_config = {
                "local_bind_host": config.bind_host,
                "local_bind_port": broadcast_bind_port,
                "remote_target_host": "127.0.0.1",
                "remote_target_port": broadcast_bind_port,
                "route_responses_to_last_sender": True,
                "strict_target_port_match": False,
            }
            return BundleConfig(
                id=f"{session_id}_bundle",
                rules=[
                    BundleRule(
                        id=f"{session_id}_tcp",
                        kind=BUNDLE_RULE_TCP_FORWARD,
                        enabled=False,
                        config=dict(rule_config),
                    ),
                    BundleRule(
                        id=f"{session_id}_udp",
                        kind=BUNDLE_RULE_UDP_FORWARD,
                        enabled=False,
                        config=dict(rule_config),
                    ),
                    BundleRule(
                        id=f"{session_id}_tcp_relay",
                        kind=BUNDLE_RULE_TCP_RELAY,
                        enabled=True,
                        config=tcp_relay_config,
                    ),
                    BundleRule(
                        id=f"{session_id}_udp_raw",
                        kind=BUNDLE_RULE_UDP_RAW_BRIDGE,
                        enabled=True,
                        config=udp_raw_config,
                    ),
                    BundleRule(
                        id=f"{session_id}_udp_broadcast",
                        kind=BUNDLE_RULE_UDP_BROADCAST_FORWARD,
                        config=broadcast_config,
                    ),
                ],
            )
        tcp_relay_config = {
            "local_bind_host": config.bind_host,
            "local_bind_port": 0,
            "remote_target_host": config.target_host,
            "remote_target_port": config.target_port,
        }
        udp_raw_config = {
            "local_bind_host": config.bind_host,
            "local_bind_port": 0,
            "remote_target_host": config.target_host,
            "remote_target_port": config.target_port,
        }
        broadcast_config = {
            "local_bind_host": config.bind_host,
            "local_bind_port": broadcast_bind_port,
            "remote_target_host": config.target_host,
            "remote_target_port": config.target_port,
            "strict_target_port_match": False,
        }
        return BundleConfig(
            id=f"{session_id}_bundle",
            rules=[
                BundleRule(
                    id=f"{session_id}_tcp",
                    kind=BUNDLE_RULE_TCP_FORWARD,
                    enabled=True,
                    config=dict(rule_config),
                ),
                BundleRule(
                    id=f"{session_id}_udp",
                    kind=BUNDLE_RULE_UDP_FORWARD,
                    enabled=True,
                    config=dict(rule_config),
                ),
                BundleRule(
                    id=f"{session_id}_tcp_relay",
                    kind=BUNDLE_RULE_TCP_RELAY,
                    enabled=True,
                    config=tcp_relay_config,
                ),
                BundleRule(
                    id=f"{session_id}_udp_raw",
                    kind=BUNDLE_RULE_UDP_RAW_BRIDGE,
                    enabled=True,
                    config=udp_raw_config,
                ),
                BundleRule(
                    id=f"{session_id}_udp_broadcast",
                    kind=BUNDLE_RULE_UDP_BROADCAST_FORWARD,
                    config=broadcast_config,
                ),
            ],
        )

    def _bundle_transport(
        self,
        session_id: str,
        config: AdapterConfig,
    ) -> Transport:
        with self._lock:
            transport = self._transports.get(session_id)
        if transport is not None:
            return transport
        if self._bundle_transport_factory is None:
            raise RuntimeError(
                "UDP broadcast/LAN discovery forwarding requires "
                "an available bundle transport"
            )
        transport = self._bundle_transport_factory(session_id, config)
        with self._lock:
            if session_id not in self._configs or not self._configs[session_id].enabled:
                self._close_transport(transport)
                raise RuntimeError("Bundle session is no longer configured")
            old_transport = self._transports.pop(session_id, None)
            self._transports[session_id] = transport
        self._close_transport(old_transport)
        return transport

    @staticmethod
    def _bundle_diagnostics(
        result: Dict[str, object],
        bundle: BundleConfig,
        broadcast_bind_port: int,
    ) -> Dict[str, object]:
        has_game_forward = any(
            rule.enabled and rule.kind in {
                BUNDLE_RULE_TCP_FORWARD,
                BUNDLE_RULE_UDP_FORWARD,
            }
            for rule in bundle.rules
        )
        has_broadcast = any(
            rule.enabled and rule.kind == BUNDLE_RULE_UDP_BROADCAST_FORWARD
            for rule in bundle.rules
        )
        has_tcp_relay = any(
            rule.enabled and rule.kind == BUNDLE_RULE_TCP_RELAY
            for rule in bundle.rules
        )
        has_udp_raw_bridge = any(
            rule.enabled and rule.kind == BUNDLE_RULE_UDP_RAW_BRIDGE
            for rule in bundle.rules
        )
        diag: Dict[str, object] = {
            **result,
            "included_rule_kinds": [rule.kind for rule in bundle.rules],
            "udp_broadcast_bind_port": broadcast_bind_port,
            "discovery_helper_connection": {
                "host": bundle.rules[0].config.get("local_bind_host"),
                "port": broadcast_bind_port,
                "udp_available": has_broadcast,
            },
            "udp_broadcast_lan_discovery_included": has_broadcast,
            "broadcast_only_forwarding": (
                not has_game_forward
                and not has_tcp_relay
                and not has_udp_raw_bridge
                and has_broadcast
            ),
            "tcp_relay_available": has_tcp_relay,
            "udp_raw_bridge_available": has_udp_raw_bridge,
            "tcp_available": has_game_forward or has_tcp_relay,
            "udp_available": has_game_forward or has_udp_raw_bridge,
        }
        # Determine bind_port for local_game_connection
        bind_host = bundle.rules[0].config.get("local_bind_host")
        if has_game_forward:
            # Create/host: local_game_connection is the shared TCP/UDP
            # port used by tcp_forward and udp_forward.
            bind_port = bundle.rules[0].config.get("local_bind_port")
            diag["local_game_connection"] = {
                "host": bind_host,
                "port": bind_port,
                "tcp_available": True,
                "udp_available": True,
            }
        elif has_tcp_relay or has_udp_raw_bridge:
            shared_port = None
            for rule in bundle.rules:
                if rule.enabled and rule.kind in {
                    BUNDLE_RULE_TCP_RELAY,
                    BUNDLE_RULE_UDP_RAW_BRIDGE,
                }:
                    shared_port = rule.config.get("local_bind_port")
                    if shared_port:
                        break
            diag["local_game_connection"] = {
                "host": bind_host,
                "port": shared_port,
                "tcp_available": has_tcp_relay,
                "udp_available": has_udp_raw_bridge,
            }
        return diag

    def _profile_from_config(self, session_id: str, config: AdapterConfig) -> GameProfile:
        if config.adapter_type == ADAPTER_TYPE_TCP_FORWARD:
            protocol = ""
        elif config.adapter_type == ADAPTER_TYPE_TCP_RELAY:
            protocol = "tcp"
        else:
            protocol = "udp"
        return GameProfile(
            profile_id=session_id,
            display_name=f"Adapter {session_id}",
            exe_path="",
            adapter_type=config.adapter_type,
            protocol=protocol,
            local_bind_host=config.bind_host,
            local_bind_port=config.bind_port,
            remote_target_host=config.target_host,
            remote_target_port=config.target_port,
        )

    @staticmethod
    def _close_transport(transport: Optional[Transport]) -> None:
        if transport is None:
            return
        close = getattr(transport, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _bundle_counters(rule_statuses: list[dict[str, object]]) -> AdapterCounters:
    counters = AdapterCounters()
    for rule in rule_statuses:
        stats = rule.get("stats")
        if not isinstance(stats, dict):
            continue
        kind = rule.get("kind")
        if kind == BUNDLE_RULE_TCP_RELAY:
            counters.packets_from_game += _int_stat(stats, "packets_from_game")
            counters.packets_to_transport += _int_stat(stats, "packets_to_transport")
            counters.packets_from_transport += _int_stat(stats, "packets_from_transport")
            counters.packets_to_game += _int_stat(stats, "packets_to_game")
            counters.bytes_from_game += _int_stat(stats, "bytes_from_game")
            counters.bytes_to_transport += _int_stat(stats, "bytes_to_transport")
            counters.bytes_from_transport += _int_stat(stats, "bytes_from_transport")
            counters.bytes_to_game += _int_stat(stats, "bytes_to_game")
        elif kind == BUNDLE_RULE_UDP_RAW_BRIDGE:
            counters.packets_from_game += _int_stat(stats, "packets_from_game")
            counters.packets_to_transport += _int_stat(stats, "packets_to_transport")
            counters.packets_from_transport += _int_stat(stats, "packets_from_transport")
            counters.packets_to_game += _int_stat(stats, "packets_to_game")
            counters.bytes_from_game += _int_stat(stats, "bytes_from_game")
            counters.bytes_to_transport += _int_stat(stats, "bytes_to_transport")
            counters.bytes_from_transport += _int_stat(stats, "bytes_from_transport")
            counters.bytes_to_game += _int_stat(stats, "bytes_to_game")
    return counters


def _int_stat(stats: dict[str, object], key: str) -> int:
    value = stats.get(key, 0)
    return value if type(value) is int else 0
