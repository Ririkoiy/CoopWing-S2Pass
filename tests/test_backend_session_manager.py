# -*- coding: utf-8 -*-
"""Tests for backend.session_manager (fake sessions, no Core)."""
from __future__ import annotations

import os
import threading
import time
import unittest
from unittest import mock

from adapters.transport import FakePairTransport
from backend.models import (
    ADAPTER_STATUS_READY,
    AdapterCounters,
    AdapterStatus,
    BackendError,
)
from backend.session_manager import (
    FakeSessionRunner,
    RUNNER_MODE_ENV,
    SessionManager,
)


class FakeCoreHandle:
    pass


class FakeLoopHandle:
    pass


class FakeCoreTransport(FakePairTransport):
    instances = []

    def __init__(self, core, loop):
        super().__init__()
        self.core = core
        self.loop = loop
        self.close_count = 0
        FakeCoreTransport.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    def close(self):
        self.close_count += 1


class RealCoreRunnerStub:
    instances = []

    def __init__(self, transport_factory=None):
        self.transport_factory = transport_factory
        self.transport = None
        self.status_after_relay_ready = None
        self.calls = []
        RealCoreRunnerStub.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []

    def _prepare_transport(self):
        if self.transport_factory is not None:
            self.transport = self.transport_factory(FakeCoreHandle(), FakeLoopHandle())

    def start_create(self, info, emit):
        self.calls.append(("start_create", info.session_id))
        self._prepare_transport()
        if info.room_id is None:
            info.room_id = "ABC234"
        emit("room_created", f"Room {info.room_id} created", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        self.status_after_relay_ready = (
            info.adapter_status.status if info.adapter_status is not None else None
        )
        emit("session_running", "Session running", {"session_id": info.session_id})

    def start_join(self, info, emit):
        self.calls.append(("start_join", info.session_id))
        self._prepare_transport()
        emit("room_joined", f"Joined room {info.room_id}", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        self.status_after_relay_ready = (
            info.adapter_status.status if info.adapter_status is not None else None
        )
        emit("session_running", "Session running", {"session_id": info.session_id})

    def stop(self, info, emit):
        self.calls.append(("stop", info.session_id))
        emit("session_stopping", "Session stopping", {"session_id": info.session_id})
        emit("session_stopped", "Session stopped", {"session_id": info.session_id})


def _dict_contains_key(data, key):
    if isinstance(data, dict):
        if key in data:
            return True
        return any(_dict_contains_key(value, key) for value in data.values())
    if isinstance(data, list):
        return any(_dict_contains_key(value, key) for value in data)
    return False


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def start_create(self, info, emit):
        self.calls.append(("start_create", info.session_id))
        if info.room_id is None:
            info.room_id = "ABC234"
        emit("room_created", f"Room {info.room_id} created", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        emit("session_running", "Session running", {"session_id": info.session_id})

    def start_join(self, info, emit):
        self.calls.append(("start_join", info.session_id))
        emit("room_joined", f"Joined room {info.room_id}", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        emit("session_running", "Session running", {"session_id": info.session_id})

    def stop(self, info, emit):
        self.calls.append(("stop", info.session_id))
        emit("session_stopping", "Session stopping", {"session_id": info.session_id})
        emit("session_stopped", "Session stopped", {"session_id": info.session_id})


class DelayedConfirmedCreateRunner:
    confirmed_room_id = "SRV789"

    def __init__(self):
        self.calls = []
        self.room_id_at_start = None
        self.thread = None

    def start_create(self, info, emit):
        self.calls.append(("start_create", info.session_id))
        self.room_id_at_start = info.room_id

        def run():
            time.sleep(0.05)
            emit(
                "room_created",
                f"Room {self.confirmed_room_id} created",
                {"room_id": self.confirmed_room_id},
            )
            emit("relay_ready", "Relay path ready", {"room_id": self.confirmed_room_id})
            emit("session_running", "Session running", {"session_id": info.session_id})

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def start_join(self, info, emit):
        self.calls.append(("start_join", info.session_id))
        emit("room_joined", f"Joined room {info.room_id}", {"room_id": info.room_id})
        emit("relay_ready", "Relay path ready", {"room_id": info.room_id})
        emit("session_running", "Session running", {"session_id": info.session_id})

    def stop(self, info, emit):
        self.calls.append(("stop", info.session_id))
        emit("session_stopping", "Session stopping", {"session_id": info.session_id})
        emit("session_stopped", "Session stopped", {"session_id": info.session_id})


class RoomNotFoundJoinRunner(RecordingRunner):
    def start_join(self, info, emit):
        self.calls.append(("start_join", info.session_id))
        emit(
            "session_failed",
            "Room not found",
            {
                "session_id": info.session_id,
                "role": "join",
                "source_event": "ERROR",
                "code": 1001,
            },
        )


class SnapshotAdapterManager:
    def snapshot(self, session_id):
        return AdapterStatus(
            enabled=True,
            status=ADAPTER_STATUS_READY,
            counters=AdapterCounters(
                packets_from_game=1,
                packets_to_transport=2,
                packets_from_transport=3,
                packets_to_game=4,
                bytes_from_game=10,
                bytes_to_transport=20,
                bytes_from_transport=30,
                bytes_to_game=40,
            ),
        )


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        self.mgr = SessionManager()

    # ------------------------------------------------------------------
    # runner mode
    # ------------------------------------------------------------------

    def test_default_runner_mode_is_fake(self):
        mgr = SessionManager()

        self.assertEqual(mgr.runner_mode, "fake")
        self.assertIsInstance(mgr._runner_factory(), FakeSessionRunner)

    def test_environment_real_core_mode_selects_core_runner_factory(self):
        from backend.core_session_runner import CoreSessionRunner

        with mock.patch.dict(os.environ, {RUNNER_MODE_ENV: "real_core"}):
            mgr = SessionManager()

        self.assertEqual(mgr.runner_mode, "real_core")
        self.assertIsInstance(mgr._runner_factory(), CoreSessionRunner)

    def test_invalid_runner_mode_fails_clearly(self):
        with self.assertRaises(ValueError) as ctx:
            SessionManager(runner_mode="not_a_mode")

        self.assertIn("Invalid backend runner mode", str(ctx.exception))

    # ------------------------------------------------------------------
    # real_core adapter wiring
    # ------------------------------------------------------------------

    def test_real_core_without_adapter_config_does_not_create_transport_or_status(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                })

        self.assertEqual(info.status, "running")
        self.assertIsNone(info.adapter_status)
        self.assertNotIn("adapter_status", info.to_dict())
        self.assertEqual(FakeCoreTransport.instances, [])

    def test_real_core_disabled_adapter_config_does_not_create_transport(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                    "adapter_config": {"enabled": False},
                })

        self.assertEqual(info.status, "running")
        self.assertEqual(info.adapter_status.status, "disabled")
        self.assertEqual(FakeCoreTransport.instances, [])

    def test_real_core_enabled_adapter_starts_after_session_running(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                    "adapter_config": {
                        "enabled": True,
                        "bind_port": 0,
                        "target_port": 40200,
                    },
                })
                try:
                    logs = mgr.get_logs(info.session_id)
                finally:
                    mgr.stop_session(info.session_id)

        session_runner = RealCoreRunnerStub.instances[-1]
        self.assertEqual(session_runner.status_after_relay_ready, "stopped")
        self.assertEqual(info.status, "stopped")
        self.assertEqual(info.adapter_status.status, "stopped")
        self.assertEqual(len(FakeCoreTransport.instances), 1)
        types = [event.type for event in logs]
        self.assertLess(types.index("session_running"), types.index("adapter_ready"))

    def test_real_core_adapter_bind_failure_leaves_core_session_running(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                    "adapter_config": {
                        "enabled": True,
                        "bind_host": "203.0.113.1",
                        "bind_port": 40100,
                    },
                })
                logs = mgr.get_logs(info.session_id)

        self.assertEqual(info.status, "running")
        self.assertEqual(info.adapter_status.status, "error")
        self.assertEqual(info.adapter_status.error["code"], "ADAPTER_BIND_FAILED")
        self.assertEqual(FakeCoreTransport.instances[0].close_count, 1)
        types = [event.type for event in logs]
        self.assertLess(types.index("session_running"), types.index("adapter_error"))

    def test_stop_real_core_adapter_stops_adapter_before_runner(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                    "adapter_config": {
                        "enabled": True,
                        "bind_port": 0,
                    },
                })
                transport = FakeCoreTransport.instances[0]
                stopped = mgr.stop_session(info.session_id)
                logs = mgr.get_logs(info.session_id)

        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(transport.close_count, 1)
        runner = RealCoreRunnerStub.instances[-1]
        self.assertEqual(runner.calls, [
            ("start_create", info.session_id),
            ("stop", info.session_id),
        ])
        types = [event.type for event in logs]
        self.assertLess(types.index("adapter_stopped"), types.index("session_stopping"))

    def test_real_core_adapter_status_and_logs_do_not_expose_live_handles(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.join_session({
                    "server_host": "127.0.0.1",
                    "room_id": "ABC234",
                    "player_name": "JoinerB",
                    "adapter_config": {
                        "enabled": True,
                        "bind_port": 0,
                    },
                })
                payload = {
                    "status": info.to_dict(),
                    "logs": [event.to_dict() for event in mgr.get_logs(info.session_id)],
                }
                mgr.stop_session(info.session_id)

        for key in ("relay_token", "core", "loop", "transport", "socket", "thread", "task"):
            self.assertFalse(_dict_contains_key(payload, key), key)

    # ------------------------------------------------------------------
    # create_session
    # ------------------------------------------------------------------

    def test_create_session_role_is_create(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(info.role, "create")

    def test_create_session_room_id_length_6(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(len(info.room_id), 6)

    def test_create_session_room_id_no_ambiguous_chars(self):
        """Room ID must not contain I, O, 0, 1 for readability."""
        ambiguous = set("IO01")
        for _ in range(20):
            info = self.mgr.create_session({
                "server_host": "192.168.1.10",
                "player_name": "CreatorA",
            })
            self.assertIsNotNone(info.room_id)
            self.assertFalse(
                ambiguous.intersection(info.room_id),
                f"Room ID {info.room_id} contains ambiguous characters",
            )

    def test_create_session_status_reaches_running(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(info.status, "running")

    def test_create_session_waits_for_confirmed_room_id(self):
        runners = []

        def factory():
            runner = DelayedConfirmedCreateRunner()
            runners.append(runner)
            return runner

        mgr = SessionManager(
            runner_factory=factory,
            create_confirm_timeout=1.0,
        )

        info = mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        logs = mgr.get_logs(info.session_id)

        self.assertIsNone(runners[0].room_id_at_start)
        self.assertEqual(info.room_id, DelayedConfirmedCreateRunner.confirmed_room_id)
        self.assertEqual(mgr.get_session(info.session_id).room_id, info.room_id)
        room_created = [e for e in logs if e.type == "room_created"][0]
        self.assertEqual(room_created.data["room_id"], info.room_id)

    def test_create_session_starting_event_does_not_expose_room_id(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        logs = self.mgr.get_logs(info.session_id)
        starting = [e for e in logs if e.type == "session_starting"][0]

        self.assertNotIn("room_id", starting.to_dict()["data"])

    def test_create_room_created_event_matches_session_room_id(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        logs = self.mgr.get_logs(info.session_id)
        room_created = [e for e in logs if e.type == "room_created"][0]

        self.assertEqual(room_created.data["room_id"], info.room_id)

    def test_create_session_session_id_format(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertTrue(info.session_id.startswith("s_"))
        self.assertEqual(len(info.session_id), 14)  # s_ + 12 hex

    def test_create_session_adapter_defaults(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(info.adapter_host, "127.0.0.1")
        self.assertGreater(info.adapter_port, 0)

    def test_create_session_uses_bind_params(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "bind_host": "127.0.0.2",
            "bind_port": 40099,
        })
        self.assertEqual(info.adapter_host, "127.0.0.2")
        self.assertEqual(info.adapter_port, 40099)

    def test_create_session_bind_port_zero_gets_nonzero_fake(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "bind_port": 0,
        })
        self.assertGreater(info.adapter_port, 0)

    def test_create_session_has_stats(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertIsNotNone(info.stats)
        self.assertEqual(info.stats.packets_from_game, 0)
        self.assertFalse(info.stats.has_error)

    def test_create_without_adapter_config_omits_adapter_status(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertIsNone(info.adapter_config)
        self.assertNotIn("adapter_status", info.to_dict())

    def test_create_with_disabled_adapter_config_serializes_disabled(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": False,
            },
        })
        self.assertIsNotNone(info.adapter_config)
        self.assertFalse(info.adapter_config.enabled)
        self.assertEqual(info.to_dict()["adapter_status"], {
            "enabled": False,
            "status": "disabled",
        })

    def test_create_with_enabled_adapter_config_stores_passive_status(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_host": "127.0.0.1",
                "bind_port": 40100,
                "target_host": "127.0.0.1",
                "target_port": 40200,
            },
        })
        self.assertTrue(info.adapter_config.enabled)
        d = info.to_dict()["adapter_status"]
        self.assertTrue(d["enabled"])
        self.assertEqual(d["status"], "stopped")
        self.assertEqual(d["bind_port"], 40100)
        self.assertEqual(d["target_port"], 40200)
        self.assertEqual(d["counters"], {
            "packets_from_game": 0,
            "packets_to_transport": 0,
            "packets_from_transport": 0,
            "packets_to_game": 0,
            "bytes_from_game": 0,
            "bytes_to_transport": 0,
            "bytes_from_transport": 0,
            "bytes_to_game": 0,
        })
        self.assertIsNone(d["error"])

    # ------------------------------------------------------------------
    # create_session validation
    # ------------------------------------------------------------------

    def test_create_session_missing_server_host(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({"player_name": "X"})
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_missing_player_name(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({"server_host": "1.2.3.4"})
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_empty_player_name(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({"server_host": "1.2.3.4", "player_name": "   "})
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_server_port_negative(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "server_port": -1,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_server_port_zero(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "server_port": 0,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_server_port_too_high(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "server_port": 99999,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_server_port_not_int(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "server_port": "abc",
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_server_udp_port(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "server_udp_port": -5,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_bind_port_negative(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "bind_port": -1,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_invalid_bind_port_too_high(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "bind_port": 99999,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_session_bind_port_zero_is_valid(self):
        """bind_port=0 is allowed (means auto-assign)."""
        info = self.mgr.create_session({
            "server_host": "1.2.3.4",
            "player_name": "X",
            "bind_port": 0,
        })
        self.assertGreater(info.adapter_port, 0)

    def test_create_invalid_adapter_config_type(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "adapter_config": "not an object",
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_invalid_adapter_enabled_type(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "adapter_config": {"enabled": "true"},
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_unknown_adapter_type_rejected(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "adapter_config": {"adapter_type": "other"},
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_create_invalid_adapter_port_rejected(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.create_session({
                "server_host": "1.2.3.4",
                "player_name": "X",
                "adapter_config": {"bind_port": 65536},
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    # ------------------------------------------------------------------
    # join_session
    # ------------------------------------------------------------------

    def test_join_session_role_is_join(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(info.role, "join")

    def test_join_session_uses_provided_room_id(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "XYZ789",
            "player_name": "JoinerB",
        })
        self.assertEqual(info.room_id, "XYZ789")

    def test_join_using_create_response_room_id_succeeds_in_fake_mode(self):
        created = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })

        joined = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": created.room_id,
            "player_name": "JoinerB",
        })

        self.assertEqual(joined.room_id, created.room_id)
        self.assertEqual(joined.status, "running")

    def test_join_session_starting_event_does_not_expose_room_id(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "XYZ789",
            "player_name": "JoinerB",
        })
        logs = self.mgr.get_logs(info.session_id)
        starting = [e for e in logs if e.type == "session_starting"][0]

        self.assertNotIn("room_id", starting.to_dict()["data"])

    def test_join_room_not_found_error_code_is_preserved(self):
        mgr = SessionManager(runner_factory=RoomNotFoundJoinRunner)

        info = mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "MISSING",
            "player_name": "JoinerB",
        })
        logs = mgr.get_logs(info.session_id)
        failure = [e for e in logs if e.type == "session_failed"][0]

        self.assertEqual(info.status, "failed")
        self.assertEqual(failure.data["code"], 1001)
        self.assertEqual(failure.message, "Room not found")

    def test_join_session_requires_room_id(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.join_session({
                "server_host": "192.168.1.10",
                "player_name": "JoinerB",
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_join_session_default_game_server_host(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(info.game_server_host, "127.0.0.1")

    def test_join_session_omits_default_game_server_port(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertIsNone(info.game_server_port)
        self.assertNotIn("game_server_port", info.to_dict())

    def test_join_session_custom_game_server(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
            "game_server_host": "10.0.0.1",
            "game_server_port": 50000,
        })
        self.assertEqual(info.game_server_host, "10.0.0.1")
        self.assertEqual(info.game_server_port, 50000)

    def test_join_session_status_reaches_running(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(info.status, "running")

    def test_join_session_has_stats(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertIsNotNone(info.stats)

    def test_join_without_adapter_config_omits_adapter_status(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertIsNone(info.adapter_config)
        self.assertNotIn("adapter_status", info.to_dict())

    def test_join_with_disabled_adapter_config_serializes_disabled(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
            "adapter_config": {
                "enabled": False,
            },
        })
        self.assertIsNotNone(info.adapter_config)
        self.assertEqual(info.to_dict()["adapter_status"], {
            "enabled": False,
            "status": "disabled",
        })

    def test_join_with_enabled_adapter_config_stores_passive_status(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
            "adapter_config": {
                "enabled": True,
                "bind_port": 40101,
            },
        })
        d = info.to_dict()["adapter_status"]
        self.assertTrue(d["enabled"])
        self.assertEqual(d["status"], "stopped")
        self.assertEqual(d["bind_port"], 40101)
        self.assertNotIn("target_port", d)

    def test_join_session_invalid_game_server_port_negative(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.join_session({
                "server_host": "1.2.3.4",
                "room_id": "ABC234",
                "player_name": "X",
                "game_server_port": -1,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_join_session_invalid_game_server_port_zero(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.join_session({
                "server_host": "1.2.3.4",
                "room_id": "ABC234",
                "player_name": "X",
                "game_server_port": 0,
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_join_session_invalid_game_server_port_not_int(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.join_session({
                "server_host": "1.2.3.4",
                "room_id": "ABC234",
                "player_name": "X",
                "game_server_port": "xyz",
            })
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    # ------------------------------------------------------------------
    # stop_session
    # ------------------------------------------------------------------

    def test_stop_session_transitions_to_stopped(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        stopped = self.mgr.stop_session(info.session_id)
        self.assertEqual(stopped.status, "stopped")

    def test_stop_session_updates_timestamp(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        original_ts = info.updated_at
        stopped = self.mgr.stop_session(info.session_id)
        self.assertGreaterEqual(stopped.updated_at, original_ts)

    def test_stop_enabled_stopped_adapter_session_remains_normal_stop(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": True,
            },
        })

        stopped = self.mgr.stop_session(info.session_id)

        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(stopped.adapter_status.status, "stopped")
        self.assertEqual(stopped.to_dict()["adapter_status"]["status"], "stopped")

    def test_stop_already_stopped_returns_info_without_duplicate_events(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.mgr.stop_session(info.session_id)
        before = [event.type for event in self.mgr.get_logs(info.session_id)]

        stopped = self.mgr.stop_session(info.session_id)
        after = [event.type for event in self.mgr.get_logs(info.session_id)]

        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(after, before)
        self.assertEqual(after.count("session_stopping"), 1)
        self.assertEqual(after.count("session_stopped"), 1)

    def test_stop_failed_session_returns_info(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        info.status = "failed"

        failed = self.mgr.stop_session(info.session_id)

        self.assertEqual(failed.status, "failed")

    def test_stop_unknown_session_raises_session_not_found(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.stop_session("s_nonexistent")
        self.assertEqual(ctx.exception.code, "SESSION_NOT_FOUND")

    def test_stop_failed_session_stops_ready_adapter_once(self):
        RealCoreRunnerStub.reset()
        FakeCoreTransport.reset()
        with mock.patch("backend.core_session_runner.CoreSessionRunner", RealCoreRunnerStub):
            with mock.patch(
                "adapters.core_transport_adapter.CoreTransportAdapter",
                FakeCoreTransport,
            ):
                mgr = SessionManager(runner_mode="real_core")
                info = mgr.create_session({
                    "server_host": "127.0.0.1",
                    "player_name": "CreatorA",
                    "adapter_config": {
                        "enabled": True,
                        "bind_port": 0,
                    },
                })
                info.status = "failed"

                first = mgr.stop_session(info.session_id)
                second = mgr.stop_session(info.session_id)
                logs = mgr.get_logs(info.session_id)
        types = [event.type for event in logs]
        runner = RealCoreRunnerStub.instances[-1]

        self.assertEqual(first.status, "failed")
        self.assertEqual(second.status, "failed")
        self.assertEqual(info.adapter_status.status, "stopped")
        self.assertEqual(FakeCoreTransport.instances[0].close_count, 1)
        self.assertEqual(runner.calls, [("start_create", info.session_id)])
        self.assertEqual(types.count("adapter_stopped"), 1)
        self.assertEqual(types.count("session_stopping"), 0)
        self.assertEqual(types.count("session_stopped"), 0)

    # ------------------------------------------------------------------
    # get_session
    # ------------------------------------------------------------------

    def test_get_unknown_session_raises(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.get_session("s_nonexistent")
        self.assertEqual(ctx.exception.code, "SESSION_NOT_FOUND")

    def test_get_session_returns_info(self):
        created = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        fetched = self.mgr.get_session(created.session_id)
        self.assertEqual(fetched.session_id, created.session_id)
        self.assertEqual(fetched.role, "create")

    def test_get_session_refreshes_adapter_snapshot(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.mgr._adapter_manager = SnapshotAdapterManager()

        fetched = self.mgr.get_session(info.session_id)

        self.assertEqual(
            fetched.adapter_status.counters.to_dict(),
            {
                "packets_from_game": 1,
                "packets_to_transport": 2,
                "packets_from_transport": 3,
                "packets_to_game": 4,
                "bytes_from_game": 10,
                "bytes_to_transport": 20,
                "bytes_from_transport": 30,
                "bytes_to_game": 40,
            },
        )

    # ------------------------------------------------------------------
    # list_sessions
    # ------------------------------------------------------------------

    def test_list_sessions_empty_initially(self):
        self.assertEqual(self.mgr.list_sessions(), [])

    def test_list_sessions_returns_created(self):
        info1 = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        info2 = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "Bob",
        })
        sessions = self.mgr.list_sessions()
        self.assertEqual(len(sessions), 2)
        ids = {s.session_id for s in sessions}
        self.assertIn(info1.session_id, ids)
        self.assertIn(info2.session_id, ids)

    def test_list_sessions_refreshes_adapter_snapshots(self):
        self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        self.mgr._adapter_manager = SnapshotAdapterManager()

        sessions = self.mgr.list_sessions()

        self.assertEqual(sessions[0].adapter_status.counters.packets_to_game, 4)

    # ------------------------------------------------------------------
    # create vs join path separation
    # ------------------------------------------------------------------

    def test_create_and_join_have_different_paths(self):
        """Create goes through room_created; join through room_joined."""
        mgr = SessionManager()
        # create — use internal state to see the path
        c = mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        j = mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "Bob",
        })
        self.assertEqual(c.role, "create")
        self.assertEqual(j.role, "join")
        self.assertEqual(c.status, "running")
        self.assertEqual(j.status, "running")
        # Both exist independently
        self.assertNotEqual(c.session_id, j.session_id)
        self.assertNotEqual(c.room_id, j.room_id)

    # ------------------------------------------------------------------
    # fake adapter port allocation
    # ------------------------------------------------------------------

    def test_multiple_fake_sessions_get_distinct_adapter_ports(self):
        """Default adapter ports must be unique across sessions."""
        ports = set()
        for _ in range(5):
            info = self.mgr.create_session({
                "server_host": "192.168.1.10",
                "player_name": f"Player{_}",
            })
            ports.add(info.adapter_port)
        for _ in range(3):
            info = self.mgr.join_session({
                "server_host": "192.168.1.10",
                "room_id": "ABC234",
                "player_name": f"Joiner{_}",
            })
            ports.add(info.adapter_port)
        self.assertEqual(len(ports), 8, f"Expected 8 unique fake ports, got {len(ports)}")

    def test_explicit_bind_port_respected(self):
        """Explicit bind_port must override the fake allocator."""
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "X",
            "bind_port": 40123,
        })
        self.assertEqual(info.adapter_port, 40123)

    # ------------------------------------------------------------------
    # get_logs
    # ------------------------------------------------------------------

    def test_create_session_logs_contain_expected_events(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        logs = self.mgr.get_logs(info.session_id)
        types = [e.type for e in logs]
        self.assertIn("session_created", types)
        self.assertIn("session_starting", types)
        self.assertIn("room_created", types)
        self.assertIn("relay_ready", types)
        self.assertIn("session_running", types)

    def test_join_session_logs_contain_expected_events(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        logs = self.mgr.get_logs(info.session_id)
        types = [e.type for e in logs]
        self.assertIn("session_created", types)
        self.assertIn("session_starting", types)
        self.assertIn("room_joined", types)
        self.assertIn("relay_ready", types)
        self.assertIn("session_running", types)

    def test_create_logs_do_not_contain_room_joined(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        logs = self.mgr.get_logs(info.session_id)
        types = [e.type for e in logs]
        self.assertNotIn("room_joined", types)

    def test_join_logs_do_not_contain_room_created(self):
        info = self.mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        logs = self.mgr.get_logs(info.session_id)
        types = [e.type for e in logs]
        self.assertNotIn("room_created", types)

    def test_stop_session_appends_stopping_and_stopped(self):
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.mgr.stop_session(info.session_id)
        logs = self.mgr.get_logs(info.session_id)
        types = [e.type for e in logs]
        self.assertIn("session_stopping", types)
        self.assertIn("session_stopped", types)

    def test_get_logs_unknown_session_raises(self):
        with self.assertRaises(BackendError) as ctx:
            self.mgr.get_logs("s_nonexistent")
        self.assertEqual(ctx.exception.code, "SESSION_NOT_FOUND")

    def test_status_to_dict_does_not_include_events(self):
        """SessionInfo.to_dict() must not leak full event list in status response."""
        info = self.mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        d = info.to_dict()
        self.assertNotIn("events", d)

    # ------------------------------------------------------------------
    # runner boundary
    # ------------------------------------------------------------------

    def test_create_session_uses_runner_factory_event_sink(self):
        runners = []

        def factory():
            runner = RecordingRunner()
            runners.append(runner)
            return runner

        mgr = SessionManager(runner_factory=factory)
        info = mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })

        self.assertEqual(info.status, "running")
        self.assertEqual(runners[0].calls, [("start_create", info.session_id)])
        types = [e.type for e in mgr.get_logs(info.session_id)]
        self.assertEqual(types, [
            "session_created",
            "session_starting",
            "room_created",
            "relay_ready",
            "session_running",
        ])

    def test_join_session_uses_runner_factory_event_sink(self):
        runners = []

        def factory():
            runner = RecordingRunner()
            runners.append(runner)
            return runner

        mgr = SessionManager(runner_factory=factory)
        info = mgr.join_session({
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })

        self.assertEqual(info.status, "running")
        self.assertEqual(runners[0].calls, [("start_join", info.session_id)])
        types = [e.type for e in mgr.get_logs(info.session_id)]
        self.assertEqual(types, [
            "session_created",
            "session_starting",
            "room_joined",
            "relay_ready",
            "session_running",
        ])

    def test_stop_session_uses_existing_runner(self):
        runners = []

        def factory():
            runner = RecordingRunner()
            runners.append(runner)
            return runner

        mgr = SessionManager(runner_factory=factory)
        info = mgr.create_session({
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })

        stopped = mgr.stop_session(info.session_id)

        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(runners[0].calls, [
            ("start_create", info.session_id),
            ("stop", info.session_id),
        ])
        types = [e.type for e in mgr.get_logs(info.session_id)]
        self.assertIn("session_stopping", types)
        self.assertIn("session_stopped", types)


if __name__ == "__main__":
    unittest.main()
