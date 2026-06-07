# -*- coding: utf-8 -*-
"""Tests for backend.server — HTTP endpoints with fake sessions.

Starts a real ThreadingHTTPServer on 127.0.0.1:0 in a background thread.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
import unittest

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.lan_discovery import LanPeer
from backend.process_port_detector import (
    ProcessPortCandidate,
    ProcessPortDetectionError,
    ProcessPortScanResult,
)
from backend.server import make_server
from secondary_ip_manager import AdapterBindDecision, SecondaryIpRecommendation, SecondaryIpStatus


def _request(method: str, path: str, body=None, host="127.0.0.1", port=0):
    """Send an HTTP request and return (status, parsed_body)."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
    conn.request(method, path, body=encoded, headers={
        "Content-Type": "application/json",
    })
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"_raw": raw.decode("utf-8", errors="replace")}
    return resp.status, data


def _json_contains_key(data, key):
    if isinstance(data, dict):
        if key in data:
            return True
        return any(_json_contains_key(value, key) for value in data.values())
    if isinstance(data, list):
        return any(_json_contains_key(value, key) for value in data)
    return False


def _alice_participant():
    return {"player_id": "p_alice000001", "player_name": "Alice", "is_host": True}


def _bob_participant():
    return {"player_id": "p_bob00000002", "player_name": "Bob", "is_host": False}


class HTTPTestBase(unittest.TestCase):
    """Base class that starts/stops a backend server on a random port."""

    @classmethod
    def setUpClass(cls):
        cls._server = make_server(host="127.0.0.1", port=0, quiet=True)
        cls._port = cls._server.server_address[1]
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._thread.join(timeout=2)

    @property
    def port(self):
        return self._port

    def req(self, method, path, body=None):
        if (
            method == "POST"
            and path == "/sessions/create"
            and isinstance(body, dict)
            and not body.pop("__omit_game_server_port", False)
            and "game_server_port" not in body
            and "server_host" in body
            and "player_name" in body
        ):
            body = dict(body)
            body["game_server_port"] = 27015
        return _request(method, path, body=body, port=self.port)


class TestHealthEndpoint(HTTPTestBase):
    def test_health_returns_200(self):
        status, data = self.req("GET", "/health")
        self.assertEqual(status, 200)

    def test_health_mode_is_fake(self):
        _, data = self.req("GET", "/health")
        self.assertEqual(data["mode"], "fake")

    def test_health_backend_field(self):
        _, data = self.req("GET", "/health")
        self.assertEqual(data["backend"], "s2pass")

    def test_health_version(self):
        _, data = self.req("GET", "/health")
        self.assertEqual(data["version"], "0.4.0")

    def test_health_uptime(self):
        _, data = self.req("GET", "/health")
        self.assertIn("uptime_seconds", data)
        self.assertGreaterEqual(data["uptime_seconds"], 0)

    def test_health_exposes_backend_admin_state(self):
        _, data = self.req("GET", "/health")
        self.assertIn("backend_admin", data)
        self.assertFalse(data["backend_admin"])


class _FakeProcessPortDetector:
    def __init__(self):
        self.calls = []

    def scan_pid(self, pid):
        self.calls.append(pid)
        if pid == 99999:
            raise ProcessPortDetectionError(
                "INVALID_PID",
                "No running process found for PID 99999",
            )
        return ProcessPortScanResult(
            pid=pid,
            candidates=[
                ProcessPortCandidate(
                    pid=pid,
                    protocol="tcp",
                    local_address="0.0.0.0",
                    local_port=27015,
                    state="Listen",
                    confidence="high",
                    reason="TCP LISTEN on 0.0.0.0:27015",
                ),
                ProcessPortCandidate(
                    pid=pid,
                    protocol="udp",
                    local_address="0.0.0.0",
                    local_port=27016,
                    confidence="high",
                    reason="UDP bound 0.0.0.0:27016",
                ),
            ],
        )


class TestProcessPortEndpoint(unittest.TestCase):
    def setUp(self):
        self.detector = _FakeProcessPortDetector()
        self.server = make_server(
            host="127.0.0.1",
            port=0,
            quiet=True,
            process_port_detector=self.detector,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def req(self, method, path, body=None):
        return _request(method, path, body=body, port=self.port)

    def test_scan_returns_structured_candidates(self):
        status, data = self.req("POST", "/process-ports/scan", {"pid": 12345})

        self.assertEqual(status, 200)
        self.assertEqual(self.detector.calls, [12345])
        self.assertEqual(data["pid"], 12345)
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["candidates"][0]["protocol"], "tcp")
        self.assertEqual(data["candidates"][0]["local_port"], 27015)
        self.assertEqual(data["candidates"][1]["protocol"], "udp")
        self.assertTrue(data["candidates"][1]["reason"])

    def test_invalid_pid_returns_clean_error(self):
        status, data = self.req("POST", "/process-ports/scan", {"pid": 0})

        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_PID")

    def test_missing_process_returns_clean_error(self):
        status, data = self.req("POST", "/process-ports/scan", {"pid": 99999})

        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_PID")

    def test_get_is_not_allowed(self):
        status, data = self.req("GET", "/process-ports/scan")

        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["code"], "METHOD_NOT_ALLOWED")


