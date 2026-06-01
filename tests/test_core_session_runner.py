# -*- coding: utf-8 -*-
"""Offline tests for backend CoreSessionRunner real-core wiring."""
from __future__ import annotations

import asyncio
import threading
import time
import unittest

from backend.core_session_runner import CoreSessionRunner
from backend.models import SessionInfo, SessionStats


class FakeCoreEvent:
    def __init__(self, event_type, message="", data=None):
        self.type = event_type
        self.message = message
        self.data = data or {}


class FakeConfig:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)
        FakeConfig.instances.append(self)

    @classmethod
    def reset(cls):
        cls.instances = []


def make_fake_core_class(events=None, finish_after_events=False):
    class FakeCore:
        instances = []
        instance_created = threading.Event()

        def __init__(self, config, event_callback=None, event_queue=None):
            self.config = config
            self.event_callback = event_callback
            self.event_queue = event_queue
            self.run_started = threading.Event()
            self.run_cancelled = threading.Event()
            self.close_called = threading.Event()
            self.allow_finish = threading.Event()
            FakeCore.instances.append(self)
            FakeCore.instance_created.set()

        async def run(self):
            self.run_started.set()
            for event in events or []:
                if self.event_callback is not None:
                    self.event_callback(event)
                await asyncio.sleep(0)
            if finish_after_events:
                return
            try:
                while not self.allow_finish.is_set():
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                self.run_cancelled.set()
                raise

        async def close(self):
            self.close_called.set()

    return FakeCore


class EventCollector:
    def __init__(self) -> None:
        self.events = []
        self._lock = threading.Lock()
        self._seen = {}

    def emit(self, event_type, message, data=None):
        with self._lock:
            self.events.append((event_type, message, data or {}))
            self._seen.setdefault(event_type, threading.Event()).set()

    def wait_for(self, event_type, timeout=1.0):
        with self._lock:
            event = self._seen.setdefault(event_type, threading.Event())
        return event.wait(timeout=timeout)

    def types(self):
        with self._lock:
            return [event_type for event_type, _message, _data in self.events]

    def data_for(self, event_type):
        with self._lock:
            return [data for etype, _message, data in self.events if etype == event_type]


def _make_info(role="create", force_relay=True):
    return SessionInfo(
        session_id="s_abc123def456",
        role=role,
        status="starting",
        room_id="ABC234",
        player_name="Tester",
        server_host="127.0.0.1",
        server_port=9000,
        server_udp_port=9001,
        force_relay=force_relay,
        created_at=time.time(),
        updated_at=time.time(),
        stats=SessionStats(),
    )


