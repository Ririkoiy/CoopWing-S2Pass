# -*- coding: utf-8 -*-
"""Tests for backend.models."""
from __future__ import annotations

import unittest

from backend.models import (
    ADAPTER_STATUS_ERROR,
    ADAPTER_STATUS_READY,
    AdapterConfig,
    AdapterCounters,
    AdapterStatus,
    BackendError,
    SessionEvent,
    SessionInfo,
    SessionStats,
)


class SessionEventTests(unittest.TestCase):
    def test_to_dict_minimal(self):
        evt = SessionEvent(type="test_event", message="test message", timestamp=1000.0)
        d = evt.to_dict()
        self.assertEqual(d["type"], "test_event")
        self.assertEqual(d["message"], "test message")
        self.assertEqual(d["timestamp"], 1000.0)
        self.assertEqual(d["data"], {})

    def test_to_dict_with_data(self):
        evt = SessionEvent(type="room_created", message="Room ABC created",
                           timestamp=2000.0, data={"room_id": "ABC234"})
        d = evt.to_dict()
        self.assertEqual(d["type"], "room_created")
        self.assertEqual(d["data"], {"room_id": "ABC234"})

    def test_timestamp_defaults_to_now(self):
        import time
        before = time.time()
        evt = SessionEvent(type="x", message="y")
        after = time.time()
        self.assertGreaterEqual(evt.timestamp, before)
        self.assertLessEqual(evt.timestamp, after)

    def test_data_default_none_to_dict_empty(self):
        evt = SessionEvent(type="x", message="y")
        d = evt.to_dict()
        self.assertEqual(d["data"], {})


class BackendErrorTests(unittest.TestCase):
    def test_to_dict_minimal(self):
        err = BackendError(code="TEST", message="test message")
        d = err.to_dict()
        self.assertEqual(d["code"], "TEST")
        self.assertEqual(d["message"], "test message")
        self.assertNotIn("details", d)

    def test_to_dict_with_details(self):
        err = BackendError(code="TEST", message="msg", details={"key": "val"})
        d = err.to_dict()
        self.assertEqual(d["details"], {"key": "val"})

    def test_is_exception(self):
        err = BackendError(code="X", message="y")
        self.assertIsInstance(err, Exception)


class AdapterConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = AdapterConfig()
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.adapter_type, "local_udp_bridge")
        self.assertEqual(cfg.bind_host, "127.0.0.1")
        self.assertEqual(cfg.bind_port, 0)
        self.assertEqual(cfg.target_host, "127.0.0.1")
        self.assertIsNone(cfg.target_port)

    def test_from_dict_valid(self):
        cfg = AdapterConfig.from_dict({
            "enabled": True,
            "adapter_type": "local_udp_bridge",
            "bind_host": "127.0.0.1",
            "bind_port": 40100,
            "target_host": "127.0.0.1",
            "target_port": 40200,
        })
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.bind_port, 40100)
        self.assertEqual(cfg.target_port, 40200)


class AdapterStatusTests(unittest.TestCase):
    def test_disabled_serialization(self):
        d = AdapterStatus.disabled().to_dict()
        self.assertEqual(d, {
            "enabled": False,
            "status": "disabled",
        })

    def test_ready_serialization(self):
        status = AdapterStatus(
            enabled=True,
            status=ADAPTER_STATUS_READY,
            adapter_type="local_udp_bridge",
            bind_host="127.0.0.1",
            bind_port=40100,
            target_host="127.0.0.1",
            target_port=40200,
            counters=AdapterCounters(),
        )
        d = status.to_dict()
        self.assertTrue(d["enabled"])
        self.assertEqual(d["status"], "ready")
        self.assertEqual(d["adapter_type"], "local_udp_bridge")
        self.assertEqual(d["bind_host"], "127.0.0.1")
        self.assertEqual(d["bind_port"], 40100)
        self.assertEqual(d["target_host"], "127.0.0.1")
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

    def test_error_serialization(self):
        status = AdapterStatus(
            enabled=True,
            status=ADAPTER_STATUS_ERROR,
            bind_port=40100,
            target_port=40200,
            error={
                "code": "ADAPTER_BIND_FAILED",
                "message": "Failed to bind UDP socket to 127.0.0.1:40100",
            },
        )
        d = status.to_dict()
        self.assertEqual(d["status"], "error")
        self.assertEqual(d["error"]["code"], "ADAPTER_BIND_FAILED")
        self.assertEqual(d["error"]["message"], "Failed to bind UDP socket to 127.0.0.1:40100")

    def test_counter_field_names_exact(self):
        d = AdapterCounters().to_dict()
        self.assertEqual(set(d.keys()), {
            "packets_from_game",
            "packets_to_transport",
            "packets_from_transport",
            "packets_to_game",
            "bytes_from_game",
            "bytes_to_transport",
            "bytes_from_transport",
            "bytes_to_game",
        })

    def test_counter_bytes_serialize(self):
        d = AdapterCounters(
            packets_from_game=5,
            packets_to_transport=3,
            packets_from_transport=4,
            packets_to_game=5,
            bytes_from_game=500,
            bytes_to_transport=300,
            bytes_from_transport=400,
            bytes_to_game=500,
        ).to_dict()
        self.assertEqual(d["bytes_from_game"], 500)
        self.assertEqual(d["bytes_to_transport"], 300)
        self.assertEqual(d["bytes_from_transport"], 400)
        self.assertEqual(d["bytes_to_game"], 500)


