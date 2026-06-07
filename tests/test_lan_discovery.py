# -*- coding: utf-8 -*-
"""Tests for backend.lan_discovery — unit + integration, no Core protocol deps."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import unittest
from unittest import mock

from backend.lan_discovery import (
    LanDiscovery,
    LanDiscoveryConfig,
    LanPeer,
    _generate_peer_id,
)

RUN_LAN_SOCKET_TESTS = os.environ.get("COOPWING_RUN_LAN_SOCKET_TESTS") == "1"


class TestLanPeer(unittest.TestCase):

    def test_basic_fields(self):
        p = LanPeer(peer_id="ld_abc123def456", name="TestPC",
                     host="192.168.1.100", port=21520, version="0.3-A1")
        self.assertEqual(p.peer_id, "ld_abc123def456")
        self.assertEqual(p.name, "TestPC")
        self.assertEqual(p.host, "192.168.1.100")
        self.assertEqual(p.port, 21520)
        self.assertEqual(p.version, "0.3-A1")
        self.assertGreater(p.last_seen, 0.0)

    def test_to_dict(self):
        p = LanPeer(peer_id="ld_abc123def456", name="X", host="1.2.3.4",
                     port=80, version="v1")
        d = p.to_dict()
        self.assertEqual(d["peer_id"], "ld_abc123def456")
        self.assertEqual(d["name"], "X")
        self.assertEqual(d["host"], "1.2.3.4")
        self.assertEqual(d["port"], 80)

    def test_from_dict_valid(self):
        raw = {"peer_id": "ld_fff", "name": "N", "port": 9999, "version": "v"}
        p = LanPeer.from_dict(raw, "10.0.0.1")
        self.assertEqual(p.peer_id, "ld_fff")
        self.assertEqual(p.host, "10.0.0.1")
        self.assertEqual(p.port, 9999)

    def test_from_dict_missing_fields_defaults(self):
        p = LanPeer.from_dict({}, "10.0.0.1")
        self.assertEqual(p.peer_id, "")
        self.assertEqual(p.name, "")
        self.assertEqual(p.host, "10.0.0.1")
        self.assertEqual(p.port, 0)
        self.assertEqual(p.version, "")

    def test_from_dict_non_int_port_defaults_to_zero(self):
        p = LanPeer.from_dict({"port": "not-a-number"}, "10.0.0.1")
        self.assertEqual(p.port, 0)

    def test_from_dict_bool_port_defaults_to_zero(self):
        p = LanPeer.from_dict({"port": True}, "10.0.0.1")
        self.assertEqual(p.port, 0)


class TestLanDiscoveryConfig(unittest.TestCase):

    def test_defaults(self):
        c = LanDiscoveryConfig()
        self.assertEqual(c.service_port, 21520)
        self.assertEqual(c.broadcast_port, 21521)
        self.assertGreater(c.announce_interval_seconds, 0)
        self.assertGreater(c.peer_timeout_seconds, 0)
        self.assertEqual(c.product_name, "Co-opWinG")
        self.assertEqual(c.version, "0.4.0")
        self.assertEqual(c.instance_name, "")

    def test_custom_values(self):
        c = LanDiscoveryConfig(
            service_port=9999,
            broadcast_port=8888,
            announce_interval_seconds=2.0,
            peer_timeout_seconds=60.0,
            product_name="TestProduct",
            version="9.9",
            instance_name="MyInstance",
        )
        self.assertEqual(c.service_port, 9999)
        self.assertEqual(c.broadcast_port, 8888)
        self.assertEqual(c.announce_interval_seconds, 2.0)
        self.assertEqual(c.peer_timeout_seconds, 60.0)


class TestPeerIdFormat(unittest.TestCase):

    def test_prefix_is_ld(self):
        pid = _generate_peer_id()
        self.assertTrue(pid.startswith("ld_"), f"Expected ld_ prefix, got {pid}")

    def test_hex_suffix_length(self):
        pid = _generate_peer_id()
        hex_part = pid[3:]
        self.assertEqual(len(hex_part), 12, f"Expected 12 hex chars, got {len(hex_part)}")

    def test_not_player_id_format(self):
        pid = _generate_peer_id()
        self.assertFalse(pid.startswith("p_"), "LAN peer_id must not collide with protocol player_id (p_)")

    def test_unique_ids(self):
        ids = {_generate_peer_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)


class TestAnnouncePayload(unittest.TestCase):

    def setUp(self):
        self.config = LanDiscoveryConfig(
            instance_name="TestBox",
            service_port=21520,
            version="0.3-A1",
        )
        self.disco = LanDiscovery(self.config)

    def test_payload_is_valid_json_line(self):
        payload = self.disco._build_announce_payload()
        self.assertIn("peer_id", payload)
        self.assertIn("name", payload)
        self.assertIn("port", payload)
        self.assertIn("version", payload)
        self.assertIn("product", payload)
        self.assertEqual(payload["product"], "Co-opWinG")
        self.assertNotIn("type", payload)        # no protocol message type
        self.assertNotIn("room_id", payload)      # no protocol field
        self.assertNotIn("player_id", payload)    # no protocol player_id

        encoded = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.assertLess(len(encoded), 1500)

    def test_payload_excludes_core_protocol_fields(self):
        payload = self.disco._build_announce_payload()
        forbidden = {
            "type", "room_id", "player_id", "player_name",
            "relay_token", "relay_ip", "relay_port",
            "echo_timestamp", "timestamp", "peer_ip", "peer_name",
            "peer_port", "reason", "code", "message", "seq",
        }
        for key in forbidden:
            self.assertNotIn(key, payload,
                             f"LAN payload must not contain protocol field: {key}")

    def test_payload_product_name_matches_config(self):
        payload = self.disco._build_announce_payload()
        self.assertEqual(payload["product"], self.disco._config.product_name)


class TestSelfIgnore(unittest.TestCase):

    def test_handle_packet_rejects_own_peer_id(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="Self"))
        payload = json.dumps(disco._build_announce_payload())
        data = (payload + "\n").encode("utf-8")
        disco._handle_packet(data, "127.0.0.1")
        peers = disco.get_peers()
        self.assertEqual(len(peers), 0, "Must ignore own announce")

    def test_handle_packet_rejects_wrong_product(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="A"))
        other = LanDiscovery(LanDiscoveryConfig(instance_name="B", product_name="OtherApp"))
        payload = json.dumps(other._build_announce_payload())
        data = (payload + "\n").encode("utf-8")
        disco._handle_packet(data, "192.168.1.1")
        peers = disco.get_peers()
        self.assertEqual(len(peers), 0, "Must ignore different product")


class TestIllegalPayloadSafety(unittest.TestCase):

    def setUp(self):
        self.disco = LanDiscovery(LanDiscoveryConfig(instance_name="Safe"))
        # inject a fake peer so we have something to receive
        fake = LanPeer(peer_id="ld_fake00000000", name="Fake",
                        host="10.0.0.1", port=8888, version="0.3-A1")
        with self.disco._lock:
            self.disco._peers[fake.peer_id] = fake

    def test_non_json_does_not_crash(self):
        self.disco._handle_packet(b"this is not json", "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)  # only the fake

    def test_empty_bytes_does_not_crash(self):
        self.disco._handle_packet(b"", "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)

    def test_json_array_does_not_crash(self):
        self.disco._handle_packet(b"[1,2,3]\n", "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)

    def test_json_no_peer_id_does_not_crash(self):
        self.disco._handle_packet(b'{"name":"X"}\n', "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)

    def test_oversized_payload_ignored(self):
        big = b"x" * 1600
        self.disco._handle_packet(big, "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)  # oversized dropped

    def test_non_utf8_bytes_does_not_crash(self):
        self.disco._handle_packet(b"\xff\xfe\x00\x01\n", "10.0.0.1")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 1)

    def test_valid_peer_from_other_instance(self):
        other = LanDiscovery(LanDiscoveryConfig(instance_name="Other"))
        payload = json.dumps(other._build_announce_payload())
        data = (payload + "\n").encode("utf-8")
        self.disco._handle_packet(data, "192.168.5.100")
        peers = self.disco.get_peers()
        self.assertEqual(len(peers), 2)  # fake + other
        found = [p for p in peers if p.peer_id == other.peer_id]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].host, "192.168.5.100")


class TestStopIdempotent(unittest.TestCase):

    def test_stop_not_started_does_not_crash(self):
        disco = LanDiscovery(LanDiscoveryConfig())
        disco.stop()
        disco.stop()
        disco.stop()

    def test_stop_repeated_does_not_crash(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="StopTest",
            announce_interval_seconds=60.0,
        ))
        disco.start()
        time.sleep(0.05)
        disco.stop()
        disco.stop()
        disco.stop()

    def test_stop_clears_peers(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="ClearTest",
            announce_interval_seconds=60.0,
        ))
        # inject a peer directly
        p = LanPeer(peer_id="ld_xxx", name="X", host="1.2.3.4",
                     port=9999, version="0.3-A1")
        with disco._lock:
            disco._peers[p.peer_id] = p
        disco.start()
        time.sleep(0.05)
        disco.stop()
        self.assertEqual(len(disco.get_peers()), 0)


class TestPeerTimeout(unittest.TestCase):

    def test_stale_peers_removed_on_get_peers(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="TimeoutTest",
            peer_timeout_seconds=0.1,
            announce_interval_seconds=60.0,
        ))
        p = LanPeer(peer_id="ld_stale", name="Stale", host="10.0.0.1",
                     port=8888, version="0.3-A1")
        with disco._lock:
            disco._peers[p.peer_id] = p
        self.assertEqual(len(disco.get_peers()), 1)
        time.sleep(0.2)
        self.assertEqual(len(disco.get_peers()), 0)

    def test_fresh_peer_stays(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="FreshTest",
            peer_timeout_seconds=30.0,
        ))
        p = LanPeer(peer_id="ld_fresh", name="Fresh", host="10.0.0.1",
                     port=8888, version="0.3-A1")
        with disco._lock:
            disco._peers[p.peer_id] = p
        self.assertEqual(len(disco.get_peers()), 1)


class TestGetPeersThreadSafety(unittest.TestCase):

    def test_get_peers_returns_sorted(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="SortTest"))
        peers = [
            LanPeer(peer_id="ld_z", name="zebra", host="1.1.1.1", port=1, version="v"),
            LanPeer(peer_id="ld_a", name="alpha", host="2.2.2.2", port=2, version="v"),
            LanPeer(peer_id="ld_m", name="mike", host="3.3.3.3", port=3, version="v"),
        ]
        with disco._lock:
            for p in peers:
                disco._peers[p.peer_id] = p
        result = disco.get_peers()
        self.assertEqual([p.name for p in result], ["alpha", "mike", "zebra"])


class TestProtocolCompliance(unittest.TestCase):
    """Verify no Core protocol fields leak into LAN discovery module."""

    def test_source_has_no_protocol_message_types(self):
        import ast
        import inspect
        from backend import lan_discovery as ld
        src = inspect.getsource(ld)
        tree = ast.parse(src)
        strings = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.add(node.value)
        forbidden = {
            "CREATE_ROOM", "ROOM_CREATED", "JOIN_ROOM", "ROOM_JOINED",
            "PEER_INFO", "HEARTBEAT", "LEAVE_ROOM",
            "P2P_SUCCESS", "P2P_FAILED", "RELAY_ENABLED", "ERROR",
            "WAITING", "READY", "PUNCHING", "DIRECT", "RELAY", "CLOSED",
        }
        found = forbidden & strings
        self.assertSetEqual(found, set(),
                            f"LAN discovery must not reference protocol types: {found}")

    def test_source_has_no_protocol_error_codes(self):
        import ast
        import inspect
        from backend import lan_discovery as ld
        src = inspect.getsource(ld)
        tree = ast.parse(src)
        ints = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                ints.add(node.value)
        forbidden_codes = {1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010}
        found = forbidden_codes & ints
        self.assertSetEqual(found, set(),
                            f"LAN discovery must not reference protocol error codes: {found}")


class TestRealSocketLifecycle(unittest.TestCase):
    """Minimal integration test with actual UDP sockets."""

    def test_start_stop_starts_and_stops(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="LifecycleTest",
            announce_interval_seconds=60.0,
        ))
        disco.start()
        time.sleep(0.1)
        try:
            self.assertTrue(disco._running)
            self.assertIsNotNone(disco._thread)
            self.assertTrue(disco._thread.is_alive())
        finally:
            disco.stop()
        self.assertFalse(disco._running)

    @unittest.skipUnless(
        RUN_LAN_SOCKET_TESTS,
        "real LAN socket discovery test is environment-dependent",
    )
    def test_two_instances_discover_each_other(self):
        a = LanDiscovery(LanDiscoveryConfig(
            instance_name="Alice",
            announce_interval_seconds=0.3,
            peer_timeout_seconds=10.0,
        ))
        b = LanDiscovery(LanDiscoveryConfig(
            instance_name="Bob",
            announce_interval_seconds=0.3,
            peer_timeout_seconds=10.0,
        ))

        # Start B's listener first so it's ready when A announces
        b.start()
        time.sleep(0.1)
        try:
            a.start()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                a_peers = a.get_peers()
                b_peers = b.get_peers()
                a_sees = any(p.peer_id == b.peer_id for p in a_peers)
                b_sees = any(p.peer_id == a.peer_id for p in b_peers)
                if a_sees and b_sees:
                    break
                time.sleep(0.2)
            else:
                self.fail(
                    f"Mutual discovery timed out. "
                    f"A peers: {[p.peer_id for p in a.get_peers()]}, "
                    f"B peers: {[p.peer_id for p in b.get_peers()]}"
                )
        finally:
            a.stop()
            b.stop()

        # Verify neither sees itself
        for p in a.get_peers():
            self.assertNotEqual(p.peer_id, a.peer_id)
        for p in b.get_peers():
            self.assertNotEqual(p.peer_id, b.peer_id)


class TestStartFailureRollback(unittest.TestCase):
    """start() must roll back state when _setup_sockets() fails."""

    def test_start_re_raises_on_setup_failure(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="FailTest"))
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("port in use")):
            with self.assertRaises(OSError):
                disco.start()

    def test_start_failure_sets_running_false(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="FailTest"))
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("port in use")):
            try:
                disco.start()
            except OSError:
                pass
        self.assertFalse(disco._running)

    def test_start_failure_sets_thread_none(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="FailTest"))
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("port in use")):
            try:
                disco.start()
            except OSError:
                pass
        self.assertIsNone(disco._thread)

    def test_start_failure_closes_sockets(self):
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="FailTest"))
        real_close = disco._close_sockets
        close_called = []

        def tracking_close():
            close_called.append(True)
            real_close()

        disco._close_sockets = tracking_close
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("port in use")):
            try:
                disco.start()
            except OSError:
                pass
        self.assertTrue(close_called, "_close_sockets must be called on start failure")

    def test_start_still_idempotent_after_failure(self):
        """After a failed start, a second start() must still be callable."""
        disco = LanDiscovery(LanDiscoveryConfig(instance_name="RetryTest"))
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("port in use")):
            try:
                disco.start()
            except OSError:
                pass
        self.assertFalse(disco._running)
        # Second call should not crash; setup_sockets still mocked so it'll
        # fail again, but the guard should have seen _running==False.
        with mock.patch.object(disco, "_setup_sockets", side_effect=OSError("still bad")):
            try:
                disco.start()
            except OSError:
                pass
        self.assertFalse(disco._running)


class TestStopDoesNotJoinCurrentThread(unittest.TestCase):
    """stop() must not call thread.join() when invoked from the discovery thread."""

    def test_stop_from_same_thread_does_not_join(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="SelfJoinTest",
            announce_interval_seconds=60.0,
        ))
        disco._running = True
        disco._announce_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disco._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disco._listen_sock.bind(("127.0.0.1", 0))
        disco._thread = threading.current_thread()

        join_called = []
        real_join = threading.Thread.join

        def tracking_join(self_obj, timeout=None):
            join_called.append(True)
            return real_join(self_obj, timeout)

        with mock.patch.object(threading.Thread, "join", tracking_join):
            disco.stop()

        self.assertEqual(join_called, [],
                         "join() must not be called when stopping from own thread")

    def test_stop_from_other_thread_does_join(self):
        disco = LanDiscovery(LanDiscoveryConfig(
            instance_name="OtherThreadTest",
            announce_interval_seconds=60.0,
        ))
        disco._running = True
        disco._announce_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disco._listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        disco._listen_sock.bind(("127.0.0.1", 0))
        fake_thread = threading.Thread(target=lambda: time.sleep(5))
        fake_thread.start()
        disco._thread = fake_thread

        try:
            join_called = []
            real_join = threading.Thread.join

            def tracking_join(self_obj, timeout=None):
                join_called.append(True)
                return real_join(self_obj, timeout)

            with mock.patch.object(threading.Thread, "join", tracking_join):
                disco.stop()

            self.assertTrue(join_called,
                            "join() must be called when stopping from a different thread")
        finally:
            fake_thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
