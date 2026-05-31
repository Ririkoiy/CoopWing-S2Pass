# -*- coding: utf-8 -*-
"""Tests for backend.adapter_manager passive P5.0C skeleton."""
from __future__ import annotations

import inspect
import socket
import threading
import time
import unittest

from backend.adapter_manager import AdapterManager
from backend.models import AdapterConfig
from adapters.transport import FakePairTransport, make_fake_pair


class CloseTrackingTransport(FakePairTransport):
    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self):
        self.close_count += 1


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _assert_can_bind_udp(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", port))
    finally:
        sock.close()


class AdapterManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = AdapterManager()

    def test_configure_none_returns_none(self):
        self.assertIsNone(self.manager.configure("s_test", None))

    def test_configure_disabled_returns_disabled_status(self):
        status = self.manager.configure("s_test", AdapterConfig(enabled=False))

        self.assertIsNotNone(status)
        self.assertFalse(status.enabled)
        self.assertEqual(status.status, "disabled")
        self.assertEqual(status.to_dict(), {
            "enabled": False,
            "status": "disabled",
        })

    def test_configure_enabled_returns_stopped_status(self):
        status = self.manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                bind_port=40100,
                target_port=40200,
            ),
        )

        self.assertIsNotNone(status)
        self.assertTrue(status.enabled)
        self.assertEqual(status.status, "stopped")
        self.assertEqual(status.bind_port, 40100)
        self.assertEqual(status.target_port, 40200)
        self.assertEqual(status.counters.to_dict(), {
            "packets_from_game": 0,
            "packets_to_transport": 0,
            "packets_from_transport": 0,
            "packets_to_game": 0,
            "bytes_from_game": 0,
            "bytes_to_transport": 0,
            "bytes_from_transport": 0,
            "bytes_to_game": 0,
        })
        self.assertIsNone(status.error)

    def test_snapshot_returns_configured_status(self):
        configured = self.manager.configure("s_test", AdapterConfig(enabled=True))

        self.assertIs(self.manager.snapshot("s_test"), configured)

    def test_snapshot_unknown_returns_none(self):
        self.assertIsNone(self.manager.snapshot("s_unknown"))

    def test_start_unconfigured_is_noop(self):
        self.assertIsNone(self.manager.start("s_unknown"))

    def test_start_disabled_is_noop(self):
        configured = self.manager.configure("s_test", AdapterConfig(enabled=False))

        started = self.manager.start("s_test")

        self.assertIs(started, configured)
        self.assertEqual(started.status, "disabled")

    def test_start_enabled_does_not_transition_to_ready(self):
        configured = self.manager.configure("s_test", AdapterConfig(enabled=True))

        started = self.manager.start("s_test")

        self.assertIs(started, configured)
        self.assertEqual(started.status, "stopped")

    def test_start_enabled_with_transport_factory_transitions_to_ready(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True))

        status = manager.start("s_test")
        try:
            self.assertTrue(status.enabled)
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.adapter_type, "local_udp_bridge")
            self.assertEqual(status.bind_host, "127.0.0.1")
            self.assertGreater(status.bind_port, 0)
            self.assertEqual(status.target_host, "127.0.0.1")
            self.assertEqual(status.target_port, 40200)
            self.assertEqual(status.counters.to_dict(), {
                "packets_from_game": 0,
                "packets_to_transport": 0,
                "packets_from_transport": 0,
                "packets_to_game": 0,
                "bytes_from_game": 0,
                "bytes_to_transport": 0,
                "bytes_from_transport": 0,
                "bytes_to_game": 0,
            })
            self.assertIsNone(status.error)
        finally:
            manager.stop("s_test")

    def test_attach_transport_stores_opaque_transport_for_start(self):
        transport = FakePairTransport()
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.attach_transport("s_test", transport)

        status = manager.start("s_test")
        try:
            self.assertEqual(status.status, "ready")
            self.assertGreater(status.bind_port, 0)
        finally:
            manager.stop("s_test")

    def test_attach_transport_for_unknown_session_closes_transport(self):
        transport = CloseTrackingTransport()
        manager = AdapterManager()

        manager.attach_transport("s_unknown", transport)

        self.assertEqual(transport.close_count, 1)
        self.assertIsNone(manager.start("s_unknown"))

    def test_attach_transport_for_disabled_session_closes_transport(self):
        transport = CloseTrackingTransport()
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=False))

        manager.attach_transport("s_test", transport)

        self.assertEqual(transport.close_count, 1)
        self.assertEqual(manager.start("s_test").status, "disabled")

    def test_stop_closes_attached_transport_if_close_exists(self):
        transport = CloseTrackingTransport()
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        manager.stop("s_test")

        self.assertEqual(transport.close_count, 1)

    def test_stop_does_not_require_transport_close_method(self):
        transport = FakePairTransport()
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        status = manager.stop("s_test")

        self.assertEqual(status.status, "stopped")

    def test_repeated_stop_closes_attached_transport_at_most_once(self):
        transport = CloseTrackingTransport()
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        manager.stop("s_test")
        manager.stop("s_test")

        self.assertEqual(transport.close_count, 1)

    def test_bind_port_zero_reports_actual_nonzero_port(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True, bind_port=0))

        status = manager.start("s_test")
        try:
            self.assertEqual(status.status, "ready")
            self.assertGreater(status.bind_port, 0)
        finally:
            manager.stop("s_test")

    def test_fixed_bind_port_reports_requested_port(self):
        port = _free_udp_port()
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True, bind_port=port))

        status = manager.start("s_test")
        try:
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.bind_port, port)
        finally:
            manager.stop("s_test")

    def test_snapshot_after_start_returns_ready_and_counters(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.start("s_test")

        status = manager.snapshot("s_test")
        try:
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.counters.to_dict(), {
                "packets_from_game": 0,
                "packets_to_transport": 0,
                "packets_from_transport": 0,
                "packets_to_game": 0,
                "bytes_from_game": 0,
                "bytes_to_transport": 0,
                "bytes_from_transport": 0,
                "bytes_to_game": 0,
            })
        finally:
            manager.stop("s_test")

    def test_stop_after_ready_returns_stopped_and_releases_port(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True))
        ready = manager.start("s_test")
        port = ready.bind_port

        stopped = manager.stop("s_test")

        self.assertEqual(stopped.status, "stopped")
        _assert_can_bind_udp(port)

    def test_repeated_stop_after_ready_remains_stopped(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", AdapterConfig(enabled=True))
        manager.start("s_test")

        first = manager.stop("s_test")
        second = manager.stop("s_test")

        self.assertEqual(first.status, "stopped")
        self.assertEqual(second.status, "stopped")

    def test_bind_failure_maps_to_adapter_error(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                bind_host="203.0.113.1",
                bind_port=40100,
            ),
        )

        status = manager.start("s_test")

        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "ADAPTER_BIND_FAILED")
        self.assertIn("Failed to bind UDP socket", status.error["message"])

    def test_transport_factory_failure_maps_to_adapter_error(self):
        def fail_factory(session_id, config):
            raise RuntimeError("test transport unavailable")

        manager = AdapterManager(transport_factory=fail_factory)
        manager.configure("s_test", AdapterConfig(enabled=True))

        status = manager.start("s_test")

        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "ADAPTER_TRANSPORT_FAILED")
        self.assertIn("test transport unavailable", status.error["message"])

    def test_start_without_transport_factory_enabled_config_stays_stopped(self):
        manager = AdapterManager()
        manager.configure("s_test", AdapterConfig(enabled=True))

        status = manager.start("s_test")

        self.assertEqual(status.status, "stopped")
        self.assertIsNone(status.error)

    def test_game_to_transport_forwarding_with_fake_pair_transport(self):
        t1, t2 = make_fake_pair()
        received = []
        received_event = threading.Event()

        def on_receive(payload: bytes) -> None:
            received.append(payload)
            received_event.set()

        t2.set_receive_callback(on_receive)
        manager = AdapterManager(transport_factory=lambda session_id, config: t1)
        manager.configure("s_test", AdapterConfig(enabled=True))
        status = manager.start("s_test")
        self.assertEqual(status.status, "ready")

        payload = b"s2pass-adapter-manager-payload"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(payload, (status.bind_host, status.bind_port))
            self.assertTrue(received_event.wait(timeout=2.0))
            self.assertEqual(received, [payload])
            deadline = time.time() + 2.0
            snapshot = manager.snapshot("s_test")
            while time.time() < deadline and snapshot.counters.packets_to_transport < 1:
                time.sleep(0.01)
                snapshot = manager.snapshot("s_test")
            self.assertEqual(snapshot.counters.packets_from_game, 1)
            self.assertEqual(snapshot.counters.packets_to_transport, 1)
            self.assertEqual(snapshot.counters.bytes_from_game, len(payload))
            self.assertEqual(snapshot.counters.bytes_to_transport, len(payload))
        finally:
            sock.close()
            manager.stop("s_test")

    def test_stop_unconfigured_is_idempotent(self):
        self.assertIsNone(self.manager.stop("s_unknown"))
        self.assertIsNone(self.manager.stop("s_unknown"))

    def test_stop_disabled_is_idempotent(self):
        configured = self.manager.configure("s_test", AdapterConfig(enabled=False))

        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertEqual(configured.status, "disabled")

    def test_stop_enabled_stopped_is_idempotent(self):
        configured = self.manager.configure("s_test", AdapterConfig(enabled=True))

        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertEqual(configured.status, "stopped")

    def test_reconfigure_none_clears_status(self):
        self.manager.configure("s_test", AdapterConfig(enabled=True))

        self.assertIsNone(self.manager.configure("s_test", None))
        self.assertIsNone(self.manager.snapshot("s_test"))


