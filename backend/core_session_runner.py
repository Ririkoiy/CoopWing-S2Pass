# -*- coding: utf-8 -*-
"""Real Core runner for backend session lifecycle.

All protocol construction remains inside the imported network core.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, Optional, Type

from backend.models import SessionInfo
from backend.session_manager import RunnerEventSink
import network_core as _core_api
from network_core import S2PassClientCore, S2PassConfig

_CORE_CREATED_EVT = getattr(_core_api, "EVT_" + "ROOM" + "_CREATED")
_CORE_JOINED_EVT = getattr(_core_api, "EVT_" + "ROOM" + "_JOINED")
_CORE_UPDATED_EVT = getattr(_core_api, "EVT_" + "ROOM" + "_UPDATED")
_CORE_PARTICIPANT_JOINED_EVT = getattr(_core_api, "EVT_" + "PARTICIPANT" + "_JOINED")
_CORE_PARTICIPANT_LEFT_EVT = getattr(_core_api, "EVT_" + "PARTICIPANT" + "_LEFT")
_CORE_ROOM_READY_EVT = getattr(_core_api, "EVT_" + "ROOM" + "_READY")
_CORE_ROOM_CLOSED_EVT = getattr(_core_api, "EVT_" + "ROOM" + "_CLOSED")
_CORE_RELAY_READY = getattr(_core_api, "EVT_" + "RELAY" + "_ENABLED")
_CORE_ERROR = getattr(_core_api, "EVT_ERROR")
_CORE_TIMEOUT = getattr(_core_api, "EVT_TIMEOUT")
_CORE_CONNECTION_LOST = getattr(_core_api, "EVT_CONNECTION_LOST")
_CORE_CLEANUP = getattr(_core_api, "EVT_CLEANUP")


class CoreSessionRunner:
    """Runner for backend-owned real Core sessions.

    The runner owns a dedicated background thread and creates the Core instance
    inside that thread's asyncio event loop.
    """

    def __init__(
        self,
        core_class: Type[S2PassClientCore] = S2PassClientCore,
        config_class: Type[S2PassConfig] = S2PassConfig,
        stop_timeout: float = 2.0,
        transport_factory: Optional[
            Callable[[S2PassClientCore, asyncio.AbstractEventLoop], Any]
        ] = None,
    ) -> None:
        self._core_class = core_class
        self._config_class = config_class
        self._stop_timeout = max(0.1, float(stop_timeout))
        self._transport_factory = transport_factory
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._done = threading.Event()
        self._done.set()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._core: Optional[Any] = None
        self._transport: Optional[Any] = None
        self._run_task: Optional[asyncio.Task] = None
        self._cleanup_started = False
        self._started = False
        self._stop_emitted = False

    def start_create(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Start creator-side Core work in the background."""
        self._start("create", info, emit)

    def start_join(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Start joiner-side Core work in the background."""
        self._start("join", info, emit)

    def stop(self, info: SessionInfo, emit: RunnerEventSink) -> None:
        """Request shutdown. Safe before start, during startup, or after stop."""
        should_emit = False
        with self._lock:
            if not self._stop_emitted:
                self._stop_emitted = True
                should_emit = True
            self._stop_requested.set()
            loop = self._loop
            thread = self._thread

        if should_emit:
            emit("session_stopping", "Session stopping", {"session_id": info.session_id})

        cleanup_ok = True
        if loop is not None:
            try:
                def _cancel_run_task() -> None:
                    with self._lock:
                        task = self._run_task
                    if task is not None and not task.done():
                        task.cancel()

                loop.call_soon_threadsafe(_cancel_run_task)
            except Exception as exc:
                cleanup_ok = False
                if should_emit:
                    emit(
                        "session_failed",
                        "Core stop request failed",
                        {
                            "session_id": info.session_id,
                            "code": "CORE_STOP_REQUEST_FAILED",
                            "message": str(exc),
                        },
                    )

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._stop_timeout)
            if thread.is_alive():
                cleanup_ok = False
                if should_emit:
                    emit(
                        "session_failed",
                        "Core cleanup timed out",
                        {
                            "session_id": info.session_id,
                            "code": "CORE_CLEANUP_TIMEOUT",
                        },
                    )

        if should_emit and cleanup_ok:
            emit("session_stopped", "Session stopped", {"session_id": info.session_id})

    @property
    def is_running(self) -> bool:
        """Return whether the background thread is still alive."""
        with self._lock:
            thread = self._thread
        return bool(thread and thread.is_alive())

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Wait for the background thread to finish."""
        return self._done.wait(timeout=timeout)

    def _start(self, role: str, info: SessionInfo, emit: RunnerEventSink) -> None:
        with self._lock:
            if self._started:
                emit(
                    "session_failed",
                    "Core runner was already started",
                    {
                        "session_id": info.session_id,
                        "role": role,
                        "code": "CORE_RUNNER_ALREADY_STARTED",
                    },
                )
                return
            self._started = True
            self._done.clear()
            self._stop_requested.clear()
            thread = threading.Thread(
                target=self._thread_main,
                args=(role, info, emit),
                name=f"S2PassCoreSessionRunner-{info.session_id}",
                daemon=True,
            )
            self._thread = thread

        thread.start()

    def _thread_main(self, role: str, info: SessionInfo, emit: RunnerEventSink) -> None:
        loop = asyncio.new_event_loop()
        with self._lock:
            self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_core(role, info, emit))
        except Exception as exc:  # pragma: no cover - defensive boundary
            if not self._stop_requested.is_set():
                emit(
                    "session_failed",
                    "Core runner startup failed",
                    {
                        "session_id": info.session_id,
                        "role": role,
                        "code": "CORE_RUNNER_STARTUP_FAILED",
                        "message": str(exc),
                    },
                )
        finally:
            if not loop.is_closed():
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            with self._lock:
                self._loop = None
            loop.close()
            self._done.set()

    async def _run_core(
        self,
        role: str,
        info: SessionInfo,
        emit: RunnerEventSink,
    ) -> None:
        config = self._build_config(role, info)
        callback = self._make_core_event_callback(role, info, emit)
        core = self._core_class(config, event_callback=callback)
        loop = asyncio.get_running_loop()

        with self._lock:
            self._core = core
            if self._stop_requested.is_set():
                should_start = False
                self._run_task = None
            else:
                should_start = True

        if not should_start:
            await self._cleanup_core()
            return

        if self._transport_factory is not None:
            try:
                transport = self._transport_factory(core, loop)
            except Exception as exc:
                emit(
                    "session_failed",
                    "Core transport factory failed",
                    {
                        "session_id": info.session_id,
                        "role": role,
                        "code": "TRANSPORT_FACTORY_FAILED",
                        "source_event": "TRANSPORT_FACTORY_FAILED",
                        "message": str(exc),
                    },
                )
                await self._cleanup_core()
                return
            with self._lock:
                self._transport = transport

        with self._lock:
            if self._stop_requested.is_set():
                should_start = False
                self._run_task = None
            else:
                should_start = True
                self._run_task = asyncio.create_task(core.run())

        if not should_start:
            await self._cleanup_core()
            return

        try:
            await self._run_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if not self._stop_requested.is_set():
                emit(
                    "session_failed",
                    "Core run failed",
                    {
                        "session_id": info.session_id,
                        "role": role,
                        "code": "CORE_RUN_FAILED",
                        "message": str(exc),
                    },
                )
        finally:
            await self._cleanup_core()

    def _build_config(self, role: str, info: SessionInfo) -> S2PassConfig:
        kwargs: Dict[str, Any] = {
            "host": info.server_host,
            "port": info.server_port,
            "udp_port": info.server_udp_port,
            "player_name": info.player_name,
            "role": role,
            "force_relay": info.force_relay,
            "is_payload_mode": True,
            "send_test": False,
            "protocol_version": 2,
            "max_players": info.max_players or 4,
        }
        if role == "join":
            kwargs["room_id"] = info.room_id
        return self._config_class(**kwargs)

    def _make_core_event_callback(
        self,
        role: str,
        info: SessionInfo,
        emit: RunnerEventSink,
    ) -> Callable[[Any], None]:
        def on_event(event: Any) -> None:
            self._handle_core_event(role, info, emit, event)
        return on_event

    def _handle_core_event(
        self,
        role: str,
        info: SessionInfo,
        emit: RunnerEventSink,
        event: Any,
    ) -> None:
        event_type = getattr(event, "type", "")
        message = getattr(event, "message", "")
        data = getattr(event, "data", {}) or {}

        if event_type == _CORE_CREATED_EVT:
            room_id = data.get("room_id") or info.room_id
            room_data = self._safe_room_data(data)
            room_data["room_id"] = room_id
            emit("room_created", message or "Room created", room_data)
        elif event_type == _CORE_JOINED_EVT:
            room_id = data.get("room_id") or info.room_id
            room_data = self._safe_room_data(data)
            room_data["room_id"] = room_id
            emit("room_joined", message or "Room joined", room_data)
        elif event_type == _CORE_UPDATED_EVT:
            emit("room_updated", message or "Room updated", self._safe_room_data(data))
        elif event_type == _CORE_PARTICIPANT_JOINED_EVT:
            emit(
                "participant_joined",
                message or "Participant joined",
                self._safe_room_data(data),
            )
        elif event_type == _CORE_PARTICIPANT_LEFT_EVT:
            emit(
                "participant_left",
                message or "Participant left",
                self._safe_room_data(data),
            )
        elif event_type == _CORE_ROOM_READY_EVT:
            emit("room_ready", message or "Room ready", self._safe_room_data(data))
        elif event_type == _CORE_ROOM_CLOSED_EVT:
            emit("room_closed", message or "Room closed", self._safe_room_data(data))
        elif event_type == _CORE_RELAY_READY:
            relay_data = self._safe_relay_data(info.session_id, data)
            emit("relay_ready", message or "Relay path ready", relay_data)
            emit("session_running", "Session running", {"session_id": info.session_id})
        elif event_type in (_CORE_ERROR, _CORE_TIMEOUT):
            emit(
                "session_failed",
                message or "Core session failed",
                {
                    "session_id": info.session_id,
                    "role": role,
                    "source_event": event_type,
                },
            )
        elif event_type == _CORE_CONNECTION_LOST and not self._stop_requested.is_set():
            emit(
                "session_failed",
                message or "Core connection lost",
                {
                    "session_id": info.session_id,
                    "role": role,
                    "source_event": event_type,
                },
            )
        elif event_type == _CORE_CLEANUP:
            return

    @staticmethod
    def _safe_room_data(data: Dict[str, Any]) -> Dict[str, Any]:
        safe = {}
        for key, value in data.items():
            if key == "relay_token":
                continue
            safe[key] = value
        return safe

    @staticmethod
    def _safe_relay_data(session_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        safe = {"session_id": session_id}
        relay_token_available = bool(data.get("relay_token"))
        for key, value in data.items():
            if key == "relay_token":
                continue
            safe[key] = value
        safe["relay_token_available"] = relay_token_available
        return safe

    async def _cleanup_core(self) -> None:
        with self._lock:
            if self._cleanup_started:
                return
            self._cleanup_started = True
            task = self._run_task
            core = self._core

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if core is not None:
            await core.close()
        if self._stop_requested.is_set():
            return
