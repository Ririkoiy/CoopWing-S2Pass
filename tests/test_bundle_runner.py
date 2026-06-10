"""Focused tests for generic TCP/UDP bundle orchestration."""
from __future__ import annotations

import inspect
import socket
import unittest

from adapters.tcp_adapter import GenericTcpForwardAdapter
from adapters.transport import make_fake_pair
from adapters.udp_adapter import GenericUdpForwardAdapter
from adapters.udp_broadcast_forward_adapter import (
    GenericUdpBroadcastForwardAdapter,
)
from adapters.udp_raw_bridge_adapter import UdpRawBridgeAdapter
from backend.bundle_runner import BundleRunner
from backend.models import (
    BUNDLE_STATUS_FAILED,
    BUNDLE_STATUS_RUNNING,
    BUNDLE_STATUS_STOPPED,
    BUNDLE_RULE_UDP_RAW_BRIDGE,
    BundleConfig,
    BundleRule,
)


class FakeAdapter:
    def __init__(self, rule_id, events, fail_start=False, fail_stop=False):
        self.rule_id = rule_id
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    def start(self):
        self.events.append(("start", self.rule_id))
        if self.fail_start:
            raise RuntimeError(f"start failed for {self.rule_id}")

    def stop(self):
        self.events.append(("stop", self.rule_id))
        if self.fail_stop:
            raise RuntimeError(f"stop failed for {self.rule_id}")


def _rule(rule_id, kind="tcp_forward", enabled=True, **config):
    return BundleRule(
        id=rule_id,
        kind=kind,
        enabled=enabled,
        config=config,
    )