class CoreSessionRunnerTests(unittest.TestCase):
    def setUp(self):
        FakeConfig.reset()

    def test_can_construct_runner(self):
        runner = CoreSessionRunner(config_class=FakeConfig)
        self.assertFalse(runner.is_running)

    def test_no_public_core_or_loop_accessors(self):
        runner = CoreSessionRunner(config_class=FakeConfig)

        self.assertFalse(hasattr(runner, "get_core"))
        self.assertFalse(hasattr(runner, "get_loop"))

    def test_start_create_builds_expected_config_values(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))

        config = FakeConfig.instances[0]
        self.assertEqual(config.host, info.server_host)
        self.assertEqual(config.port, info.server_port)
        self.assertEqual(config.udp_port, info.server_udp_port)
        self.assertEqual(config.player_name, info.player_name)
        self.assertEqual(config.role, "create")
        self.assertTrue(config.force_relay)
        self.assertTrue(config.is_payload_mode)
        self.assertFalse(config.send_test)
        self.assertNotIn("room_id", config.kwargs)
        runner.stop(info, collector.emit)

    def test_start_create_passes_force_relay_false(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create", force_relay=False)

        runner.start_create(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))

        self.assertFalse(FakeConfig.instances[0].force_relay)
        runner.stop(info, collector.emit)

    def test_transport_factory_called_once_with_core_and_owning_loop(self):
        fake_core = make_fake_core_class()
        calls = []
        transport_handle = object()

        def factory(core, loop):
            calls.append({
                "core": core,
                "loop": loop,
                "thread": threading.current_thread(),
                "run_started": core.run_started.is_set(),
            })
            return transport_handle

        runner = CoreSessionRunner(
            core_class=fake_core,
            config_class=FakeConfig,
            transport_factory=factory,
        )
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))
        core = fake_core.instances[0]
        self.assertTrue(core.run_started.wait(timeout=1.0))

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["core"], core)
        self.assertIsInstance(calls[0]["loop"], asyncio.AbstractEventLoop)
        with runner._lock:
            self.assertIs(runner._loop, calls[0]["loop"])
            self.assertIs(runner._transport, transport_handle)
        self.assertIs(calls[0]["thread"], runner._thread)
        self.assertFalse(calls[0]["run_started"])
        runner.stop(info, collector.emit)

    def test_transport_factory_result_not_emitted_in_event_data(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("RELAY_ENABLED", data={"relay_port": 9001})],
        )
        transport_handle = {"live": "transport_handle"}
        runner = CoreSessionRunner(
            core_class=fake_core,
            config_class=FakeConfig,
            transport_factory=lambda core, loop: transport_handle,
        )
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)
        self.assertTrue(collector.wait_for("session_running"))

        for _etype, _message, data in collector.events:
            self.assertIsNot(data, transport_handle)
            self.assertNotIn("transport", data)
            self.assertNotIn("core", data)
            self.assertNotIn("loop", data)
        runner.stop(info, collector.emit)

    def test_transport_factory_failure_emits_session_failed_and_exits(self):
        fake_core = make_fake_core_class()

        def factory(_core, _loop):
            raise RuntimeError("factory boom")

        runner = CoreSessionRunner(
            core_class=fake_core,
            config_class=FakeConfig,
            transport_factory=factory,
        )
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)

        self.assertTrue(collector.wait_for("session_failed"))
        self.assertTrue(runner.wait(timeout=1.0))
        core = fake_core.instances[0]
        self.assertFalse(core.run_started.is_set())
        self.assertTrue(core.close_called.wait(timeout=1.0))
        failure = collector.data_for("session_failed")[0]
        self.assertEqual(failure["source_event"], "TRANSPORT_FACTORY_FAILED")
        self.assertEqual(failure["code"], "TRANSPORT_FACTORY_FAILED")
        self.assertNotIn("relay_token", failure)
        self.assertNotIn("core", failure)
        self.assertNotIn("loop", failure)

    def test_stop_behavior_unchanged_when_transport_factory_succeeds(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(
            core_class=fake_core,
            config_class=FakeConfig,
            transport_factory=lambda core, loop: object(),
        )
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))
        core = fake_core.instances[0]
        self.assertTrue(core.run_started.wait(timeout=1.0))
        runner.stop(info, collector.emit)

        self.assertTrue(core.run_cancelled.wait(timeout=1.0))
        self.assertTrue(core.close_called.wait(timeout=1.0))
        self.assertIn("session_stopping", collector.types())
        self.assertIn("session_stopped", collector.types())
        self.assertTrue(runner.wait(timeout=1.0))

    def test_start_join_builds_expected_config_values(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("join")

        runner.start_join(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))

        config = FakeConfig.instances[0]
        self.assertEqual(config.host, info.server_host)
        self.assertEqual(config.port, info.server_port)
        self.assertEqual(config.udp_port, info.server_udp_port)
        self.assertEqual(config.player_name, info.player_name)
        self.assertEqual(config.room_id, info.room_id)
        self.assertEqual(config.role, "join")
        self.assertTrue(config.force_relay)
        self.assertTrue(config.is_payload_mode)
        self.assertFalse(config.send_test)
        runner.stop(info, collector.emit)

    def test_start_create_returns_without_blocking(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        start = time.perf_counter()
        runner.start_create(info, collector.emit)
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.05)
        runner.stop(info, collector.emit)

    def test_start_join_returns_without_blocking(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("join")

        start = time.perf_counter()
        runner.start_join(info, collector.emit)
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.05)
        runner.stop(info, collector.emit)

    def test_room_created_maps_to_backend_event(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("ROOM_CREATED", data={"room_id": "XYZ789"})],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)

        self.assertTrue(collector.wait_for("room_created"))
        self.assertEqual(collector.data_for("room_created")[0]["room_id"], "XYZ789")
        self.assertTrue(runner.wait(timeout=1.0))

    def test_room_joined_maps_to_backend_event(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("ROOM_JOINED", data={})],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("join")

        runner.start_join(info, collector.emit)

        self.assertTrue(collector.wait_for("room_joined"))
        self.assertEqual(collector.data_for("room_joined")[0]["room_id"], info.room_id)
        self.assertTrue(runner.wait(timeout=1.0))

    def test_relay_enabled_maps_to_ready_and_running(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("RELAY_ENABLED", data={"relay_port": 9001})],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)

        self.assertTrue(collector.wait_for("relay_ready"))
        self.assertTrue(collector.wait_for("session_running"))
        types = collector.types()
        self.assertLess(types.index("relay_ready"), types.index("session_running"))
        self.assertTrue(runner.wait(timeout=1.0))

    def test_error_maps_to_session_failed(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("ERROR", message="boom")],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)

        self.assertTrue(collector.wait_for("session_failed"))
        self.assertIn("session_failed", collector.types())
        self.assertTrue(runner.wait(timeout=1.0))

    def test_timeout_maps_to_session_failed(self):
        fake_core = make_fake_core_class(
            events=[FakeCoreEvent("TIMEOUT", message="late")],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("join")

        runner.start_join(info, collector.emit)

        self.assertTrue(collector.wait_for("session_failed"))
        self.assertIn("session_failed", collector.types())
        self.assertTrue(runner.wait(timeout=1.0))

    def test_stop_cancels_run_task_and_awaits_close(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)
        self.assertTrue(fake_core.instance_created.wait(timeout=1.0))
        core = fake_core.instances[0]
        self.assertTrue(core.run_started.wait(timeout=1.0))
        runner.stop(info, collector.emit)

        self.assertTrue(core.run_cancelled.wait(timeout=1.0))
        self.assertTrue(core.close_called.wait(timeout=1.0))
        self.assertIn("session_stopping", collector.types())
        self.assertIn("session_stopped", collector.types())
        self.assertTrue(runner.wait(timeout=1.0))

    def test_stop_before_core_creation_is_safe(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.stop(info, collector.emit)

        self.assertEqual(collector.types(), ["session_stopping", "session_stopped"])
        self.assertEqual(fake_core.instances, [])

    def test_repeated_stop_does_not_duplicate_stop_events(self):
        fake_core = make_fake_core_class()
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.stop(info, collector.emit)
        runner.stop(info, collector.emit)

        self.assertEqual(collector.types(), ["session_stopping", "session_stopped"])

    def test_relay_token_is_not_exposed_in_backend_data(self):
        fake_core = make_fake_core_class(
            events=[
                FakeCoreEvent(
                    "RELAY_ENABLED",
                    data={
                        "relay_token": "rtk_secret",
                        "relay_ip": "127.0.0.1",
                        "relay_port": 9001,
                    },
                )
            ],
            finish_after_events=True,
        )
        runner = CoreSessionRunner(core_class=fake_core, config_class=FakeConfig)
        collector = EventCollector()
        info = _make_info("create")

        runner.start_create(info, collector.emit)

        self.assertTrue(collector.wait_for("relay_ready"))
        relay_data = collector.data_for("relay_ready")[0]
        self.assertNotIn("relay_token", relay_data)
        self.assertEqual(relay_data["relay_port"], 9001)
        self.assertTrue(runner.wait(timeout=1.0))

    def test_core_session_runner_static_boundaries(self):
        import inspect
        import backend.core_session_runner as csr

        src = inspect.getsource(csr)
        self.assertNotIn("adapters.core_transport_adapter", src)
        self.assertNotIn("adapters.local_udp_bridge_adapter", src)
        self.assertNotIn("def get_core", src)
        self.assertNotIn("def get_loop", src)


if __name__ == "__main__":
    unittest.main()
