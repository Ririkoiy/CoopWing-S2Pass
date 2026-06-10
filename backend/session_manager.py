# -*- coding: utf-8 -*-
"""S2Pass Backend — fake session manager.

Manages session records, status, logs, and deterministic fake runner events.
Does not import network_core or adapters.
"""
from __future__ import annotations

import secrets
import threading
import time
import os
import dataclasses
from typing import Any, Callable, Dict, List, Optional, Protocol

from backend.adapter_manager import AdapterManager
from backend.models import (
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_READY,
    ADAPTER_TYPE_BUNDLE,
    ADAPTER_TYPE_LOCAL_UDP_BRIDGE,
    ADAPTER_TYPE_TCP_FORWARD,
    AdapterConfig,
    AdapterStatus,
    BackendError,
    ParticipantDto,
    SessionEvent,
    SessionInfo,
    SessionStats,
)
from adapters.transport import make_fake_pair
from secondary_ip_manager import (
    FALLBACK_BIND_HOST,
    InMemoryLeaseStore,
    SecondaryIpManager,
    SecondaryIpSystem,
)

RUNNER_MODE_ENV = "S2PASS_BACKEND_RUNNER"
RUNNER_MODE_FAKE = "fake"
RUNNER_MODE_REAL_CORE = "real_core"
_RUNNER_MODES = {RUNNER_MODE_FAKE, RUNNER_MODE_REAL_CORE}
_ROOM_ID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ROOM_ID_LENGTH = 6
_EVENT_STATUS: Dict[str, str] = {
    "session_starting": "starting",
    "room_created": "room_created",
    "room_joined": "room_joined",
    "room_ready": "room_ready",
    "room_closed": "closed",
    "relay_ready": "relay_ready",
    "session_running": "running",
    "session_stopping": "stopping",
    "session_stopped": "stopped",
    "session_failed": "failed",
}
_CREATE_CONFIRM_TIMEOUT_SECONDS = 10.0
_ROOM_ID_CONFIRMING_EVENTS = {"room_created", "room_joined", "relay_ready"}


RunnerEventSink = Callable[[str, str, Optional[Dict[str, Any]]], None]


def _generate_session_id() -> str:
    """Generate session_id: s_<hex12>"""
    return f"s_{secrets.token_hex(6)}"


def _generate_room_id() -> str:
    """Generate room_id: [A-Z0-9]{6} excluding I/O/1/0 for readability."""
    return ''.join(secrets.choice(_ROOM_ID_CHARS) for _ in range(_ROOM_ID_LENGTH))