class TestBackendRunnerMode(unittest.TestCase):
    def test_make_server_default_runner_mode_is_fake(self):
        server = make_server(host="127.0.0.1", port=0, quiet=True)
        try:
            self.assertEqual(server._manager.runner_mode, "fake")
        finally:
            server.server_close()

    def test_make_server_real_core_mode_selects_real_core(self):
        from backend.core_session_runner import CoreSessionRunner

        server = make_server(
            host="127.0.0.1",
            port=0,
            quiet=True,
            runner_mode="real_core",
        )
        try:
            self.assertEqual(server._manager.runner_mode, "real_core")
            self.assertIsInstance(server._manager._runner_factory(), CoreSessionRunner)
        finally:
            server.server_close()

    def test_make_server_invalid_runner_mode_fails_clearly(self):
        with self.assertRaises(ValueError) as ctx:
            make_server(
                host="127.0.0.1",
                port=0,
                quiet=True,
                runner_mode="definitely_wrong",
            )

        self.assertIn("Invalid backend runner mode", str(ctx.exception))


class _FakeLanDiscovery:
    instances = []
    fail_start = False

    def __init__(self, config):
        self.config = config
        self.peer_id = "ld_fakeapi0001"
        self.peers = []
        self.started = False
        self.start_count = 0
        self.stop_count = 0
        self.get_peers_count = 0
        self.__class__.instances.append(self)

    def start(self):
        self.start_count += 1
        if self.fail_start:
            raise OSError("port already in use")
        self.started = True

    def stop(self):
        self.stop_count += 1
        self.started = False
        self.peers = []

    def get_peers(self):
        self.get_peers_count += 1
        return list(self.peers)


class LanDiscoveryHTTPTestBase(unittest.TestCase):
    """LAN discovery HTTP tests with a fake discovery engine."""

    def setUp(self):
        _FakeLanDiscovery.instances = []
        _FakeLanDiscovery.fail_start = False
        self._server = make_server(
            host="127.0.0.1",
            port=0,
            quiet=True,
            lan_discovery_factory=_FakeLanDiscovery,
        )
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._server.shutdown()
        self._thread.join(timeout=2)
        self._server.server_close()

    def req(self, method, path, body=None):
        return _request(method, path, body=body, port=self._port)

    @property
    def fake(self):
        return _FakeLanDiscovery.instances[0]


class _FakeSecondaryIpManager:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def has_ip_mutation_permission(self):
        return bool(getattr(self.decision, "backend_admin", False))

    def choose_adapter_bind_host(
        self,
        requested_ip,
        default_bind_host="127.0.0.1",
        interface_hint=None,
        prefix_length=None,
    ):
        self.calls.append((
            requested_ip,
            default_bind_host,
            interface_hint,
            prefix_length,
        ))
        return self.decision

    def recommend_secondary_ip(self):
        return SecondaryIpRecommendation(
            available=True,
            backend_admin=self.has_ip_mutation_permission(),
            interface_index=18,
            interface_alias="Ethernet",
            interface_description="Intel Ethernet",
            interface_ip="192.168.5.42",
            prefix_length=24,
            recommended_ip="192.168.5.233",
        )

    def startup_cleanup_stale_leases(self):
        return self._cleanup_result()

    def auto_allocate_on_admin_startup(self):
        return SecondaryIpStatus(
            allocated=False,
            backend_admin=self.has_ip_mutation_permission(),
            source="auto",
        )

    def release_allocated_secondary_ip(self):
        return self._cleanup_result()

    def get_secondary_ip_status(self):
        return SecondaryIpStatus(
            allocated=False,
            backend_admin=self.has_ip_mutation_permission(),
        )

    @staticmethod
    def _cleanup_result():
        from secondary_ip_manager import CleanupResult
        return CleanupResult(items=[], ok=True)