def _reserve_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class BundleRunnerLifecycleTests(unittest.TestCase):
    def test_tcp_and_udp_bundle_starts_with_existing_adapters(self):
        bundle = BundleConfig(
            id="tcp-udp",
            rules=[
                _rule(
                    "tcp",
                    local_bind_host="127.0.0.1",
                    local_bind_port=0,
                    remote_target_host="127.0.0.1",
                    remote_target_port=9,
                ),
                _rule(
                    "udp",
                    kind="udp_forward",
                    local_bind_host="127.0.0.1",
                    local_bind_port=0,
                    remote_target_host="127.0.0.1",
                    remote_target_port=9,
                ),
            ],
        )
        runner = BundleRunner()

        result = runner.start(bundle)
        self.addCleanup(runner.stop)

        self.assertEqual(result.status, BUNDLE_STATUS_RUNNING)
        self.assertEqual(result.started_rule_ids, ["tcp", "udp"])
        self.assertIsInstance(runner._started[0][1], GenericTcpForwardAdapter)
        self.assertIsInstance(runner._started[1][1], GenericUdpForwardAdapter)
        self.assertTrue(all(adapter.is_running() for _, adapter in runner._started))

    def test_stop_stops_all_in_reverse_start_order(self):
        events = []
        runner = BundleRunner(
            adapter_factory=lambda rule: FakeAdapter(rule.id, events)
        )
        bundle = BundleConfig(
            id="ordered",
            rules=[_rule("one"), _rule("two", "udp_forward"), _rule("three")],
        )

        self.assertEqual(runner.start(bundle).status, BUNDLE_STATUS_RUNNING)
        result = runner.stop()

        self.assertEqual(result.status, BUNDLE_STATUS_STOPPED)
        self.assertEqual(result.stopped_rule_ids, ["three", "two", "one"])
        self.assertEqual(events, [
            ("start", "one"),
            ("start", "two"),
            ("start", "three"),
            ("stop", "three"),
            ("stop", "two"),
            ("stop", "one"),
        ])

    def test_disabled_rule_is_not_instantiated(self):
        events = []
        created = []

        def factory(rule):
            created.append(rule.id)
            return FakeAdapter(rule.id, events)

        runner = BundleRunner(adapter_factory=factory)
        result = runner.start(BundleConfig(
            id="disabled",
            rules=[_rule("off", enabled=False), _rule("on", "udp_forward")],
        ))
        self.addCleanup(runner.stop)

        self.assertEqual(result.started_rule_ids, ["on"])
        self.assertEqual(created, ["on"])
        self.assertEqual(events, [("start", "on")])

    def test_invalid_rule_kind_fails_before_any_rule_starts(self):
        created = []
        runner = BundleRunner(
            adapter_factory=lambda rule: created.append(rule.id)
        )

        result = runner.start(BundleConfig(
            id="invalid",
            rules=[_rule("valid"), _rule("bad", "not_a_kind")],
        ))

        self.assertEqual(result.status, BUNDLE_STATUS_FAILED)
        self.assertEqual(result.failed_rule_id, "bad")
        self.assertEqual(result.failed_rule_kind, "not_a_kind")
        self.assertIn("Unsupported bundle rule kind", result.error_detail)
        self.assertEqual(created, [])

    def test_duplicate_rule_ids_are_rejected_before_start(self):
        created = []
        runner = BundleRunner(
            adapter_factory=lambda rule: created.append(rule.id)
        )

        result = runner.start(BundleConfig(
            id="duplicates",
            rules=[_rule("same"), _rule("same", "udp_forward")],
        ))

        self.assertEqual(result.status, BUNDLE_STATUS_FAILED)
        self.assertEqual(result.failed_rule_id, "same")
        self.assertIn("Duplicate bundle rule id", result.error_detail)
        self.assertEqual(created, [])

    def test_later_start_failure_cleans_up_started_rules(self):
        events = []

        def factory(rule):
            return FakeAdapter(
                rule.id,
                events,
                fail_start=rule.id == "fail",
            )

        runner = BundleRunner(adapter_factory=factory)
        result = runner.start(BundleConfig(
            id="rollback",
            rules=[_rule("one"), _rule("two", "udp_forward"), _rule("fail")],
        ))

        self.assertEqual(result.status, BUNDLE_STATUS_FAILED)
        self.assertEqual(result.failed_rule_id, "fail")
        self.assertEqual(result.started_rule_ids, ["one", "two"])
        self.assertEqual(result.stopped_rule_ids, ["two", "one"])
        self.assertFalse(runner.is_running)
        self.assertEqual(events, [
            ("start", "one"),
            ("start", "two"),
            ("start", "fail"),
            ("stop", "two"),
            ("stop", "one"),
        ])

    def test_udp_broadcast_rule_uses_existing_adapter(self):
        bind_port = _reserve_udp_port()
        target_port = _reserve_udp_port()
        transports = []

        def transport_factory(rule):
            local, _ = make_fake_pair()
            transports.append(local)
            return local

        runner = BundleRunner(transport_factory=transport_factory)
        result = runner.start(BundleConfig(
            id="broadcast",
            rules=[_rule(
                "lan",
                "udp_broadcast_forward",
                local_bind_host="127.0.0.1",
                local_bind_port=bind_port,
                remote_target_host="127.0.0.1",
                remote_target_port=target_port,
            )],
        ))
        self.addCleanup(runner.stop)

        self.assertEqual(result.status, BUNDLE_STATUS_RUNNING)
        self.assertEqual(len(transports), 1)
        self.assertIsInstance(
            runner._started[0][1],
            GenericUdpBroadcastForwardAdapter,
        )

    def test_udp_raw_bridge_rule_uses_new_adapter(self):
        transports = []

        def transport_factory(rule):
            local, _ = make_fake_pair()
            transports.append(local)
            return local

        runner = BundleRunner(transport_factory=transport_factory)
        result = runner.start(BundleConfig(
            id="raw-bridge",
            rules=[_rule(
                "raw",
                BUNDLE_RULE_UDP_RAW_BRIDGE,
                local_bind_host="127.0.0.1",
                local_bind_port=0,
                remote_target_host="127.0.0.1",
                remote_target_port=9,
            )],
        ))
        self.addCleanup(runner.stop)

        self.assertEqual(result.status, BUNDLE_STATUS_RUNNING)
        self.assertEqual(len(transports), 1)
        self.assertIsInstance(runner._started[0][1], UdpRawBridgeAdapter)

    def test_stop_is_idempotent(self):
        events = []
        runner = BundleRunner(
            adapter_factory=lambda rule: FakeAdapter(rule.id, events)
        )
        runner.start(BundleConfig(id="idempotent", rules=[_rule("one")]))

        first = runner.stop()
        second = runner.stop()

        self.assertEqual(first.stopped_rule_ids, ["one"])
        self.assertEqual(second.stopped_rule_ids, [])
        self.assertEqual(events.count(("stop", "one")), 1)

    def test_empty_bundle_is_stopped_noop(self):
        runner = BundleRunner()

        result = runner.start(BundleConfig(id="empty"))

        self.assertEqual(result.status, BUNDLE_STATUS_STOPPED)
        self.assertTrue(result.ok)
        self.assertFalse(runner.is_running)

    def test_stop_continues_after_child_stop_failure(self):
        events = []

        def factory(rule):
            return FakeAdapter(
                rule.id,
                events,
                fail_stop=rule.id == "two",
            )

        runner = BundleRunner(adapter_factory=factory)
        runner.start(BundleConfig(
            id="stop-errors",
            rules=[_rule("one"), _rule("two", "udp_forward"), _rule("three")],
        ))

        result = runner.stop()

        self.assertEqual(result.stopped_rule_ids, ["three", "one"])
        self.assertEqual(len(result.cleanup_errors), 1)
        self.assertIn("two (udp_forward)", result.cleanup_errors[0])
        self.assertEqual(events[-3:], [
            ("stop", "three"),
            ("stop", "two"),
            ("stop", "one"),
        ])


class BundleModelTests(unittest.TestCase):
    def test_bundle_config_from_dict_and_to_dict(self):
        bundle = BundleConfig.from_dict({
            "name": "local-services",
            "rules": [{
                "id": "tcp",
                "kind": "tcp_forward",
                "enabled": False,
                "config": {"local_bind_port": 1234},
            }],
        })

        self.assertEqual(bundle.id, "local-services")
        self.assertEqual(bundle.rules[0].id, "tcp")
        self.assertFalse(bundle.rules[0].enabled)
        self.assertEqual(bundle.to_dict()["rules"][0]["config"], {
            "local_bind_port": 1234,
        })

    def test_bundle_runner_has_no_core_protocol_construction(self):
        import backend.bundle_runner as bundle_module

        source = inspect.getsource(bundle_module)
        for protocol_term in (
            "CREATE_ROOM",
            "JOIN_ROOM",
            "RELAY_ENABLED",
            "relay_token",
        ):
            self.assertNotIn(protocol_term, source)


if __name__ == "__main__":
    unittest.main()