class SessionRunner(Protocol):
    """Internal runner boundary for fake now and real Core later."""

    def start_create(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Start a create lifecycle and report events through *emit*."""

    def start_join(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Start a join lifecycle and report events through *emit*."""

    def stop(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Stop a lifecycle and report events through *emit*."""


class FakeSessionRunner:
    """Deterministic offline runner used for tests and UI preview."""

    def start_create(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        if info.room_id is None:
            info.room_id = _generate_room_id()
        emit("room_created", f"Room {info.room_id} created", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        emit("session_running", "Session running", {"session_id": info.session_id})

    def start_join(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        emit("room_joined", f"Joined room {info.room_id}", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        emit("session_running", "Session running", {"session_id": info.session_id})

    def stop(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        emit("session_stopping", "Session stopping", {"session_id": info.session_id})
        emit("session_stopped", "Session stopped", {"session_id": info.session_id})


class _NoopSecondaryIpSystem(SecondaryIpSystem):
    """Default backend system boundary: never mutates machine IP settings."""

    def has_ip_mutation_permission(self) -> bool:
        return False

    def list_interfaces(self):
        return []

    def list_interface_ipv4(self, interface_index: int):
        return []


def resolve_runner_mode(runner_mode: Optional[str] = None) -> str:
    """Resolve and validate the backend runner mode."""
    raw = runner_mode
    if raw is None:
        raw = os.environ.get(RUNNER_MODE_ENV, RUNNER_MODE_FAKE)
    mode = str(raw).strip().lower()
    if mode not in _RUNNER_MODES:
        raise ValueError(
            f"Invalid backend runner mode: {raw!r}. "
            f"Expected one of: {', '.join(sorted(_RUNNER_MODES))}"
        )
    return mode


def make_runner_factory(runner_mode: Optional[str] = None) -> Callable[[], SessionRunner]:
    """Return the runner factory for *runner_mode*.

    Explicit real_core mode validates CoreSessionRunner at configuration time;
    it must not silently fall back to fake mode.
    """
    mode = resolve_runner_mode(runner_mode)
    if mode == RUNNER_MODE_FAKE:
        return FakeSessionRunner

    try:
        from backend.core_session_runner import CoreSessionRunner
        CoreSessionRunner()
    except Exception as exc:
        raise RuntimeError(
            "S2PASS_BACKEND_RUNNER=real_core requested, but "
            "CoreSessionRunner could not be imported or constructed"
        ) from exc
    return CoreSessionRunner


class SessionManager:
    """Holds backend sessions and applies runner lifecycle events."""

    def __init__(
        self,
        runner_factory: Optional[Callable[[], SessionRunner]] = None,
        runner_mode: Optional[str] = None,
        create_confirm_timeout: float = _CREATE_CONFIRM_TIMEOUT_SECONDS,
        secondary_ip_manager: Optional[SecondaryIpManager] = None,
        adapter_manager: Optional[AdapterManager] = None,
    ) -> None:
        self.runner_mode = resolve_runner_mode(runner_mode)
        self._sessions: Dict[str, SessionInfo] = {}
        self._logs: Dict[str, List[SessionEvent]] = {}
        self._runners: Dict[str, SessionRunner] = {}
        self._adapter_manager = adapter_manager or AdapterManager(
            bundle_transport_factory=(
                self._make_fake_bundle_transport
                if self.runner_mode == RUNNER_MODE_FAKE
                else None
            ),
        )
        self._custom_runner_factory = runner_factory
        self._runner_factory = runner_factory or make_runner_factory(self.runner_mode)
        self._lock = threading.Lock()
        self._fake_port_counter: int = 40000
        self._fake_port_lock = threading.Lock()
        self._create_confirm_timeout = max(0.0, float(create_confirm_timeout))
        self._create_confirm_events: Dict[str, threading.Event] = {}
        self._secondary_ip_manager = secondary_ip_manager or SecondaryIpManager(
            _NoopSecondaryIpSystem(),
            InMemoryLeaseStore(),
        )

    def _allocate_fake_port(self) -> int:
        """Return next unique fake adapter port (thread-safe)."""
        with self._fake_port_lock:
            port = self._fake_port_counter
            self._fake_port_counter += 1
            return port

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def create_session(self, request: Dict[str, Any]) -> SessionInfo:
        server_host = _require_str(request, "server_host")
        player_name = _require_str(request, "player_name")
        server_port = _require_port(request, "server_port", default=9000)
        server_udp_port = _require_port(request, "server_udp_port", default=9001)
        game_server_port = _optional_request_port(request, "game_server_port")
        force_relay = _request_bool(request, "force_relay", default=True)
        bind_port = _require_port_or_zero(request, "bind_port", default=0)
        adapter_config = _parse_adapter_config(request)
        adapter_config, secondary_ip_state = self._resolve_secondary_ip_adapter_config(
            adapter_config,
        )
        _validate_create_adapter_target(adapter_config, game_server_port)
        _validate_forward_adapter_target(adapter_config)

        session_id = _generate_session_id()
        now = time.time()

        adapter_port = bind_port if bind_port != 0 else self._allocate_fake_port()

        info = SessionInfo(
            session_id=session_id,
            role="create",
            status="starting",
            room_id=None,
            player_name=player_name,
            server_host=server_host,
            server_port=server_port,
            server_udp_port=server_udp_port,
            adapter_host=str(request.get("bind_host", "127.0.0.1")),
            adapter_port=adapter_port,
            game_server_host=str(request.get("game_server_host", "127.0.0.1")),
            game_server_port=game_server_port,
            force_relay=force_relay,
            created_at=now,
            updated_at=now,
            stats=SessionStats(),
            adapter_config=adapter_config,
            secondary_ip_enabled=secondary_ip_state["enabled"],
            secondary_ip_fallback_used=secondary_ip_state["fallback_used"],
            secondary_ip_warning=secondary_ip_state["warning"],
            backend_admin=secondary_ip_state["backend_admin"],
            secondary_ip_bind_address=secondary_ip_state["bind_address"],
            secondary_ip_interface_index=secondary_ip_state["interface_index"],
            secondary_ip_interface_alias=secondary_ip_state["interface_alias"],
            adapter_bind_mode=secondary_ip_state["bind_mode"],
        )
        info.adapter_status = self._adapter_manager.configure(session_id, adapter_config)

        runner = self._make_runner(session_id, adapter_config)

        with self._lock:
            self._sessions[session_id] = info
            self._logs[session_id] = []
            self._runners[session_id] = runner
            self._create_confirm_events[session_id] = threading.Event()

        self._emit_event(
            session_id,
            "session_created",
            f"Session {session_id} created",
            {"session_id": session_id, "role": "create"},
            timestamp=now,
        )
        self._emit_event(
            session_id,
            "session_starting",
            "Session starting",
            None,
            timestamp=now,
        )
        runner.start_create(info, self._make_event_sink(session_id))
        self._wait_for_create_confirmation(session_id)

        return info

    def join_session(self, request: Dict[str, Any]) -> SessionInfo:
        server_host = _require_str(request, "server_host")
        room_id = _require_str(request, "room_id")
        player_name = _require_str(request, "player_name")
        server_port = _require_port(request, "server_port", default=9000)
        server_udp_port = _require_port(request, "server_udp_port", default=9001)
        game_server_port = _optional_request_port(request, "game_server_port")
        force_relay = _request_bool(request, "force_relay", default=True)
        adapter_config = _parse_adapter_config(request)
        if adapter_config is None:
            adapter_config = AdapterConfig(
                enabled=True,
                adapter_type=ADAPTER_TYPE_BUNDLE,
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            )
        adapter_config, secondary_ip_state = self._resolve_secondary_ip_adapter_config(
            adapter_config,
        )
        _validate_forward_adapter_target(adapter_config, for_join=True)

        session_id = _generate_session_id()
        now = time.time()

        info = SessionInfo(
            session_id=session_id,
            role="join",
            status="starting",
            room_id=room_id,
            player_name=player_name,
            server_host=server_host,
            server_port=server_port,
            server_udp_port=server_udp_port,
            adapter_host="127.0.0.1",
            adapter_port=self._allocate_fake_port(),
            game_server_host=str(request.get("game_server_host", "127.0.0.1")),
            game_server_port=game_server_port,
            force_relay=force_relay,
            created_at=now,
            updated_at=now,
            stats=SessionStats(),
            adapter_config=adapter_config,
            secondary_ip_enabled=secondary_ip_state["enabled"],
            secondary_ip_fallback_used=secondary_ip_state["fallback_used"],
            secondary_ip_warning=secondary_ip_state["warning"],
            backend_admin=secondary_ip_state["backend_admin"],
            secondary_ip_bind_address=secondary_ip_state["bind_address"],
            secondary_ip_interface_index=secondary_ip_state["interface_index"],
            secondary_ip_interface_alias=secondary_ip_state["interface_alias"],
            adapter_bind_mode=secondary_ip_state["bind_mode"],
        )
        info.adapter_status = self._adapter_manager.configure(session_id, adapter_config)

        runner = self._make_runner(session_id, adapter_config)

        with self._lock:
            self._sessions[session_id] = info
            self._logs[session_id] = []
            self._runners[session_id] = runner

        self._emit_event(
            session_id,
            "session_created",
            f"Session {session_id} created",
            {"session_id": session_id, "role": "join"},
            timestamp=now,
        )
        self._emit_event(
            session_id,
            "session_starting",
            "Session starting",
            None,
            timestamp=now,
        )
        runner.start_join(info, self._make_event_sink(session_id))

        return info

    def get_session(self, session_id: str) -> SessionInfo:
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise BackendError(
                code="SESSION_NOT_FOUND",
                message=f"Session not found: {session_id}",
                details={"session_id": session_id},
            )
        adapter_status = self._adapter_manager.snapshot(session_id)
        if adapter_status is not None:
            info.adapter_status = adapter_status
        return info

    def list_sessions(self) -> List[SessionInfo]:
        with self._lock:
            sessions = list(self._sessions.values())
        for info in sessions:
            adapter_status = self._adapter_manager.snapshot(info.session_id)
            if adapter_status is not None:
                info.adapter_status = adapter_status
        return sessions

    def backend_has_ip_mutation_permission(self) -> bool:
        checker = getattr(
            self._secondary_ip_manager,
            "has_ip_mutation_permission",
            None,
        )
        if checker is None:
            return False
        return bool(checker())

    def secondary_ip_recommendation(self) -> Dict[str, Any]:
        recommender = getattr(
            self._secondary_ip_manager,
            "recommend_secondary_ip",
            None,
        )
        if not callable(recommender):
            return {
                "available": False,
                "backend_admin": self.backend_has_ip_mutation_permission(),
                "interface_index": None,
                "interface_alias": None,
                "interface_description": None,
                "interface_ip": None,
                "prefix_length": None,
                "recommended_ip": None,
                "reason": "not_supported",
                "warning": "secondary IP recommendation is not supported",
            }
        try:
            recommendation = recommender()
        except Exception as exc:
            return {
                "available": False,
                "backend_admin": self.backend_has_ip_mutation_permission(),
                "interface_index": None,
                "interface_alias": None,
                "interface_description": None,
                "interface_ip": None,
                "prefix_length": None,
                "recommended_ip": None,
                "reason": "recommendation_failed",
                "warning": str(exc),
            }
        to_dict = getattr(recommendation, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return dict(recommendation)

    # ------------------------------------------------------------------
    # secondary IP lifecycle
    # ------------------------------------------------------------------

    def startup_auto_allocate_secondary_ip(self) -> Dict[str, Any]:
        """Clean stale leases and attempt safe auto-allocation.

        Safe to call even when not admin — returns status with
        ``allocated=False`` in that case.
        """
        try:
            cleanup = self._secondary_ip_manager.startup_cleanup_stale_leases()
        except Exception:
            cleanup = None
        status = self._secondary_ip_manager.auto_allocate_on_admin_startup()
        result = status.to_dict()
        if cleanup is not None:
            result["startup_cleanup"] = {
                "ok": cleanup.ok,
                "items": [
                    {
                        "interface_index": item.lease.interface_index,
                        "ip_address": item.lease.ip_address,
                        "status": item.status,
                        "error": item.error,
                    }
                    for item in cleanup.items
                ],
            }
        return result

    def release_secondary_ip(self) -> Dict[str, Any]:
        """Release the secondary IP allocated during this session."""
        result = self._secondary_ip_manager.release_allocated_secondary_ip()
        return {
            "ok": result.ok,
            "items": [
                {
                    "interface_index": item.lease.interface_index,
                    "ip_address": item.lease.ip_address,
                    "status": item.status,
                    "error": item.error,
                }
                for item in result.items
            ],
        }

    def secondary_ip_status(self) -> Dict[str, Any]:
        """Return the current secondary IP state snapshot."""
        return self._secondary_ip_manager.get_secondary_ip_status().to_dict()

    def shutdown_secondary_ip(self) -> Dict[str, Any]:
        """Release secondary IP on shutdown. Best-effort, never raises."""
        try:
            return self.release_secondary_ip()
        except Exception:
            return {"ok": False, "items": [], "warning": "shutdown release failed"}

    def stop_session(self, session_id: str) -> SessionInfo:
        info = self.get_session(session_id)
        if info.status in ("stopped", "failed"):
            self._stop_adapter_if_ready(session_id, info)
            return info
        with self._lock:
            runner = self._runners.get(session_id)
        if runner is None:
            raise BackendError(
                code="INTERNAL_ERROR",
                message=f"Session runner missing: {session_id}",
                details={"session_id": session_id},
            )
        self._stop_adapter_if_ready(session_id, info)
        runner.stop(info, self._make_event_sink(session_id))
        return info

    def get_logs(self, session_id: str) -> List[SessionEvent]:
        """Return per-session events. Raises SESSION_NOT_FOUND if unknown."""
        self.get_session(session_id)  # validates existence
        with self._lock:
            return list(self._logs.get(session_id, []))

    # ------------------------------------------------------------------
    # runner event sink
    # ------------------------------------------------------------------

    def _make_event_sink(self, session_id: str) -> RunnerEventSink:
        def emit(event_type: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
            self._emit_event(session_id, event_type, message, data)
        return emit

    def _make_runner(
        self,
        session_id: str,
        adapter_config: Optional[AdapterConfig],
    ) -> SessionRunner:
        """Create a runner, adding real_core transport wiring only when needed."""
        if self._custom_runner_factory is not None:
            return self._custom_runner_factory()
        if (
            self.runner_mode == RUNNER_MODE_REAL_CORE
            and adapter_config is not None
            and adapter_config.enabled
        ):
            from adapters.core_transport_adapter import CoreTransportAdapter
            from backend.core_session_runner import CoreSessionRunner

            def transport_factory(core: Any, loop: Any) -> Any:
                transport = CoreTransportAdapter(core, loop)
                self._adapter_manager.attach_transport(session_id, transport)
                return transport

            return CoreSessionRunner(transport_factory=transport_factory)
        return self._runner_factory()

    def _wait_for_create_confirmation(self, session_id: str) -> None:
        with self._lock:
            confirm_event = self._create_confirm_events.get(session_id)
        if confirm_event is None:
            return

        try:
            confirmed = confirm_event.wait(timeout=self._create_confirm_timeout)
            with self._lock:
                info = self._sessions.get(session_id)
            if info is None:
                raise BackendError(
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                    details={"session_id": session_id},
                )
            if not confirmed:
                raise BackendError(
                    code="SESSION_START_TIMEOUT",
                    message="Timed out waiting for confirmed room_id",
                    details={"session_id": session_id},
                )
            if info.status == "failed":
                error = info.error if isinstance(info.error, dict) else {}
                code = error.get("code")
                message = error.get("message")
                if isinstance(code, str) and isinstance(message, str):
                    raise BackendError(
                        code=code,
                        message=message,
                        details={"session_id": session_id},
                    )
                raise BackendError(
                    code="SESSION_START_FAILED",
                    message="Session failed before room_id was confirmed",
                    details={"session_id": session_id},
                )
            if info.room_id is None:
                raise BackendError(
                    code="SESSION_ROOM_ID_UNCONFIRMED",
                    message="Session did not receive a confirmed room_id",
                    details={"session_id": session_id},
                )
        finally:
            with self._lock:
                self._create_confirm_events.pop(session_id, None)

    def _resolve_secondary_ip_adapter_config(
        self,
        adapter_config: Optional[AdapterConfig],
    ) -> tuple[Optional[AdapterConfig], Dict[str, Any]]:
        state: Dict[str, Any] = {
            "enabled": False,
            "fallback_used": False,
            "warning": None,
            "backend_admin": self.backend_has_ip_mutation_permission(),
            "bind_address": None,
            "interface_index": None,
            "interface_alias": None,
            "bind_mode": "loopback",
        }
        if (
            adapter_config is None
            or not adapter_config.enabled
            or adapter_config.secondary_ip_request is None
        ):
            return adapter_config, state

        request = adapter_config.secondary_ip_request
        decision = self._secondary_ip_manager.choose_adapter_bind_host(
            request.ip_address,
            default_bind_host=adapter_config.bind_host,
            interface_hint=request.interface_hint,
            prefix_length=request.prefix_length,
        )
        fallback_used = bool(getattr(decision, "fallback_used", False))
        enabled = bool(
            getattr(
                decision,
                "secondary_ip_enabled",
                decision.bind_host != FALLBACK_BIND_HOST and not fallback_used,
            )
        )
        warning = getattr(decision, "warning", None)
        state = {
            "enabled": enabled,
            "fallback_used": fallback_used,
            "warning": warning,
            "backend_admin": bool(getattr(decision, "backend_admin", False)),
            "bind_address": decision.bind_host if enabled else None,
            "interface_index": getattr(decision, "target_interface_index", None),
            "interface_alias": getattr(decision, "target_interface_alias", None),
            "bind_mode": getattr(decision, "bind_mode", "loopback"),
        }
        return dataclasses.replace(
            adapter_config,
            bind_host=decision.bind_host,
            secondary_ip_enabled=enabled,
            secondary_ip_warning=warning,
        ), state

    def _stop_adapter_if_ready(self, session_id: str, info: SessionInfo) -> None:
        prior_adapter_status = self._adapter_manager.snapshot(session_id)
        adapter_status = self._adapter_manager.stop(session_id)
        if adapter_status is not None:
            info.adapter_status = adapter_status
        if (
            prior_adapter_status is not None
            and prior_adapter_status.enabled
            and prior_adapter_status.status == ADAPTER_STATUS_READY
        ):
            self._emit_event(
                session_id,
                "adapter_stopped",
                "Adapter stopped",
                _adapter_status_event_data(adapter_status),
            )

    def _emit_event(
        self,
        session_id: str,
        event_type: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        now = time.time() if timestamp is None else timestamp
        should_start_adapter = False
        with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                raise BackendError(
                    code="SESSION_NOT_FOUND",
                    message=f"Session not found: {session_id}",
                    details={"session_id": session_id},
                )
            if (
                event_type in _ROOM_ID_CONFIRMING_EVENTS
                and data is not None
                and "room_id" in data
                and data["room_id"] is not None
            ):
                info.room_id = str(data["room_id"])
            status = _EVENT_STATUS.get(event_type)
            if status is not None:
                info.status = status
            self._apply_runner_event(info, event_type, data)
            info.updated_at = now
            self._logs.setdefault(session_id, []).append(SessionEvent(
                type=event_type,
                message=message,
                timestamp=now,
                data=data,
            ))
            should_start_adapter = (
                event_type == "session_running"
                and info.adapter_config is not None
                and info.adapter_config.enabled
            )
            confirm_event = self._create_confirm_events.get(session_id)
            should_confirm_create = (
                confirm_event is not None
                and event_type in ("room_created", "session_failed")
            )

        if should_start_adapter:
            self._start_adapter_after_session_running(session_id)
        if should_confirm_create:
            confirm_event.set()

    def _start_adapter_after_session_running(self, session_id: str) -> None:
        status = self._adapter_manager.start(session_id)
        if status is None:
            return
        with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                return
            info.adapter_status = status
            if status.enabled and status.bind_host and status.bind_port:
                info.adapter_host = status.bind_host
                info.adapter_port = status.bind_port
            info.updated_at = time.time()

        if status.status == ADAPTER_STATUS_READY:
            self._emit_event(
                session_id,
                "adapter_ready",
                (
                    "Adapter ready; Bundle includes UDP Broadcast/LAN "
                    "Discovery forwarding"
                    if status.adapter_type == ADAPTER_TYPE_BUNDLE
                    else "Adapter ready"
                ),
                _adapter_status_event_data(status),
            )
        elif status.status == ADAPTER_STATUS_ERROR:
            self._emit_event(
                session_id,
                "adapter_error",
                "Adapter error",
                _adapter_status_event_data(status),
            )
            if status.adapter_type == ADAPTER_TYPE_BUNDLE:
                error = status.error or {}
                self._emit_event(
                    session_id,
                    "session_failed",
                    error.get("message", "Bundle failed to start"),
                    {
                        "session_id": session_id,
                        "code": error.get("code", "BUNDLE_START_FAILED"),
                        "message": error.get(
                            "message",
                            "Bundle failed to start",
                        ),
                    },
                )

    def _apply_runner_event(
        self,
        info: SessionInfo,
        event_type: str,
        data: Optional[Dict[str, Any]],
    ) -> None:
        if not isinstance(data, dict):
            return

        if event_type == "session_failed":
            code = data.get("code")
            message = data.get("message")
            if isinstance(code, str) and isinstance(message, str):
                info.error = {"code": code, "message": message}

        if isinstance(data.get("player_id"), str):
            info.player_id = data["player_id"]
        if isinstance(data.get("protocol_version"), int):
            info.protocol_version = data["protocol_version"]
        elif event_type == "room_updated" and info.protocol_version is None:
            info.protocol_version = 2
        if isinstance(data.get("max_players"), int):
            info.max_players = data["max_players"]
        if isinstance(data.get("host_player_id"), str):
            info.host_player_id = data["host_player_id"]
        if isinstance(data.get("server_time"), (int, float)):
            info.server_time = float(data["server_time"])

        if "participants" in data:
            info.participants = _participant_dtos(data.get("participants"))
            if isinstance(data.get("participant_count"), int):
                info.participant_count = data["participant_count"]
            else:
                info.participant_count = len(info.participants)
            if info.host_player_id is None:
                for participant in info.participants:
                    if participant.is_host:
                        info.host_player_id = participant.player_id
                        break
        elif isinstance(data.get("participant_count"), int):
            info.participant_count = data["participant_count"]

        room_event = data.get("event")
        if isinstance(room_event, str):
            info.last_room_event = room_event
        elif event_type in {
            "room_updated",
            "participant_joined",
            "participant_left",
            "room_ready",
            "room_closed",
        }:
            info.last_room_event = event_type

        if event_type == "room_ready" or room_event == "room_ready":
            info.room_ready = True
        if event_type == "room_closed" or room_event == "room_closed":
            info.room_closed = True
        if event_type == "relay_ready":
            info.relay_ready = True
            if isinstance(data.get("relay_token_available"), bool):
                info.relay_token_available = data["relay_token_available"]
            elif data.get("relay_token"):
                info.relay_token_available = True
            if isinstance(data.get("relay_target_host"), str):
                info.relay_target_host = data["relay_target_host"]
            if isinstance(data.get("relay_target_port"), int):
                info.relay_target_port = data["relay_target_port"]

        peer_endpoint = data.get("peer_endpoint")
        if isinstance(peer_endpoint, dict):
            self._apply_peer_endpoint(info, peer_endpoint, source="peer_endpoint")
        elif "peer_ip" in data or "peer_port" in data:
            self._apply_peer_endpoint(
                info,
                {
                    "host": data.get("peer_ip"),
                    "port": data.get("peer_port"),
                },
                source="peer_info",
            )

    @staticmethod
    def _apply_peer_endpoint(
        info: SessionInfo,
        endpoint: Dict[str, Any],
        *,
        source: str,
    ) -> None:
        host = endpoint.get("host")
        if not isinstance(host, str):
            host = endpoint.get("ip")
        port = endpoint.get("port")
        if isinstance(host, str) and host.strip() and isinstance(port, int) and port > 0:
            info.peer_endpoint_host = host.strip()
            info.peer_endpoint_port = port
            endpoint_source = endpoint.get("source")
            info.peer_endpoint_source = (
                endpoint_source if isinstance(endpoint_source, str) else source
            )

    @staticmethod
    def _make_fake_bundle_transport(
        session_id: str,
        config: AdapterConfig,
    ) -> Any:
        local, _ = make_fake_pair()
        return local


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _participant_dtos(raw: Any) -> List[ParticipantDto]:
    if not isinstance(raw, list):
        return []
    participants: List[ParticipantDto] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not isinstance(item.get("player_id"), str):
            continue
        participants.append(ParticipantDto.from_dict(item))
    return participants


def _require_str(request: Dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Missing or invalid field: {key}",
            details={"field": key},
        )
    return value.strip()


def _require_port(request: Dict[str, Any], key: str, default: int) -> int:
    """Validate *key* is a valid port in 1-65535. Returns the int value."""
    raw = request.get(key, default)
    try:
        port = int(raw)
    except (ValueError, TypeError):
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field {key} must be a valid integer: {raw!r}",
            details={"field": key, "value": str(raw)},
        )
    if port < 1 or port > 65535:
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field {key} must be in range 1-65535, got {port}",
            details={"field": key, "value": port},
        )
    return port


def _require_present_port(request: Dict[str, Any], key: str) -> int:
    if key not in request:
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Missing or invalid field: {key}",
            details={"field": key},
        )
    return _require_port(request, key, default=0)


def _optional_request_port(request: Dict[str, Any], key: str) -> Optional[int]:
    if key not in request or request[key] is None:
        return None
    return _require_port(request, key, default=0)


def _request_bool(request: Dict[str, Any], key: str, default: bool) -> bool:
    value = request.get(key, default)
    if not isinstance(value, bool):
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field {key} must be a boolean",
            details={"field": key, "value": str(value)},
        )
    return value


def _require_port_or_zero(request: Dict[str, Any], key: str, default: int) -> int:
    """Validate *key* is 0 or a valid port in 1-65535. 0 means 'auto-assign'."""
    raw = request.get(key, default)
    try:
        port = int(raw)
    except (ValueError, TypeError):
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field {key} must be a valid integer: {raw!r}",
            details={"field": key, "value": str(raw)},
        )
    if port < 0 or port > 65535:
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"Field {key} must be in range 0-65535, got {port}",
            details={"field": key, "value": port},
        )
    return port


def _parse_adapter_config(request: Dict[str, Any]) -> Optional[AdapterConfig]:
    if "adapter_config" not in request:
        return None
    return AdapterConfig.from_dict(request["adapter_config"])


def _validate_create_adapter_target(
    adapter_config: Optional[AdapterConfig],
    game_server_port: Optional[int],
) -> None:
    if adapter_config is None or not adapter_config.enabled:
        return
    if adapter_config.target_port == 0:
        raise BackendError(
            code="INVALID_REQUEST",
            message="Create adapter_config.target_port must be a valid game port",
            details={"field": "adapter_config.target_port", "target_port": 0},
        )
    if game_server_port is None:
        return
    if adapter_config.target_port != game_server_port:
        raise BackendError(
            code="INVALID_REQUEST",
            message="Create adapter_config.target_port must match game_server_port",
            details={
                "field": "adapter_config.target_port",
                "game_server_port": game_server_port,
                "target_port": adapter_config.target_port,
            },
        )


def _validate_forward_adapter_target(
    adapter_config: Optional[AdapterConfig],
    for_join: bool = False,
) -> None:
    if adapter_config is None or not adapter_config.enabled:
        return
    if (
        for_join
        and adapter_config.adapter_type == ADAPTER_TYPE_LOCAL_UDP_BRIDGE
        and (adapter_config.target_port is None or adapter_config.target_port == 0)
    ):
        raise BackendError(
            code="INVALID_REQUEST",
            message=(
                "UDP Only Join requires adapter_config.target_port; "
                "no-target Join is only supported by Bundle mode"
            ),
            details={
                "field": "adapter_config.target_port",
                "adapter_type": ADAPTER_TYPE_LOCAL_UDP_BRIDGE,
                "target_port": adapter_config.target_port,
            },
        )
    if adapter_config.adapter_type not in {
        ADAPTER_TYPE_BUNDLE,
        ADAPTER_TYPE_TCP_FORWARD,
    }:
        return
    if (
        for_join
        and adapter_config.adapter_type == ADAPTER_TYPE_BUNDLE
        and adapter_config.target_port == 0
    ):
        return
    if adapter_config.target_port is None or adapter_config.target_port == 0:
        mode_name = (
            "Bundle"
            if adapter_config.adapter_type == ADAPTER_TYPE_BUNDLE
            else "TCP forward"
        )
        raise BackendError(
            code="INVALID_REQUEST",
            message=f"{mode_name} adapter_config.target_port is required",
            details={"field": "adapter_config.target_port"},
        )


def _adapter_status_event_data(status: Optional[AdapterStatus]) -> Dict[str, Any]:
    if status is None:
        return {}
    return status.to_dict()