class SecondaryIpHTTPTestBase(unittest.TestCase):
    def setUp(self):
        self.secondary = _FakeSecondaryIpManager(
            AdapterBindDecision(
                bind_host="127.0.0.1",
                secondary_ip_enabled=False,
                fallback_used=True,
                warning=(
                    "failed to add secondary IP: verification failed after "
                    "New-NetIPAddress: 192.168.5.233 is not present on "
                    "interface 18 (Ethernet)"
                ),
            )
        )
        self._server = make_server(
            host="127.0.0.1",
            port=0,
            quiet=True,
            secondary_ip_manager=self.secondary,
        )
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._server.shutdown()
        self._thread.join(timeout=2)
        self._server.server_close()

    def req(self, method, path, body=None):
        return _request(method, path, body=body, port=self._port)


class TestLanDiscoveryHttpEndpoints(LanDiscoveryHTTPTestBase):
    def test_status_initial_state_is_stopped(self):
        status, data = self.req("GET", "/api/lan-discovery/status")

        self.assertEqual(status, 200)
        self.assertFalse(data["running"])
        self.assertIsNone(data["peer_id"])
        self.assertEqual(data["peer_count"], 0)
        self.assertIn("instance_name", data)
        self.assertIsInstance(data["service_port"], int)
        self.assertEqual(data["broadcast_port"], 21521)

    def test_start_is_idempotent_and_reuses_single_discovery_instance(self):
        first_status, first_data = self.req("POST", "/api/lan-discovery/start")
        second_status, second_data = self.req("POST", "/api/lan-discovery/start")

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertTrue(first_data["running"])
        self.assertTrue(second_data["running"])
        self.assertEqual(first_data["peer_id"], "ld_fakeapi0001")
        self.assertEqual(second_data["peer_id"], "ld_fakeapi0001")
        self.assertEqual(len(_FakeLanDiscovery.instances), 1)
        self.assertEqual(self.fake.start_count, 1)

    def test_peers_returns_snapshot_with_age_not_raw_last_seen(self):
        self.req("POST", "/api/lan-discovery/start")
        self.fake.peers = [
            LanPeer(
                peer_id="ld_peer0000001",
                name="PeerBox",
                host="192.168.5.44",
                port=21520,
                version="0.3-A1",
                last_seen=time.monotonic() - 2.0,
            ),
        ]

        status, data = self.req("GET", "/api/lan-discovery/peers")

        self.assertEqual(status, 200)
        self.assertTrue(data["running"])
        self.assertEqual(len(data["peers"]), 1)
        peer = data["peers"][0]
        self.assertEqual(peer["peer_id"], "ld_peer0000001")
        self.assertEqual(peer["name"], "PeerBox")
        self.assertEqual(peer["host"], "192.168.5.44")
        self.assertEqual(peer["port"], 21520)
        self.assertEqual(peer["version"], "0.3-A1")
        self.assertIsInstance(peer["last_seen_age_seconds"], (int, float))
        self.assertGreaterEqual(peer["last_seen_age_seconds"], 0.0)
        self.assertNotIn("last_seen", peer)

    def test_peers_when_stopped_returns_empty_without_starting(self):
        status, data = self.req("GET", "/api/lan-discovery/peers")

        self.assertEqual(status, 200)
        self.assertFalse(data["running"])
        self.assertEqual(data["peers"], [])
        self.assertEqual(self.fake.start_count, 0)

    def test_stop_is_idempotent_before_and_after_start(self):
        first_status, first_data = self.req("POST", "/api/lan-discovery/stop")
        self.req("POST", "/api/lan-discovery/start")
        second_status, second_data = self.req("POST", "/api/lan-discovery/stop")
        third_status, third_data = self.req("POST", "/api/lan-discovery/stop")

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(third_status, 200)
        self.assertFalse(first_data["running"])
        self.assertFalse(second_data["running"])
        self.assertFalse(third_data["running"])
        self.assertEqual(self.fake.stop_count, 1)

    def test_start_failure_returns_error_and_rolls_back_status(self):
        _FakeLanDiscovery.fail_start = True

        status, data = self.req("POST", "/api/lan-discovery/start")
        status_after, data_after = self.req("GET", "/api/lan-discovery/status")

        self.assertEqual(status, 503)
        self.assertEqual(data["error"]["code"], "LAN_DISCOVERY_START_FAILED")
        self.assertIn("port already in use", data["error"]["details"]["reason"])
        self.assertEqual(status_after, 200)
        self.assertFalse(data_after["running"])
        self.assertIsNone(data_after["peer_id"])
        self.assertEqual(data_after["peer_count"], 0)

    def test_lan_discovery_responses_do_not_expose_core_protocol_fields(self):
        self.req("POST", "/api/lan-discovery/start")
        self.fake.peers = [
            LanPeer(
                peer_id="ld_peer0000002",
                name="PeerBox",
                host="192.168.5.45",
                port=21520,
                version="0.3-A1",
            ),
        ]

        _, status_data = self.req("GET", "/api/lan-discovery/status")
        _, peers_data = self.req("GET", "/api/lan-discovery/peers")

        forbidden_keys = {
            "room_id", "player_id", "relay_token", "relay_ip", "relay_port",
        }
        forbidden_values = {
            "CREATE_ROOM", "JOIN_ROOM", "RELAY_ENABLED",
        }
        for data in (status_data, peers_data):
            for key in forbidden_keys:
                self.assertFalse(
                    _json_contains_key(data, key),
                    f"LAN discovery API must not expose core protocol field: {key}",
                )
            encoded = json.dumps(data)
            for value in forbidden_values:
                self.assertNotIn(value, encoded)

    def test_create_session_does_not_stop_lan_discovery_or_hide_peers(self):
        self.req("POST", "/api/lan-discovery/start")
        self.fake.peers = [
            LanPeer(
                peer_id="ld_peer0000003",
                name="PeerBox",
                host="192.168.5.46",
                port=21520,
                version="0.3-A1",
            ),
        ]

        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 27015,
        })
        status_after, discovery = self.req("GET", "/api/lan-discovery/status")
        peers_status, peers = self.req("GET", "/api/lan-discovery/peers")

        self.assertEqual(status, 201)
        self.assertEqual(status_after, 200)
        self.assertEqual(peers_status, 200)
        self.assertTrue(discovery["running"])
        self.assertTrue(peers["running"])
        self.assertEqual(peers["peers"][0]["peer_id"], "ld_peer0000003")
        self.assertEqual(self.fake.stop_count, 0)

    def test_join_session_does_not_stop_lan_discovery(self):
        self.req("POST", "/api/lan-discovery/start")

        status, _ = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        status_after, discovery = self.req("GET", "/api/lan-discovery/status")

        self.assertEqual(status, 201)
        self.assertEqual(status_after, 200)
        self.assertTrue(discovery["running"])
        self.assertEqual(self.fake.stop_count, 0)


