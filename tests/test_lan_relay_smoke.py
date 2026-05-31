#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for tools/lan_relay_smoke.py — argument parsing and static boundaries.
No real network tests.
"""

import os
import sys
import unittest

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure repo root is on path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLanRelaySmokeArgParse(unittest.TestCase):
    """Argument parsing tests for lan_relay_smoke.py."""

    @classmethod
    def setUpClass(cls):
        from tools.lan_relay_smoke import _parse_args
        cls._parse_args = _parse_args

    def _parse(self, *argv):
        old = sys.argv
        sys.argv = ["lan_relay_smoke.py"] + list(argv)
        try:
            return type(self)._parse_args()
        finally:
            sys.argv = old

    def test_create_role_minimal(self):
        args = self._parse("--role", "create", "--server-host", "192.168.1.10")
        self.assertEqual(args.role, "create")
        self.assertEqual(args.server_host, "192.168.1.10")
        self.assertEqual(args.server_port, 9000)
        self.assertEqual(args.server_udp_port, 9001)
        self.assertIsNone(args.room_id)

    def test_create_role_all_options(self):
        args = self._parse(
            "--role", "create",
            "--server-host", "10.0.0.1",
            "--server-port", "8000",
            "--server-udp-port", "8001",
            "--player-name", "CreatorA",
            "--bind-host", "0.0.0.0",
            "--bind-port", "5000",
        )
        self.assertEqual(args.role, "create")
        self.assertEqual(args.server_host, "10.0.0.1")
        self.assertEqual(args.server_port, 8000)
        self.assertEqual(args.server_udp_port, 8001)
        self.assertEqual(args.player_name, "CreatorA")
        self.assertEqual(args.bind_host, "0.0.0.0")
        self.assertEqual(args.bind_port, 5000)

    def test_join_role_requires_room_id(self):
        with self.assertRaises(SystemExit):
            self._parse("--role", "join", "--server-host", "192.168.1.10")

    def test_join_role_with_room_id(self):
        args = self._parse(
            "--role", "join",
            "--server-host", "192.168.1.10",
            "--room-id", "ABC123",
        )
        self.assertEqual(args.role, "join")
        self.assertEqual(args.room_id, "ABC123")

    def test_join_role_defaults_game_target(self):
        args = self._parse(
            "--role", "join",
            "--server-host", "192.168.1.10",
            "--room-id", "XYZ789",
        )
        self.assertEqual(args.game_server_host, "127.0.0.1")
        self.assertEqual(args.game_server_port, 40100)

    def test_join_role_custom_game_target(self):
        args = self._parse(
            "--role", "join",
            "--server-host", "192.168.1.10",
            "--room-id", "TEST42",
            "--game-server-host", "10.0.0.5",
            "--game-server-port", "50000",
        )
        self.assertEqual(args.game_server_host, "10.0.0.5")
        self.assertEqual(args.game_server_port, 50000)

    def test_default_player_name(self):
        args = self._parse("--role", "create", "--server-host", "1.2.3.4")
        self.assertEqual(args.player_name, "SmokePlayer")

    def test_default_bind_host(self):
        args = self._parse("--role", "create", "--server-host", "1.2.3.4")
        self.assertEqual(args.bind_host, "127.0.0.1")

    def test_default_bind_port_zero(self):
        args = self._parse("--role", "create", "--server-host", "1.2.3.4")
        self.assertEqual(args.bind_port, 0)


class TestLanRelaySmokeStaticBoundaries(unittest.TestCase):
    """Verify that lan_relay_smoke.py respects protocol/architectural boundaries."""

    @classmethod
    def setUpClass(cls):
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "lan_relay_smoke.py",
        )
        with open(script_path, "r", encoding="utf-8") as f:
            cls.source = f.read()

    def test_no_json_import(self):
        self.assertNotIn("import json", self.source,
                         "lan_relay_smoke.py must not import json directly")
        self.assertNotIn("from json", self.source,
                         "lan_relay_smoke.py must not import from json")

    def test_no_build_relay_packet(self):
        self.assertNotIn("_build_relay_packet", self.source,
                         "lan_relay_smoke.py must not call _build_relay_packet")

    def test_no_send_udp_to_relay(self):
        self.assertNotIn("_send_udp_to_relay", self.source,
                         "lan_relay_smoke.py must not call _send_udp_to_relay")

    def test_no_subprocess_spawn(self):
        """Helper must not spawn external processes (subprocess, os.system, etc.)."""
        self.assertNotIn("subprocess", self.source,
                         "lan_relay_smoke.py must not use subprocess")
        self.assertNotIn("os.system", self.source,
                         "lan_relay_smoke.py must not use os.system")
        self.assertNotIn("os.popen", self.source,
                         "lan_relay_smoke.py must not use os.popen")
        self.assertNotIn("os.startfile", self.source,
                         "lan_relay_smoke.py must not use os.startfile")
        self.assertNotIn("Popen", self.source,
                         "lan_relay_smoke.py must not use Popen")

    def test_no_import_of_server_module(self):
        """Helper must not import server module."""
        self.assertNotIn("import server", self.source,
                         "lan_relay_smoke.py must not import server")

    def test_no_import_of_game_tool_modules(self):
        """Helper must not import udp_game_server or udp_game_client modules."""
        source_no_strings = self.source
        self.assertNotIn("import udp_game_server", source_no_strings,
                         "lan_relay_smoke.py must not import udp_game_server")
        self.assertNotIn("import udp_game_client", source_no_strings,
                         "lan_relay_smoke.py must not import udp_game_client")


class TestLanRelaySmokeFailureCleanup(unittest.TestCase):
    """Verify that the failure-path cleanup bug (UnboundLocalError) is fixed."""

    @classmethod
    def setUpClass(cls):
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "lan_relay_smoke.py",
        )
        with open(script_path, "r", encoding="utf-8") as f:
            cls.source = f.read()

    def test_transport_initialized_to_none_before_try(self):
        """transport must be set to None before try in both _run_create and _run_join."""
        # Count occurrences: expect at least 2 (one per function)
        count = self.source.count("transport = None")
        self.assertGreaterEqual(count, 2,
                                "transport = None must appear before try in "
                                "_run_create and _run_join")

    def test_adapter_initialized_to_none_before_try(self):
        """adapter must be set to None before try in both _run_create and _run_join."""
        count = self.source.count("adapter = None")
        self.assertGreaterEqual(count, 2,
                                "adapter = None must appear before try in "
                                "_run_create and _run_join")

    def test_finally_guards_adapter_not_none_for_counters(self):
        """finally must check adapter is not None before _format_counters."""
        self.assertIn("if adapter is not None:\n            print(f\"[COUNTERS]",
                      self.source,
                      "finally must guard _format_counters with 'if adapter is not None'")

    def test_finally_guards_adapter_not_none_for_stop(self):
        """finally must check adapter is not None before adapter.stop()."""
        count = self.source.count("if adapter is not None:")
        # Each function has 2 such guards (counters + stop) = 4 total
        self.assertGreaterEqual(count, 4,
                                "Expected at least 4 'if adapter is not None:' guards "
                                "(2 per function: counters + stop)")

    def test_finally_guards_transport_not_none_for_close(self):
        """finally must check transport is not None before transport.close()."""
        count = self.source.count("if transport is not None:")
        self.assertGreaterEqual(count, 2,
                                "Expected at least 2 'if transport is not None:' guards "
                                "(1 per function)")

    def test_fallback_message_when_adapter_not_started(self):
        """finally must print fallback when adapter is None."""
        self.assertIn("[COUNTERS] adapter was not started", self.source,
                      "finally must handle adapter-is-None fallback message")


class TestLanRelaySmokeFormatCounters(unittest.TestCase):
    """Unit tests for _format_counters with fake adapters (no real networking)."""

    def test_format_counters_all_zeros(self):
        from tools.lan_relay_smoke import _format_counters

        class FakeAdapter:
            packets_from_game = 0
            packets_to_transport = 0
            packets_from_transport = 0
            packets_to_game = 0

        result = _format_counters(FakeAdapter())
        self.assertIn("packets_from_game=0", result)
        self.assertIn("packets_to_transport=0", result)
        self.assertIn("packets_from_transport=0", result)
        self.assertIn("packets_to_game=0", result)

    def test_format_counters_with_values(self):
        from tools.lan_relay_smoke import _format_counters

        class FakeAdapter:
            packets_from_game = 10
            packets_to_transport = 10
            packets_from_transport = 5
            packets_to_game = 5

        result = _format_counters(FakeAdapter())
        self.assertIn("packets_from_game=10", result)
        self.assertIn("packets_to_transport=10", result)
        self.assertIn("packets_from_transport=5", result)
        self.assertIn("packets_to_game=5", result)

    def test_format_counters_mixed(self):
        from tools.lan_relay_smoke import _format_counters

        class FakeAdapter:
            packets_from_game = 7
            packets_to_transport = 7
            packets_from_transport = 3
            packets_to_game = 3

        result = _format_counters(FakeAdapter())
        self.assertIn("packets_from_game=7", result)
        self.assertIn("packets_from_transport=3", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