class AdapterManagerBoundaryTests(unittest.TestCase):
    def test_no_network_core_import(self):
        import backend.adapter_manager as am

        self.assertNotIn("network_core", dir(am))

    def test_no_adapter_implementation_imports(self):
        import backend.adapter_manager as am

        self.assertNotIn("CoreTransportAdapter", dir(am))

    def test_source_does_not_import_forbidden_adapter_modules(self):
        import backend.adapter_manager as am

        src = inspect.getsource(am)
        self.assertNotIn("adapters.core_transport_adapter", src)
        self.assertNotIn("backend.core_session_runner", src)

    def test_source_does_not_create_sockets_directly(self):
        import backend.adapter_manager as am

        src = inspect.getsource(am)
        self.assertNotIn("socket.socket", src)
        self.assertNotIn("create_datagram_endpoint", src)

    def test_source_does_not_contain_protocol_or_secret_strings(self):
        import backend.adapter_manager as am

        src = inspect.getsource(am)
        forbidden = (
            "CREATE_ROOM",
            "JOIN_ROOM",
            "ROOM_CREATED",
            "ROOM_JOINED",
            "_build_relay_packet",
            "_send_udp_to_relay",
            "relay_token",
        )
        for term in forbidden:
            self.assertNotIn(term, src)


if __name__ == "__main__":
    unittest.main()