class TestSecondaryIpHttpDetails(SecondaryIpHTTPTestBase):
    def test_recommendation_endpoint_returns_default_interface_and_ip(self):
        status, data = self.req("GET", "/api/secondary-ip/recommendation")

        self.assertEqual(status, 200)
        self.assertTrue(data["available"])
        self.assertFalse(data["backend_admin"])
        self.assertEqual(data["interface_index"], 18)
        self.assertEqual(data["interface_alias"], "Ethernet")
        self.assertEqual(data["interface_ip"], "192.168.5.42")
        self.assertEqual(data["prefix_length"], 24)
        self.assertEqual(data["recommended_ip"], "192.168.5.233")

    def test_release_endpoint_returns_ok(self):
        status, data = self.req("POST", "/api/secondary-ip/release")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["items"], list)

    def test_status_endpoint_returns_snapshot(self):
        status, data = self.req("GET", "/api/secondary-ip/status")
        self.assertEqual(status, 200)
        self.assertIn("allocated", data)
        self.assertIn("backend_admin", data)
        self.assertIn("bind_mode", data)
        self.assertIn("source", data)
        self.assertIn("last_error", data)

    def test_create_returns_detailed_secondary_ip_add_error(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 27015,
            "adapter_config": {
                "enabled": True,
                "bind_host": "127.0.0.1",
                "target_host": "127.0.0.1",
                "target_port": 27015,
                "secondary_ip_request": {
                    "ip_address": "192.168.5.233",
                    "interface_hint": "18",
                    "prefix_length": 24,
                },
            },
        })

        self.assertEqual(status, 201)
        self.assertFalse(data["secondary_ip_enabled"])
        self.assertTrue(data["secondary_ip_fallback_used"])
        self.assertIn("verification failed", data["secondary_ip_warning"])
        self.assertIn("interface 18", data["secondary_ip_warning"])
        self.assertFalse(data["backend_admin"])
        self.assertEqual(data["adapter_bind_mode"], "loopback")
        self.assertIsNone(data["secondary_ip_bind_address"])
        self.assertEqual(
            self.secondary.calls,
            [("192.168.5.233", "127.0.0.1", "18", 24)],
        )
        self.assertFalse(_json_contains_key(data, "relay_token"))


