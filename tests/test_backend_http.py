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
import unittest

# Ensure backend is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.server import make_server


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
        self.assertEqual(data["version"], "0.1.0")

    def test_health_uptime(self):
        _, data = self.req("GET", "/health")
        self.assertIn("uptime_seconds", data)
        self.assertGreaterEqual(data["uptime_seconds"], 0)


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
                "bind_port": 0,
            },
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["adapter_status"]["status"], "stopped")
        self.assertNotIn("target_port", data["adapter_status"])
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
        self.assertFalse(_json_contains_key(data, "relay_token"))


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
