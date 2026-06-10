# -*- coding: utf-8 -*-
"""Tests for backend.adapter_manager passive P5.0C skeleton."""
from __future__ import annotations

import inspect
import socket
import threading
import time
import unittest

from backend.adapter_manager import AdapterManager
from backend.models import (
    BUNDLE_STATUS_FAILED,
    BUNDLE_STATUS_RUNNING,
    BUNDLE_STATUS_STOPPED,
    AdapterConfig,
    BundleResult,
)
from adapters.transport import FakePairTransport, make_fake_pair


class CloseTrackingTransport(FakePairTransport):
    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self):
        self.close_count += 1


class RecordingBundleRunner:
    def __init__(self, start_result=None):
        self.start_result = start_result
        self.bundle = None
        self.stop_count = 0

    def start(self, bundle):
        self.bundle = bundle
        if self.start_result is not None:
            return self.start_result
        return BundleResult(
            bundle_id=bundle.id,
            status=BUNDLE_STATUS_RUNNING,
            started_rule_ids=[rule.id for rule in bundle.rules],
        )

    def stop(self):
        self.stop_count += 1
        return BundleResult(
            bundle_id=self.bundle.id if self.bundle is not None else "",
            status=BUNDLE_STATUS_STOPPED,
            stopped_rule_ids=[
                rule.id for rule in reversed(self.bundle.rules)
            ] if self.bundle is not None else [],
        )


def _bundle_transport_factory(session_id, config):
    local, _ = make_fake_pair()
    return local


def _udp_config(**kwargs):
    return AdapterConfig(
        enabled=True,
        adapter_type="local_udp_bridge",
        **kwargs,
    )


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