class TestCreateSessionEndpoint(HTTPTestBase):
    def test_create_returns_201(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(status, 201)

    def test_create_has_room_id(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertIsNotNone(data["room_id"])
        self.assertEqual(len(data["room_id"]), 6)

    def test_create_response_status_and_room_created_share_room_id(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })

        _, status_data = self.req(
            "GET",
            f"/sessions/{created['session_id']}/status",
        )
        _, logs_data = self.req(
            "GET",
            f"/sessions/{created['session_id']}/logs",
        )
        room_created = [
            event for event in logs_data["events"]
            if event["type"] == "room_created"
        ][0]

        self.assertEqual(created["room_id"], status_data["room_id"])
        self.assertEqual(created["room_id"], room_created["data"]["room_id"])

    def test_create_role_is_create(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(data["role"], "create")

    def test_create_status_is_running(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(data["status"], "running")

    def test_create_defaults_force_relay_true(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertTrue(data["force_relay"])

    def test_create_accepts_force_relay_false(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "force_relay": False,
        })
        self.assertFalse(data["force_relay"])

    def test_create_session_id_format(self):
        _, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertTrue(data["session_id"].startswith("s_"))

    def test_create_missing_required_fields_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "player_name": "NoHost",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_create_missing_game_server_port_keeps_udp_preview_compat(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "__omit_game_server_port": True,
        })
        self.assertEqual(status, 201)
        self.assertNotIn("game_server_port", data)

    def test_create_invalid_server_port_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "X",
            "server_port": -1,
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_create_without_adapter_config_omits_adapter_status(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
        })
        self.assertEqual(status, 201)
        self.assertNotIn("adapter_status", data)

    def test_create_with_valid_adapter_config_returns_success(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_host": "127.0.0.1",
                "bind_port": 40100,
                "target_host": "127.0.0.1",
                "target_port": 27015,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["adapter_status"]["status"], "stopped")
        self.assertEqual(data["adapter_status"]["adapter_type"], "local_udp_bridge")

    def test_create_status_exposes_secondary_ip_fields(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": True,
                "target_host": "127.0.0.1",
                "target_port": 27015,
                "secondary_ip_request": {"ip_address": "192.168.1.250"},
            },
        })

        self.assertEqual(status, 201)
        self.assertFalse(data["secondary_ip_enabled"])
        self.assertTrue(data["secondary_ip_fallback_used"])
        self.assertIn("backend process is not elevated", data["secondary_ip_warning"])
        self.assertFalse(data["backend_admin"])
        self.assertEqual(data["adapter_bind_mode"], "loopback")
        self.assertNotIn("relay_token", data)

    def test_create_udp_adapter_config_is_not_tcp_forward(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 27015,
            "force_relay": True,
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "target_host": "127.0.0.1",
                "target_port": 27015,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["adapter_status"]["adapter_type"], "local_udp_bridge")
        self.assertNotEqual(data["adapter_status"]["adapter_type"], "tcp_forward")
        self.assertTrue(data["force_relay"])

    def test_create_adapter_target_mismatch_returns_400_with_reason(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 27015,
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "target_host": "127.0.0.1",
                "target_port": 27016,
            },
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(data["error"]["details"]["field"], "adapter_config.target_port")
        self.assertEqual(data["error"]["details"]["game_server_port"], 27015)
        self.assertEqual(data["error"]["details"]["target_port"], 27016)

    def test_create_with_tcp_forward_adapter_config_returns_success(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 25565,
            "adapter_config": {
                "enabled": True,
                "adapter_type": "tcp_forward",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "target_host": "127.0.0.1",
                "target_port": 25565,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["adapter_status"]["adapter_type"], "tcp_forward")
        self.assertGreater(data["adapter_status"]["bind_port"], 0)
        self.assertEqual(data["adapter_status"]["target_port"], 25565)

    def test_create_with_bundle_starts_and_stops_tcp_udp_rules(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "game_server_port": 27015,
            "adapter_config": {
                "enabled": True,
                "adapter_type": "bundle",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "target_host": "127.0.0.1",
                "target_port": 27015,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["adapter_status"]["status"], "ready")
        self.assertEqual(data["adapter_status"]["adapter_type"], "bundle")
        self.assertGreater(data["adapter_status"]["bind_port"], 0)
        self.assertEqual(
            len(data["adapter_status"]["payload_diagnostics"]["started_rule_ids"]),
            3,
        )
        self.assertEqual(
            data["adapter_status"]["payload_diagnostics"]["included_rule_kinds"],
            ["tcp_forward", "udp_forward", "udp_broadcast_forward"],
        )
        self.assertNotEqual(
            data["adapter_status"]["payload_diagnostics"][
                "udp_broadcast_bind_port"
            ],
            data["adapter_status"]["bind_port"],
        )

        stop_status, stopped = self.req(
            "POST",
            f"/sessions/{data['session_id']}/stop",
        )
        self.assertEqual(stop_status, 200)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["adapter_status"]["status"], "stopped")

    def test_create_bundle_without_target_port_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "bundle",
                "bind_port": 0,
            },
        })

        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(
            data["error"]["details"]["field"],
            "adapter_config.target_port",
        )

    def test_create_with_invalid_adapter_config_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {"enabled": "yes"},
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_create_with_unknown_adapter_type_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {"adapter_type": "not_supported"},
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_create_with_invalid_adapter_port_returns_400(self):
        status, data = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "CreatorA",
            "adapter_config": {"target_port": -1},
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")


