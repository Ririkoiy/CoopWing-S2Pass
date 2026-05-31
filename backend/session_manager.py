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
from typing import Any, Callable, Dict, List, Optional, Protocol

from backend.adapter_manager import AdapterManager
from backend.models import (
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_READY,
    AdapterConfig,
    AdapterStatus,
    BackendError,
    SessionEvent,
    SessionInfo,
    SessionStats,
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
    ) -> None:
        self._sessions: Dict[str, SessionInfo] = {}
        self._logs: Dict[str, List[SessionEvent]] = {}
        self._runners: Dict[str, SessionRunner] = {}
        self._adapter_manager = AdapterManager()
        self.runner_mode = resolve_runner_mode(runner_mode)
        self._custom_runner_factory = runner_factory
        self._runner_factory = runner_factory or make_runner_factory(self.runner_mode)
        self._lock = threading.Lock()
        self._fake_port_counter: int = 40000
        self._fake_port_lock = threading.Lock()
        self._create_confirm_timeout = max(0.0, float(create_confirm_timeout))
        self._create_confirm_events: Dict[str, threading.Event] = {}

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
        bind_port = _require_port_or_zero(request, "bind_port", default=0)
        adapter_config = _parse_adapter_config(request)
        _validate_create_adapter_target(adapter_config, game_server_port)

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
            created_at=now,
            updated_at=now,
            stats=SessionStats(),
            adapter_config=adapter_config,
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
        adapter_config = _parse_adapter_config(request)

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
            created_at=now,
            updated_at=now,
            stats=SessionStats(),
            adapter_config=adapter_config,
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
                "Adapter ready",
                _adapter_status_event_data(status),
            )
        elif status.status == ADAPTER_STATUS_ERROR:
            self._emit_event(
                session_id,
                "adapter_error",
                "Adapter error",
                _adapter_status_event_data(status),
            )


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

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


def _adapter_status_event_data(status: Optional[AdapterStatus]) -> Dict[str, Any]:
    if status is None:
        return {}
    return status.to_dict()