class TcpUdpSink:
    def __init__(self):
        self.tcp_payloads = []
        self.udp_payloads = []
        self._stop = threading.Event()
        self._tcp_event = threading.Event()
        self._udp_event = threading.Event()
        self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_sock.bind(("127.0.0.1", 0))
        self.port = self._tcp_sock.getsockname()[1]
        self._tcp_sock.listen()
        self._tcp_sock.settimeout(0.2)
        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_sock.bind(("127.0.0.1", self.port))
        self._udp_sock.settimeout(0.2)
        self._tcp_thread = threading.Thread(target=self._tcp_loop, daemon=True)
        self._udp_thread = threading.Thread(target=self._udp_loop, daemon=True)
        self._tcp_thread.start()
        self._udp_thread.start()

    def stop(self):
        self._stop.set()
        for sock in (self._tcp_sock, self._udp_sock):
            try:
                sock.close()
            except OSError:
                pass
        self._tcp_thread.join(timeout=1.0)
        self._udp_thread.join(timeout=1.0)

    def wait_for_tcp(self, count: int, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.tcp_payloads) >= count:
                return True
            self._tcp_event.wait(timeout=0.05)
            self._tcp_event.clear()
        return len(self.tcp_payloads) >= count

    def wait_for_udp(self, count: int, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.udp_payloads) >= count:
                return True
            self._udp_event.wait(timeout=0.05)
            self._udp_event.clear()
        return len(self.udp_payloads) >= count

    def _tcp_loop(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self._tcp_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                chunks = []
                while True:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                self.tcp_payloads.append(b"".join(chunks))
                self._tcp_event.set()

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                data, _addr = self._udp_sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.udp_payloads.append(data)
            self._udp_event.set()


def _send_tcp(host: str, port: int, payload: bytes) -> None:
    with socket.create_connection((host, port), timeout=2.0) as sock:
        sock.sendall(payload)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


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
        configured = self.manager.configure("s_test", _udp_config())

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
        configured = self.manager.configure("s_test", _udp_config())

        started = self.manager.start("s_test")

        self.assertIs(started, configured)
        self.assertEqual(started.status, "stopped")

    def test_start_enabled_with_transport_factory_transitions_to_ready(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", _udp_config())

        status = manager.start("s_test")
        try:
            self.assertTrue(status.enabled)
            self.assertEqual(status.status, "ready")
            self.assertEqual(status.adapter_type, "local_udp_bridge")
            self.assertEqual(status.bind_host, "127.0.0.1")
            self.assertGreater(status.bind_port, 0)
            self.assertEqual(status.target_host, "127.0.0.1")
            self.assertIsNone(status.target_port)
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

    def test_udp_profile_keeps_protocol_udp(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", _udp_config())

        status = manager.start("s_test")
        try:
            self.assertEqual(status.adapter_type, "local_udp_bridge")
            self.assertEqual(manager._adapters["s_test"].profile.protocol, "udp")
        finally:
            manager.stop("s_test")

    def test_attach_transport_stores_opaque_transport_for_start(self):
        transport = FakePairTransport()
        manager = AdapterManager()
        manager.configure("s_test", _udp_config())
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
        manager.configure("s_test", _udp_config())
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        manager.stop("s_test")

        self.assertEqual(transport.close_count, 1)

    def test_stop_does_not_require_transport_close_method(self):
        transport = FakePairTransport()
        manager = AdapterManager()
        manager.configure("s_test", _udp_config())
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        status = manager.stop("s_test")

        self.assertEqual(status.status, "stopped")

    def test_repeated_stop_closes_attached_transport_at_most_once(self):
        transport = CloseTrackingTransport()
        manager = AdapterManager()
        manager.configure("s_test", _udp_config())
        manager.attach_transport("s_test", transport)
        manager.start("s_test")

        manager.stop("s_test")
        manager.stop("s_test")

        self.assertEqual(transport.close_count, 1)

    def test_bind_port_zero_reports_actual_nonzero_port(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", _udp_config(bind_port=0))

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
        manager.configure("s_test", _udp_config(bind_port=port))

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
        manager.configure("s_test", _udp_config())
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
        manager.configure("s_test", _udp_config())
        ready = manager.start("s_test")
        port = ready.bind_port

        stopped = manager.stop("s_test")

        self.assertEqual(stopped.status, "stopped")
        _assert_can_bind_udp(port)

    def test_repeated_stop_after_ready_remains_stopped(self):
        manager = AdapterManager(
            transport_factory=lambda session_id, config: FakePairTransport(),
        )
        manager.configure("s_test", _udp_config())
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
                adapter_type="local_udp_bridge",
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
        manager.configure("s_test", _udp_config())

        status = manager.start("s_test")

        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "ADAPTER_TRANSPORT_FAILED")
        self.assertIn("test transport unavailable", status.error["message"])

    def test_bundle_builds_tcp_and_udp_rules_with_shared_config(self):
        runner = RecordingBundleRunner()
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
            bundle_runner_factory=lambda: runner,
        )
        manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=41001,
                target_host="127.0.0.1",
                target_port=27015,
            ),
        )

        status = manager.start("s_test")

        self.assertEqual(status.status, "ready")
        self.assertEqual(status.adapter_type, "bundle")
        self.assertEqual(status.bind_port, 41001)
        self.assertEqual(
            [rule.kind for rule in runner.bundle.rules],
            [
                "tcp_forward",
                "udp_forward",
                "tcp_relay",
                "udp_raw_bridge",
                "udp_broadcast_forward",
            ],
        )
        for rule in runner.bundle.rules[:2]:
            self.assertEqual(rule.config, {
                "local_bind_host": "127.0.0.1",
                "local_bind_port": 41001,
                "remote_target_host": "127.0.0.1",
                "remote_target_port": 27015,
            })
        raw_rule = runner.bundle.rules[3]
        self.assertEqual(raw_rule.config["remote_target_port"], 27015)
        broadcast_rule = runner.bundle.rules[4]
        self.assertEqual(
            broadcast_rule.config["local_bind_host"],
            "127.0.0.1",
        )
        self.assertNotEqual(
            broadcast_rule.config["local_bind_port"],
            41001,
        )
        self.assertEqual(
            broadcast_rule.config["remote_target_port"],
            27015,
        )

        stopped = manager.stop("s_test")
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(runner.stop_count, 1)
        self.assertEqual(
            stopped.payload_diagnostics["stopped_rule_ids"],
            [
                "s_test_udp_broadcast",
                "s_test_udp_raw",
                "s_test_tcp_relay",
                "s_test_udp",
                "s_test_tcp",
            ],
        )

    def test_bundle_start_failure_maps_to_structured_adapter_error(self):
        result = BundleResult(
            bundle_id="s_test_bundle",
            status=BUNDLE_STATUS_FAILED,
            failed_rule_id="s_test_udp_broadcast",
            failed_rule_kind="udp_broadcast_forward",
            error_detail="test broadcast failure",
        )
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
            bundle_runner_factory=lambda: RecordingBundleRunner(result),
        )
        manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_port=41002,
                target_port=27015,
            ),
        )

        status = manager.start("s_test")

        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "BUNDLE_START_FAILED")
        self.assertEqual(status.error["message"], "test broadcast failure")
        self.assertEqual(
            status.payload_diagnostics["failed_rule_kind"],
            "udp_broadcast_forward",
        )

    def test_real_bundle_uses_same_ephemeral_tcp_udp_port_and_releases_it(self):
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_port=0,
                target_port=27015,
            ),
        )

        ready = manager.start("s_test")
        self.assertEqual(ready.status, "ready")
        self.assertGreater(ready.bind_port, 0)
        port = ready.bind_port

        stopped = manager.stop("s_test")

        self.assertEqual(stopped.status, "stopped")
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tcp_sock.bind(("127.0.0.1", port))
            udp_sock.bind(("127.0.0.1", port))
        finally:
            udp_sock.close()
            tcp_sock.close()

    def test_local_three_user_bundle_tcp_udp_paths_and_rule_status(self):
        sink = TcpUdpSink()
        self.addCleanup(sink.stop)
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        session_ids = ["s_host", "s_join1", "s_join2"]
        ready_statuses = []

        for session_id in session_ids:
            manager.configure(
                session_id,
                AdapterConfig(
                    enabled=True,
                    adapter_type="bundle",
                    bind_host="127.0.0.1",
                    bind_port=0,
                    target_host="127.0.0.1",
                    target_port=sink.port,
                ),
            )
            status = manager.start(session_id)
            self.assertEqual(status.status, "ready")
            ready_statuses.append(status)

        bind_ports = [status.bind_port for status in ready_statuses]
        self.assertEqual(len(set(bind_ports)), 3)
        self.assertTrue(all(port > 0 for port in bind_ports))

        for index, status in enumerate(ready_statuses):
            tcp_payload = f"tcp-{index}".encode("ascii")
            udp_payload = f"udp-{index}".encode("ascii")
            _send_tcp(status.bind_host, status.bind_port, tcp_payload)
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                udp_sock.sendto(udp_payload, (status.bind_host, status.bind_port))
            finally:
                udp_sock.close()

        self.assertTrue(sink.wait_for_tcp(3))
        self.assertTrue(sink.wait_for_udp(3))

        broadcast_ports = []
        for session_id, status in zip(session_ids, ready_statuses):
            snapshot = manager.snapshot(session_id)
            diagnostics = snapshot.payload_diagnostics
            rules = diagnostics["rules"]
            self.assertEqual(
                [rule["kind"] for rule in rules],
                [
                    "tcp_forward",
                    "udp_forward",
                    "tcp_relay",
                    "udp_raw_bridge",
                    "udp_broadcast_forward",
                ],
            )
            tcp_rule = rules[0]
            udp_rule = rules[1]
            raw_rule = rules[3]
            broadcast_rule = rules[4]
            self.assertEqual(tcp_rule["local_bind_port"], status.bind_port)
            self.assertEqual(udp_rule["local_bind_port"], status.bind_port)
            self.assertEqual(tcp_rule["remote_target_port"], sink.port)
            self.assertEqual(udp_rule["remote_target_port"], sink.port)
            self.assertEqual(raw_rule["remote_target_port"], sink.port)
            self.assertIsInstance(tcp_rule["local_bind_port"], int)
            self.assertIsInstance(udp_rule["local_bind_port"], int)
            self.assertIsInstance(raw_rule["local_bind_port"], int)
            self.assertIsInstance(broadcast_rule["local_bind_port"], int)
            self.assertNotEqual(broadcast_rule["local_bind_port"], status.bind_port)
            broadcast_ports.append(broadcast_rule["local_bind_port"])
            self.assertGreaterEqual(
                tcp_rule["stats"]["bytes_forwarded_to_target"],
                len(b"tcp-0"),
            )
            self.assertGreaterEqual(udp_rule["stats"]["received_packets"], 1)

        self.assertEqual(len(set(broadcast_ports)), 3)

    def test_bundle_without_broadcast_transport_fails_clearly(self):
        manager = AdapterManager()
        manager.configure(
            "s_test",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                target_port=27015,
            ),
        )

        status = manager.start("s_test")

        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "BUNDLE_START_FAILED")
        self.assertIn("requires an available bundle transport", status.error["message"])

    def test_start_without_transport_factory_enabled_config_stays_stopped(self):
        manager = AdapterManager()
        manager.configure("s_test", _udp_config())

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
        manager.configure("s_test", _udp_config())
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
        configured = self.manager.configure("s_test", _udp_config())

        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertIs(self.manager.stop("s_test"), configured)
        self.assertEqual(configured.status, "stopped")

    def test_reconfigure_none_clears_status(self):
        self.manager.configure("s_test", _udp_config())

        self.assertIsNone(self.manager.configure("s_test", None))
        self.assertIsNone(self.manager.snapshot("s_test"))

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Join without target_port — correct UDP relay game path tests
    # ------------------------------------------------------------------

    def test_join_no_target_tcp_forward_and_udp_forward_are_disabled(self):
        """Join without game port: direct forwards disabled,
        tcp_relay + udp_raw_bridge active for gameplay."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        self.assertEqual(status.status, "ready")
        diagnostics = status.payload_diagnostics or {}
        self.assertEqual(
            diagnostics["included_rule_kinds"],
            [
                "tcp_forward",
                "udp_forward",
                "tcp_relay",
                "udp_raw_bridge",
                "udp_broadcast_forward",
            ],
        )
        self.assertFalse(diagnostics["broadcast_only_forwarding"])
        self.assertTrue(diagnostics["tcp_relay_available"])
        self.assertTrue(diagnostics["udp_raw_bridge_available"])
        self.assertTrue(diagnostics["tcp_available"])
        self.assertTrue(diagnostics["udp_available"])
        running_rules = diagnostics["rules"]
        self.assertEqual(len(running_rules), 3)
        running_kinds = [r["kind"] for r in running_rules]
        self.assertIn("tcp_relay", running_kinds)
        self.assertIn("udp_raw_bridge", running_kinds)
        self.assertIn("udp_broadcast_forward", running_kinds)

    def test_join_no_target_local_game_connection_points_to_gameplay_port(self):
        """local_game_connection = shared tcp_relay + udp_raw_bridge port."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        diagnostics = status.payload_diagnostics or {}
        self.assertIn("local_game_connection", diagnostics)
        lgc = diagnostics["local_game_connection"]
        self.assertEqual(lgc["host"], "127.0.0.1")
        self.assertEqual(lgc["port"], status.bind_port)
        self.assertNotEqual(lgc["port"], diagnostics["udp_broadcast_bind_port"])
        self.assertGreater(lgc["port"], 0)
        self.assertTrue(lgc["tcp_available"])
        self.assertTrue(lgc["udp_available"])

    def test_join_no_target_local_game_connection_is_not_room_id(self):
        """local_game_connection port must never equal any room-id-like value."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        room_id = "A1B2C3"
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        diagnostics = status.payload_diagnostics or {}
        lgc_port = diagnostics["local_game_connection"]["port"]
        self.assertNotEqual(str(lgc_port), room_id)
        self.assertIsInstance(lgc_port, int)
        self.assertGreater(lgc_port, 0)
        self.assertLess(lgc_port, 65536)

    def test_join_no_target_does_not_use_relay_target_as_remote_target(self):
        """tcp_forward is disabled; nothing maps to relay_target as game target."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        diagnostics = status.payload_diagnostics or {}
        for rule in diagnostics["rules"]:
            if rule["kind"] in {"tcp_relay", "udp_raw_bridge"}:
                self.assertIsNone(rule.get("remote_target_port"))
            rth = rule.get("remote_target_host")
            if rth is not None:
                self.assertEqual(rth, "127.0.0.1")

    def test_udp_raw_bridge_shares_port_with_tcp_relay_in_join_no_target(self):
        """Join no-target: udp_raw_bridge and tcp_relay share bind_port."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        diagnostics = status.payload_diagnostics or {}
        broadcast_port = diagnostics["udp_broadcast_bind_port"]
        self.assertGreater(broadcast_port, 0)
        self.assertNotEqual(broadcast_port, status.bind_port)
        lgc = diagnostics["local_game_connection"]
        self.assertEqual(lgc["port"], status.bind_port)
        self.assertTrue(lgc["tcp_available"])
        self.assertTrue(lgc["udp_available"])
        raw_rules = [r for r in diagnostics["rules"] if r["kind"] == "udp_raw_bridge"]
        self.assertEqual(len(raw_rules), 1)
        self.assertEqual(raw_rules[0]["local_bind_port"], status.bind_port)

    def test_udp_listener_exists_in_join_no_target(self):
        """A real UDP listener exists on the broadcast port for join no-target."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        diagnostics = status.payload_diagnostics
        ports = [
            diagnostics["local_game_connection"]["port"],
            diagnostics["udp_broadcast_bind_port"],
        ]
        self.assertNotEqual(ports[0], ports[1])
        for port in ports:
            self.assertGreater(port, 0)
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                test_sock.bind(("127.0.0.1", port))
                self.fail(f"UDP port {port} should be in use but bind succeeded")
            except OSError:
                pass
            finally:
                test_sock.close()

    def test_udp_packet_to_local_game_connection_increments_raw_bridge_counters(self):
        """Sending UDP to local_game_connection increments raw bridge counters."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_join",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_host="127.0.0.1",
                bind_port=0,
                target_host="127.0.0.1",
                target_port=0,
            ),
        )

        status = manager.start("s_join")
        self.addCleanup(manager.stop, "s_join")

        port = status.payload_diagnostics["local_game_connection"]["port"]
        payload = b"s2pass-join-raw-udp-test"
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.sendto(payload, ("127.0.0.1", port))
        finally:
            udp_sock.close()

        deadline = time.time() + 2.0
        snapshot = None
        while time.time() < deadline:
            snapshot = manager.snapshot("s_join")
            if snapshot is not None:
                raw_rules = [
                    rule for rule in snapshot.payload_diagnostics["rules"]
                    if rule["kind"] == "udp_raw_bridge"
                ]
                if raw_rules and raw_rules[0]["stats"]["packets_from_game"] >= 1:
                    break
            time.sleep(0.05)

        self.assertIsNotNone(snapshot)
        raw_rules = [
            rule for rule in snapshot.payload_diagnostics["rules"]
            if rule["kind"] == "udp_raw_bridge"
        ]
        self.assertEqual(len(raw_rules), 1)
        self.assertGreaterEqual(raw_rules[0]["stats"]["packets_from_game"], 1)
        self.assertGreaterEqual(raw_rules[0]["stats"]["bytes_from_game"], len(payload))
        self.assertGreaterEqual(snapshot.counters.packets_from_game, 1)
        self.assertGreaterEqual(snapshot.counters.bytes_from_game, len(payload))

    def test_create_still_requires_target_port(self):
        """Create with target_port=None or missing is rejected by validation."""
        manager = AdapterManager(
            bundle_transport_factory=_bundle_transport_factory,
        )
        manager.configure(
            "s_create",
            AdapterConfig(
                enabled=True,
                adapter_type="bundle",
                bind_port=0,
                target_port=None,
            ),
        )

        status = manager.start("s_create")
        self.assertEqual(status.status, "error")
        self.assertEqual(status.error["code"], "BUNDLE_START_FAILED")


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