class TestJoinSessionEndpoint(HTTPTestBase):
    def test_join_returns_201(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(status, 201)

    def test_join_role_is_join(self):
        _, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(data["role"], "join")

    def test_join_preserves_room_id(self):
        _, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "XYZ789",
            "player_name": "JoinerB",
        })
        self.assertEqual(data["room_id"], "XYZ789")

    def test_join_missing_room_id_returns_400(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "player_name": "JoinerB",
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_join_default_game_server(self):
        _, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(data["game_server_host"], "127.0.0.1")
        self.assertNotIn("game_server_port", data)

    def test_join_defaults_force_relay_true(self):
        _, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertTrue(data["force_relay"])

    def test_join_accepts_force_relay_false(self):
        _, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
            "force_relay": False,
        })
        self.assertFalse(data["force_relay"])

    def test_join_invalid_game_server_port_returns_400(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "X",
            "game_server_port": 0,
        })
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_join_without_adapter_config_omits_adapter_status(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
        })
        self.assertEqual(status, 201)
        self.assertNotIn("adapter_status", data)

    def test_join_with_valid_adapter_config_stays_passive_in_fake_mode(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "JoinerB",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_port": 0,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["adapter_status"]["status"], "stopped")
        self.assertNotIn("target_port", data["adapter_status"])
        self.assertFalse(_json_contains_key(data, "relay_token"))

    def test_join_bundle_without_game_server_port_starts_local_rules(self):
        status, data = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "RN4Y78",
            "player_name": "JoinerB",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "bundle",
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "target_host": "127.0.0.1",
                "target_port": 27015,
            },
        })

        self.assertEqual(status, 201)
        self.assertNotIn("game_server_port", data)
        self.assertEqual(data["status"], "running")
        adapter_status = data["adapter_status"]
        diagnostics = adapter_status["payload_diagnostics"]
        self.assertEqual(adapter_status["status"], "ready")
        self.assertEqual(adapter_status["adapter_type"], "bundle")
        self.assertEqual(adapter_status["target_port"], 27015)
        self.assertGreater(adapter_status["bind_port"], 0)
        self.assertEqual(
            diagnostics["included_rule_kinds"],
            ["tcp_forward", "udp_forward", "udp_broadcast_forward"],
        )
        self.assertEqual(
            diagnostics["local_game_connection"],
            {"host": "127.0.0.1", "port": adapter_status["bind_port"]},
        )
        self.assertEqual(
            [rule["kind"] for rule in diagnostics["rules"]],
            ["tcp_forward", "udp_forward", "udp_broadcast_forward"],
        )
        self.assertNotEqual(
            diagnostics["rules"][2]["local_bind_port"],
            adapter_status["bind_port"],
        )
        self.assertFalse(_json_contains_key(data, "relay_token"))