class SessionStatsTests(unittest.TestCase):
    def test_defaults(self):
        s = SessionStats()
        self.assertEqual(s.packets_from_game, 0)
        self.assertEqual(s.packets_to_transport, 0)
        self.assertFalse(s.has_error)

    def test_to_dict(self):
        s = SessionStats(
            packets_from_game=5,
            packets_to_transport=3,
            packets_from_transport=4,
            packets_to_game=5,
            has_error=False,
        )
        d = s.to_dict()
        self.assertEqual(d["packets_from_game"], 5)
        self.assertEqual(d["packets_to_transport"], 3)
        self.assertEqual(d["packets_from_transport"], 4)
        self.assertEqual(d["packets_to_game"], 5)
        self.assertFalse(d["has_error"])

    def test_to_dict_has_error(self):
        s = SessionStats(has_error=True)
        d = s.to_dict()
        self.assertTrue(d["has_error"])


class SessionInfoTests(unittest.TestCase):
    def _make_info(self) -> SessionInfo:
        return SessionInfo(
            session_id="s_abc123def456",
            role="create",
            status="running",
            room_id="ABC234",
            player_name="TestPlayer",
            server_host="192.168.1.10",
            server_port=9000,
            server_udp_port=9001,
            adapter_host="127.0.0.1",
            adapter_port=40000,
            created_at=1000.0,
            updated_at=1000.0,
            stats=SessionStats(packets_from_game=10),
        )

    def test_to_dict_includes_expected_fields(self):
        info = self._make_info()
        d = info.to_dict()
        self.assertEqual(d["session_id"], "s_abc123def456")
        self.assertEqual(d["role"], "create")
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["room_id"], "ABC234")
        self.assertEqual(d["player_name"], "TestPlayer")
        self.assertEqual(d["server_host"], "192.168.1.10")
        self.assertEqual(d["server_port"], 9000)
        self.assertEqual(d["server_udp_port"], 9001)
        self.assertEqual(d["adapter_host"], "127.0.0.1")
        self.assertEqual(d["adapter_port"], 40000)
        self.assertEqual(d["game_server_host"], "127.0.0.1")
        self.assertNotIn("game_server_port", d)
        self.assertTrue(d["force_relay"])
        self.assertEqual(d["created_at"], 1000.0)
        self.assertEqual(d["updated_at"], 1000.0)

    def test_to_dict_includes_stats(self):
        info = self._make_info()
        d = info.to_dict()
        self.assertIn("stats", d)
        self.assertEqual(d["stats"]["packets_from_game"], 10)

    def test_to_dict_no_error_when_none(self):
        info = self._make_info()
        d = info.to_dict()
        self.assertNotIn("error", d)

    def test_to_dict_includes_error(self):
        info = self._make_info()
        info.error = {"code": "TEST", "message": "err"}
        d = info.to_dict()
        self.assertIn("error", d)
        self.assertEqual(d["error"]["code"], "TEST")

    def test_to_dict_no_stats_when_none(self):
        info = self._make_info()
        info.stats = None
        d = info.to_dict()
        self.assertNotIn("stats", d)

    def test_defaults(self):
        info = SessionInfo(session_id="s_test", role="create", status="idle")
        self.assertEqual(info.server_port, 0)
        self.assertEqual(info.adapter_host, "127.0.0.1")
        self.assertEqual(info.adapter_port, 0)
        self.assertEqual(info.game_server_host, "127.0.0.1")
        self.assertIsNone(info.room_id)
        self.assertIsNone(info.stats)
        self.assertIsNone(info.adapter_config)
        self.assertIsNone(info.adapter_status)

    def test_to_dict_omits_adapter_status_when_unconfigured(self):
        info = self._make_info()
        d = info.to_dict()
        self.assertNotIn("adapter_status", d)

    def test_to_dict_includes_adapter_status_when_configured(self):
        info = self._make_info()
        info.adapter_status = AdapterStatus.disabled()
        d = info.to_dict()
        self.assertEqual(d["adapter_status"], {
            "enabled": False,
            "status": "disabled",
        })


if __name__ == "__main__":
    unittest.main()
