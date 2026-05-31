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
from adapters.tcp_adapter import GenericTcpForwardAdapter
from adapters.transport import Transport
from backend.models import (
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_READY,
    ADAPTER_STATUS_STOPPED,
    ADAPTER_TYPE_TCP_FORWARD,
    AdapterConfig,
    AdapterCounters,
    AdapterStatus,
)

TransportFactory = Callable[[str, AdapterConfig], Transport]


class AdapterManager:
    """Manage adapter status and optional test/future transport-backed bridges."""

    def __init__(self, transport_factory: Optional[TransportFactory] = None) -> None:
        self._configs: Dict[str, AdapterConfig] = {}
        self._statuses: Dict[str, AdapterStatus] = {}
        self._adapters: Dict[str, AdapterBase] = {}
        self._transports: Dict[str, Transport] = {}
        self._transport_factory = transport_factory
        self._lock = threading.Lock()

    def configure(
        self,
        session_id: str,
        adapter_config: Optional[AdapterConfig],
    ) -> Optional[AdapterStatus]:
        """Store passive config and return the initial passive status."""
        with self._lock:
            adapter = self._adapters.pop(session_id, None)
            transport = self._transports.pop(session_id, None)
            if adapter is not None:
                adapter.stop()
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
            if session_id in self._adapters:
                return self._snapshot_locked(session_id)
            is_tcp = config.adapter_type == ADAPTER_TYPE_TCP_FORWARD

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

        # --- UDP bridge path (needs transport) ---
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
            transport = self._transports.pop(session_id, None)
        if config is None or status is None:
            self._close_transport(transport)
            return None
        self._close_transport(transport)
        if adapter is not None:
            adapter.stop()
        if not config.enabled:
            return self.snapshot(session_id)
        if adapter is None and status.status == ADAPTER_STATUS_STOPPED:
            return self.snapshot(session_id)

        stopped = AdapterStatus.from_config(config, status=ADAPTER_STATUS_STOPPED)
        if status.enabled:
            stopped.bind_host = status.bind_host
            stopped.bind_port = status.bind_port
            stopped.counters = status.counters
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

    def _profile_from_config(self, session_id: str, config: AdapterConfig) -> GameProfile:
        return GameProfile(
            profile_id=session_id,
            display_name=f"Adapter {session_id}",
            exe_path="",
            adapter_type=config.adapter_type,
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