class TestStatusEndpoint(HTTPTestBase):
    def test_status_returns_200(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["session_id"], created["session_id"])

    def test_status_unknown_session_returns_404(self):
        status, data = self.req("GET", "/sessions/s_nonexistent/status")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "SESSION_NOT_FOUND")

    def test_status_omits_adapter_status_when_unconfigured(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/status")
        self.assertNotIn("adapter_status", data)

    def test_status_includes_disabled_adapter_status_when_configured_disabled(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
            "adapter_config": {
                "enabled": False,
            },
        })
        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["adapter_status"], {
            "enabled": False,
            "status": "disabled",
        })

    def test_status_includes_passive_enabled_adapter_status(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
            "adapter_config": {
                "enabled": True,
                "adapter_type": "local_udp_bridge",
                "bind_port": 40100,
                "target_port": 27015,
            },
        })
        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["adapter_status"]["status"], "stopped")
        self.assertEqual(data["adapter_status"]["bind_port"], 40100)
        self.assertEqual(data["adapter_status"]["target_port"], 27015)

    def test_status_does_not_expose_relay_token(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
            "adapter_config": {
                "enabled": True,
                "target_port": 27015,
            },
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/status")
        self.assertEqual(data["adapter_status"]["adapter_type"], "bundle")
        self.assertFalse(_json_contains_key(data, "relay_token"))

    def test_v1_status_response_includes_compatible_room_defaults(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })

        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")

        self.assertEqual(status, 200)
        self.assertIsNone(data["protocol_version"])
        self.assertIsNone(data["max_players"])
        self.assertIsNone(data["participant_count"])
        self.assertEqual(data["participants"], [])
        self.assertIsNone(data["host_player_id"])
        self.assertFalse(data["room_ready"])
        self.assertFalse(data["room_closed"])
        self.assertTrue(data["relay_ready"])
        self.assertFalse(data["relay_token_available"])
        self.assertIsNone(data["relay_target_host"])
        self.assertIsNone(data["relay_target_port"])

    def test_v2_status_response_includes_participants_and_relay_target(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        manager = getattr(self._server, "_manager")
        manager._emit_event(created["session_id"], "room_updated", "Room updated", {
            "room_id": created["room_id"],
            "event": "participant_joined",
            "participant_count": 2,
            "max_players": 4,
            "host_player_id": "p_alice000001",
            "participants": [_alice_participant(), _bob_participant()],
            "server_time": 1716192000.0,
        })
        manager._emit_event(created["session_id"], "relay_ready", "Relay path ready", {
            "room_id": created["room_id"],
            "relay_token_available": True,
            "relay_target_host": "203.0.113.10",
            "relay_target_port": 9001,
        })

        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")

        self.assertEqual(status, 200)
        self.assertEqual(data["protocol_version"], 2)
        self.assertEqual(data["max_players"], 4)
        self.assertEqual(data["participant_count"], 2)
        self.assertEqual(data["participants"], [_alice_participant(), _bob_participant()])
        self.assertEqual(data["host_player_id"], "p_alice000001")
        self.assertEqual(data["last_room_event"], "participant_joined")
        self.assertTrue(data["relay_ready"])
        self.assertTrue(data["relay_token_available"])
        self.assertEqual(data["relay_target_host"], "203.0.113.10")
        self.assertEqual(data["relay_target_port"], 9001)
        self.assertFalse(_json_contains_key(data, "relay_token"))

    def test_status_room_closed_after_room_closed_event(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        manager = getattr(self._server, "_manager")

        manager._emit_event(created["session_id"], "room_closed", "Room closed", {
            "room_id": created["room_id"],
        })
        status, data = self.req("GET", f"/sessions/{created['session_id']}/status")

        self.assertEqual(status, 200)
        self.assertTrue(data["room_closed"])
        self.assertEqual(data["status"], "closed")


class TestStopEndpoint(HTTPTestBase):
    def test_stop_returns_200(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        status, data = self.req("POST", f"/sessions/{created['session_id']}/stop")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "stopped")

    def test_double_stop_returns_200_with_stopped_status(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        self.req("POST", f"/sessions/{created['session_id']}/stop")
        status, data = self.req("POST", f"/sessions/{created['session_id']}/stop")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "stopped")

    def test_stop_failed_known_session_returns_200_with_failed_status(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        manager = getattr(self._server, "_manager")
        info = manager.get_session(created["session_id"])
        info.status = "failed"

        status, data = self.req("POST", f"/sessions/{created['session_id']}/stop")

        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "failed")

    def test_stop_join_session_returns_stopped_without_room_closed_event(self):
        _, joined = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "Bob",
        })

        status, data = self.req("POST", f"/sessions/{joined['session_id']}/stop")
        _, logs = self.req("GET", f"/sessions/{joined['session_id']}/logs")
        types = [event["type"] for event in logs["events"]]

        self.assertEqual(status, 200)
        self.assertEqual(data["role"], "join")
        self.assertEqual(data["status"], "stopped")
        self.assertNotIn("room_closed", types)
        self.assertFalse(data["room_closed"])

    def test_stop_unknown_session_returns_404(self):
        status, data = self.req("POST", "/sessions/s_missing/stop")

        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "SESSION_NOT_FOUND")


class TestListSessionsEndpoint(HTTPTestBase):
    def test_list_returns_created_sessions(self):
        _, c = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        _, j = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "Bob",
        })
        status, data = self.req("GET", "/sessions")
        self.assertEqual(status, 200)
        ids = {s["session_id"] for s in data["sessions"]}
        self.assertIn(c["session_id"], ids)
        self.assertIn(j["session_id"], ids)


class TestLogsEndpoint(HTTPTestBase):
    def test_logs_returns_200(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        status, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(data["session_id"], created["session_id"])

    def test_logs_response_includes_events_array(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        self.assertIn("events", data)
        self.assertIsInstance(data["events"], list)
        self.assertGreater(len(data["events"]), 0)

    def test_create_logs_include_room_created(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        types = [e["type"] for e in data["events"]]
        self.assertIn("room_created", types)

    def test_session_starting_log_does_not_expose_room_id(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        session_starting = [
            event for event in data["events"]
            if event["type"] == "session_starting"
        ][0]

        self.assertNotIn("room_id", session_starting["data"])

    def test_join_logs_include_room_joined(self):
        _, created = self.req("POST", "/sessions/join", {
            "server_host": "192.168.1.10",
            "room_id": "ABC234",
            "player_name": "Bob",
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        types = [e["type"] for e in data["events"]]
        self.assertIn("room_joined", types)

    def test_stop_then_logs_include_session_stopped(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
        })
        self.req("POST", f"/sessions/{created['session_id']}/stop")
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        types = [e["type"] for e in data["events"]]
        self.assertIn("session_stopped", types)

    def test_logs_unknown_session_returns_404(self):
        status, data = self.req("GET", "/sessions/s_missing/logs")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "SESSION_NOT_FOUND")

    def test_logs_do_not_expose_relay_token(self):
        _, created = self.req("POST", "/sessions/create", {
            "server_host": "192.168.1.10",
            "player_name": "Alice",
            "adapter_config": {
                "enabled": True,
                "target_port": 27015,
            },
        })
        _, data = self.req("GET", f"/sessions/{created['session_id']}/logs")
        self.assertFalse(_json_contains_key(data, "relay_token"))

    def test_logs_post_returns_405(self):
        status, data = self.req("POST", "/sessions/s_test/logs")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["code"], "METHOD_NOT_ALLOWED")


class TestErrorHandling(HTTPTestBase):
    def test_invalid_json_returns_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/sessions/create", body=b"not json",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        self.assertEqual(resp.status, 400)
        data = json.loads(raw)
        self.assertEqual(data["error"]["code"], "INVALID_REQUEST")

    def test_unknown_route_returns_404(self):
        status, data = self.req("GET", "/nonexistent")
        self.assertEqual(status, 404)

    def test_unknown_sub_route_returns_404(self):
        status, data = self.req("GET", "/sessions/s_test/nonexistent")
        self.assertEqual(status, 404)

    def test_method_not_allowed_on_status(self):
        status, data = self.req("POST", "/sessions/s_test/status")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_method_not_allowed_on_stop_get(self):
        status, data = self.req("GET", "/sessions/s_test/stop")
        self.assertEqual(status, 405)


class TestContentType(HTTPTestBase):
    def test_response_has_json_content_type(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        ct = resp.getheader("Content-Type", "")
        conn.close()
        self.assertIn("application/json", ct)


# ============================================================================
# Static boundary tests
# ============================================================================


class TestStaticBoundaries(unittest.TestCase):
    """Verify backend does not import Core or adapters, and has no protocol JSON."""

    def test_session_manager_does_not_import_network_core(self):
        import backend.session_manager as sm
        self.assertNotIn("network_core", dir(sm),
                         "session_manager must not import network_core")

    def test_session_manager_does_not_import_adapters(self):
        import backend.session_manager as sm
        self.assertNotIn("adapters", dir(sm),
                         "session_manager must not import adapters")

    def test_server_does_not_import_network_core(self):
        import backend.server as srv
        self.assertNotIn("network_core", dir(srv),
                         "server must not import network_core")

    def test_server_does_not_import_adapters(self):
        import backend.server as srv
        self.assertNotIn("adapters", dir(srv),
                         "server must not import adapters")

    def test_server_default_bind_is_loopback(self):
        import inspect
        import backend.server as srv
        src = inspect.getsource(srv.make_server)
        self.assertIn("127.0.0.1", src,
                      "make_server default host must be 127.0.0.1")

    def test_make_server_rejects_zeros(self):
        from backend.server import make_server
        with self.assertRaises(ValueError):
            make_server(host="0.0.0.0", port=0)

    def test_make_server_rejects_external(self):
        from backend.server import make_server
        with self.assertRaises(ValueError):
            make_server(host="192.168.1.1", port=0)

    def test_backend_files_no_protocol_json_strings(self):
        """Backend files must not contain S2Pass protocol JSON construction strings."""
        import glob as _glob
        forbidden = ("CREATE_ROOM", "JOIN_ROOM", "ROOM_CREATED", "ROOM_JOINED",
                     "_build_relay_packet", "_send_udp_to_relay")
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "backend",
        )
        for fname in _glob.glob(os.path.join(backend_dir, "*.py")):
            with open(fname, "r", encoding="utf-8") as fh:
                content = fh.read()
            for term in forbidden:
                self.assertNotIn(
                    term, content,
                    f"{os.path.basename(fname)} must not contain protocol string: {term}"
                )


if __name__ == "__main__":
    unittest.main()
