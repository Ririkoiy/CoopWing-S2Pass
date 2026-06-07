#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests and static boundary tests for S2Pass network_core.py.
"""

import sys
import os
import asyncio

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import json
import ast
from pathlib import Path

from network_core import (
    S2PassClientCore,
    S2PassConfig,
    S2PassEvent,
    EVT_ROOM_CREATED,
    EVT_ROOM_JOINED,
    EVT_ROOM_UPDATED,
    EVT_RELAY_ENABLED,
    EVT_PARTICIPANT_JOINED,
    EVT_PARTICIPANT_LEFT,
    EVT_ROOM_READY,
    EVT_ROOM_CLOSED,
)

class TestRelayIpFallback(unittest.TestCase):
    """
    涓€銆乺elay_ip fallback 娴嬭瘯
    娴嬭瘯 S2PassClientCore._resolve_relay_host(relay_ip, fallback_host)
    """
    def test_relay_ip_fallback(self):
        fallback_host = "fallback.example.com"

        # Test cases: (relay_ip, expected_resolved_host, should_fallback)
        cases = [
            (None, fallback_host, True),
            ("", fallback_host, True),
            ("0.0.0.0", fallback_host, True),
            ("::", fallback_host, True),
            ("127.0.0.1", fallback_host, True),
            ("127.1.2.3", fallback_host, True),
            ("192.168.1.10", "192.168.1.10", False),
            ("120.27.210.184", "120.27.210.184", False),
            ("example.com", "example.com", False),
        ]

        for relay_ip, expected_host, should_fallback in cases:
            with self.subTest(relay_ip=relay_ip):
                resolved_host, fallback_reason = S2PassClientCore._resolve_relay_host(relay_ip, fallback_host)
                self.assertEqual(resolved_host, expected_host)
                if should_fallback:
                    self.assertIsNotNone(fallback_reason)
                else:
                    self.assertIsNone(fallback_reason)


class TestProtocolBuilders(unittest.TestCase):
    """
    浜屻€佸崗璁瀯閫犳祴璇?    """
    def setUp(self):
        self.config = S2PassConfig(
            host="127.0.0.1",
            player_name="Alice",
            room_id="ABC123",
            role="create",
        )
        self.core = S2PassClientCore(self.config)
        self.core.room_id = "ABC123"
        self.core.player_id = "P1"
        self.core.relay_token = "TOKEN123"

    def test_build_create_room(self):
        line = self.core._build_create_room()
        self.assertTrue(line.endswith("\n"))

        obj = json.loads(line.strip())
        self.assertEqual(obj.get("type"), "CREATE_ROOM")
        payload = obj.get("payload", {})
        self.assertEqual(payload.get("player_name"), "Alice")

    def test_build_join_room(self):
        self.core.room_id = "ABC123"
        line = self.core._build_join_room()
        self.assertTrue(line.endswith("\n"))

        obj = json.loads(line.strip())
        self.assertEqual(obj.get("type"), "JOIN_ROOM")
        payload = obj.get("payload", {})
        self.assertEqual(payload.get("room_id"), "ABC123")
        self.assertEqual(payload.get("player_name"), "Alice")

    def test_build_heartbeat(self):
        line = self.core._build_heartbeat()
        self.assertTrue(line.endswith("\n"))

        obj = json.loads(line.strip())
        self.assertEqual(obj.get("type"), "HEARTBEAT")
        payload = obj.get("payload", {})
        self.assertIsInstance(payload.get("timestamp"), int)

    def test_build_p2p_failed(self):
        line = self.core._build_p2p_failed("TIMEOUT")
        self.assertTrue(line.endswith("\n"))

        obj = json.loads(line.strip())
        self.assertEqual(obj.get("type"), "P2P_FAILED")
        payload = obj.get("payload", {})
        self.assertEqual(payload.get("room_id"), "ABC123")
        self.assertEqual(payload.get("reason"), "TIMEOUT")

    def test_build_leave_room(self):
        line = self.core._build_leave_room()
        self.assertTrue(line.endswith("\n"))

        obj = json.loads(line.strip())
        self.assertEqual(obj.get("type"), "LEAVE_ROOM")
        payload = obj.get("payload", {})
        self.assertEqual(payload.get("room_id"), "ABC123")

    def test_build_udp_reg(self):
        reg_bytes = self.core._build_udp_reg()
        self.assertTrue(reg_bytes.startswith(b"REG\n"))

        json_bytes = reg_bytes[4:]
        obj = json.loads(json_bytes.decode('utf-8'))
        self.assertEqual(obj.get("player_id"), "P1")
        self.assertEqual(obj.get("room_id"), "ABC123")

    def test_build_relay_packet(self):
        self.core.relay_token = "TOKEN123"
        self.core.player_id = "P1"
        binary_payload = b"hello"

        relay_bytes = self.core._build_relay_packet(binary_payload)
        prefix = b"RELAY\n"
        self.assertTrue(relay_bytes.startswith(prefix))

        remaining = relay_bytes[len(prefix):]
        newline_idx = remaining.find(b"\n")
        self.assertGreater(newline_idx, 0)

        json_header = json.loads(remaining[:newline_idx].decode('utf-8'))
        payload = remaining[newline_idx + 1:]

        self.assertEqual(json_header.get("relay_token"), "TOKEN123")
        self.assertEqual(json_header.get("player_id"), "P1")
        self.assertEqual(payload, b"hello")

    def test_build_create_room_v2_uses_locked_fields(self):
        config = S2PassConfig(
            host="127.0.0.1",
            player_name="Alice",
            protocol_version=2,
            max_players=4,
        )
        core = S2PassClientCore(config)

        obj = json.loads(core._build_create_room().strip())

        self.assertEqual(obj.get("type"), "CREATE_ROOM")
        self.assertEqual(
            obj.get("payload"),
            {
                "player_name": "Alice",
                "protocol_version": 2,
                "max_players": 4,
            },
        )

    def test_build_join_room_v2_uses_locked_fields(self):
        config = S2PassConfig(
            host="127.0.0.1",
            player_name="Bob",
            room_id="ABC123",
            protocol_version=2,
            role="join",
        )
        core = S2PassClientCore(config)
        core.room_id = "ABC123"

        obj = json.loads(core._build_join_room().strip())

        self.assertEqual(obj.get("type"), "JOIN_ROOM")
        self.assertEqual(
            obj.get("payload"),
            {
                "room_id": "ABC123",
                "player_name": "Bob",
                "protocol_version": 2,
            },
        )


class TestV2EventMapping(unittest.TestCase):
    """
    v0.3-F: map v2 server TCP messages to client-side events.
    """
    def setUp(self):
        self.events = []
        self.core = S2PassClientCore(
            S2PassConfig(host="127.0.0.1", protocol_version=2),
            event_callback=self.events.append,
        )

    @staticmethod
    def _participant(player_id, player_name, is_host=False):
        return {
            "player_id": player_id,
            "player_name": player_name,
            "is_host": is_host,
        }

    def _event_types(self):
        return [event.type for event in self.events]

    def test_v2_room_created_maps_participants_and_room_metadata(self):
        participants = [self._participant("p_aaaaaaaaaaaa", "Alice", True)]

        asyncio.run(self.core._handle_tcp_message("ROOM_CREATED", {
            "room_id": "ABC123",
            "player_id": "p_aaaaaaaaaaaa",
            "protocol_version": 2,
            "max_players": 4,
            "participants": participants,
            "participant_count": 1,
        }))

        self.assertEqual(self._event_types(), [EVT_ROOM_CREATED])
        event = self.events[0]
        self.assertEqual(event.data["room_id"], "ABC123")
        self.assertEqual(event.data["player_id"], "p_aaaaaaaaaaaa")
        self.assertEqual(event.data["protocol_version"], 2)
        self.assertEqual(event.data["max_players"], 4)
        self.assertEqual(event.data["participants"], participants)
        self.assertEqual(event.data["participant_count"], 1)
        self.assertEqual(self.core.participants, participants)

    def test_v2_room_joined_maps_participants_room_and_player(self):
        participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_bbbbbbbbbbbb", "Bob"),
        ]

        asyncio.run(self.core._handle_tcp_message("ROOM_JOINED", {
            "room_id": "ABC123",
            "player_id": "p_bbbbbbbbbbbb",
            "protocol_version": 2,
            "max_players": 4,
            "participants": participants,
            "participant_count": 2,
        }))

        self.assertEqual(self._event_types(), [EVT_ROOM_JOINED])
        event = self.events[0]
        self.assertEqual(event.data["room_id"], "ABC123")
        self.assertEqual(event.data["player_id"], "p_bbbbbbbbbbbb")
        self.assertEqual(event.data["protocol_version"], 2)
        self.assertEqual(event.data["max_players"], 4)
        self.assertEqual(event.data["participants"], participants)
        self.assertEqual(event.data["participant_count"], 2)

    def test_v2_room_updated_participant_joined_maps_full_and_specific_event(self):
        self.core.room_id = "ABC123"
        self.core.participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_bbbbbbbbbbbb", "Bob"),
        ]
        participants = self.core.participants + [
            self._participant("p_cccccccccccc", "Carol"),
        ]

        self.core._handle_room_updated({
            "room_id": "ABC123",
            "event": "participant_joined",
            "participant_count": 3,
            "max_players": 4,
            "host_player_id": "p_aaaaaaaaaaaa",
            "participants": participants,
            "server_time": 1716192000.25,
        })

        self.assertEqual(self._event_types(), [
            EVT_ROOM_UPDATED,
            EVT_PARTICIPANT_JOINED,
        ])
        updated = self.events[0]
        joined = self.events[1]
        self.assertEqual(updated.data["event"], "participant_joined")
        self.assertEqual(updated.data["participants"], participants)
        self.assertEqual(updated.data["participant_count"], 3)
        self.assertEqual(updated.data["max_players"], 4)
        self.assertEqual(updated.data["host_player_id"], "p_aaaaaaaaaaaa")
        self.assertEqual(updated.data["room_id"], "ABC123")
        self.assertEqual(updated.data["server_time"], 1716192000.25)
        self.assertEqual(joined.data["player_id"], "p_cccccccccccc")

    def test_v2_room_updated_participant_left_maps_full_and_specific_event(self):
        self.core.room_id = "ABC123"
        self.core.participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_bbbbbbbbbbbb", "Bob"),
            self._participant("p_cccccccccccc", "Carol"),
        ]
        participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_cccccccccccc", "Carol"),
        ]

        self.core._handle_room_updated({
            "room_id": "ABC123",
            "event": "participant_left",
            "participant_count": 2,
            "max_players": 4,
            "host_player_id": "p_aaaaaaaaaaaa",
            "participants": participants,
            "server_time": 1716192001.0,
        })

        self.assertEqual(self._event_types(), [
            EVT_ROOM_UPDATED,
            EVT_PARTICIPANT_LEFT,
        ])
        self.assertEqual(self.events[1].data["player_id"], "p_bbbbbbbbbbbb")
        self.assertEqual(self.core.participants, participants)

    def test_v2_room_updated_room_ready_maps_ready_event(self):
        participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_bbbbbbbbbbbb", "Bob"),
        ]

        self.core._handle_room_updated({
            "room_id": "ABC123",
            "event": "room_ready",
            "participant_count": 2,
            "max_players": 4,
            "host_player_id": "p_aaaaaaaaaaaa",
            "participants": participants,
            "server_time": 1716192002.0,
        })

        self.assertEqual(self._event_types(), [EVT_ROOM_UPDATED, EVT_ROOM_READY])
        self.assertEqual(self.events[1].data["room_id"], "ABC123")

    def test_v2_room_updated_room_closed_maps_closed_event(self):
        participants = [self._participant("p_cccccccccccc", "Carol")]

        self.core._handle_room_updated({
            "room_id": "ABC123",
            "event": "room_closed",
            "participant_count": 1,
            "max_players": 4,
            "host_player_id": "p_aaaaaaaaaaaa",
            "participants": participants,
            "server_time": 1716192003.0,
        })

        self.assertEqual(self._event_types(), [EVT_ROOM_UPDATED, EVT_ROOM_CLOSED])
        self.assertEqual(self.events[1].data["room_id"], "ABC123")

    def test_v2_relay_enabled_maps_player_and_relay_target(self):
        self.core.player_id = "p_bbbbbbbbbbbb"

        asyncio.run(self.core._handle_tcp_message("RELAY_ENABLED", {
            "room_id": "ABC123",
            "relay_token": "rtk_abcdef0123456789",
            "relay_ip": "120.27.210.184",
            "relay_port": 9001,
        }))

        self.assertEqual(self._event_types(), [EVT_RELAY_ENABLED])
        event = self.events[0]
        self.assertEqual(event.data["player_id"], "p_bbbbbbbbbbbb")
        self.assertEqual(event.data["relay_token"], "rtk_abcdef0123456789")
        self.assertEqual(event.data["relay_ip"], "120.27.210.184")
        self.assertEqual(event.data["relay_port"], 9001)
        self.assertEqual(event.data["relay_target_host"], "120.27.210.184")
        self.assertEqual(event.data["relay_target_port"], 9001)

    def test_v2_room_updated_identical_consecutive_update_is_deduplicated(self):
        participants = [
            self._participant("p_aaaaaaaaaaaa", "Alice", True),
            self._participant("p_bbbbbbbbbbbb", "Bob"),
        ]
        payload = {
            "room_id": "ABC123",
            "event": "room_ready",
            "participant_count": 2,
            "max_players": 4,
            "host_player_id": "p_aaaaaaaaaaaa",
            "participants": participants,
            "server_time": 1716192004.0,
        }

        self.core._handle_room_updated(payload)
        self.core._handle_room_updated(payload)

        self.assertEqual(self._event_types(), [EVT_ROOM_UPDATED, EVT_ROOM_READY])


class TestEventEmitter(unittest.TestCase):
    """
    涓夈€佷簨浠惰緭鍑烘祴璇?    """
    def test_emit_callback(self):
        events = []
        def callback(event):
            events.append(event)

        config = S2PassConfig(host="127.0.0.1")
        core = S2PassClientCore(config, event_callback=callback)

        core._emit("TEST", "hello", {"x": 1})
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertIsInstance(event, S2PassEvent)
        self.assertEqual(event.type, "TEST")
        self.assertEqual(event.message, "hello")
        self.assertEqual(event.data, {"x": 1})
        self.assertIsInstance(event.timestamp, float)

    def test_emit_queue(self):
        async def run_queue_test():
            queue = asyncio.Queue()
            config = S2PassConfig(host="127.0.0.1")
            core = S2PassClientCore(config, event_queue=queue)

            core._emit("TEST", "hello", {"x": 1})
            self.assertEqual(queue.qsize(), 1)

            event = await queue.get()
            self.assertIsInstance(event, S2PassEvent)
            self.assertEqual(event.type, "TEST")
            self.assertEqual(event.message, "hello")
            self.assertEqual(event.data, {"x": 1})
            self.assertIsInstance(event.timestamp, float)

        asyncio.run(run_queue_test())


class TestStatsFormatting(unittest.TestCase):
    """
    鍥涖€佺粺璁℃牸寮忔祴璇?    """
    def test_stats_sender(self):
        config = S2PassConfig(host="127.0.0.1", send_test=True)
        core = S2PassClientCore(config)
        core.packets_sent_count = 100
        core.packets_received_count = 90
        core.received_echoes = {0: 0.01, 1: 0.02}

        stats = core._get_stats()
        self.assertEqual(stats.get("mode"), "sender")
        self.assertAlmostEqual(stats.get("loss_rate"), 10.0)
        self.assertAlmostEqual(stats.get("avg_rtt_ms"), 15.0)

        formatted = core._format_stats(stats)
        self.assertEqual(
            formatted,
            "packets sent: 100, echo received: 90, loss: 10.00%, avg RTT: 15.00 ms"
        )

    def test_stats_responder(self):
        config = S2PassConfig(host="127.0.0.1", send_test=False)
        core = S2PassClientCore(config)
        core.packets_received_count = 10
        core.packets_echoed_count = 8

        stats = core._get_stats()
        self.assertEqual(stats.get("mode"), "responder")
        self.assertEqual(stats.get("packets_received"), 10)
        self.assertEqual(stats.get("packets_echoed"), 8)

        formatted = core._format_stats(stats)
        self.assertEqual(
            formatted,
            "packets received: 10, packets echoed: 8"
        )


class TestCliStaticBoundary(unittest.TestCase):
    """
    浜斻€丆LI 杈圭晫闈欐€佹祴璇?    """
    def test_cli_client_boundaries(self):
        project_root = Path(__file__).resolve().parent.parent
        cli_client_path = project_root / "cli_client.py"

        self.assertTrue(cli_client_path.exists(), f"cli_client.py not found at {cli_client_path}")
        cli_text = cli_client_path.read_text(encoding="utf-8")

        # 1. 涓嶅寘鍚?json 鐩稿叧瀵煎叆鍜屾柟娉曡皟鐢?        self.assertNotIn("import json", cli_text)
        self.assertNotIn("json.dumps", cli_text)
        self.assertNotIn("json.loads", cli_text)

        # 2. 鍖呭惈鏍稿績绫?        self.assertIn("S2PassClientCore", cli_text)
        self.assertIn("S2PassConfig", cli_text)

        # 3. 涓嶅簲鍖呭惈鍗忚鏋勯€犲瓧闈㈤噺
        # Collect all string/bytes literals in AST
        tree = ast.parse(cli_text)
        literals = set()
        if hasattr(ast, 'Constant'):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant):
                    if isinstance(node.value, (str, bytes)):
                        literals.add(node.value)
        else:
            # Fallback for Python versions older than 3.8
            for node in ast.walk(tree):
                if isinstance(node, getattr(ast, 'Str')):
                    literals.add(node.s)
                elif isinstance(node, getattr(ast, 'Bytes')):
                    literals.add(node.b)

        forbidden_exact = {
            "CREATE_ROOM", "JOIN_ROOM", "P2P_FAILED", "RELAY\n", "REG\n",
            b"RELAY\n", b"REG\n"
        }
        for forbidden in forbidden_exact:
            self.assertNotIn(forbidden, literals)

        # Substrings check for CREATE_ROOM, JOIN_ROOM, RELAY\n, REG\n
        forbidden_substrings = ["CREATE_ROOM", "JOIN_ROOM", "RELAY\n", "REG\n"]
        for sub in forbidden_substrings:
            self.assertNotIn(sub, cli_text)


class TestNetworkCoreStaticBoundary(unittest.TestCase):
    """
    鍏€乶etwork_core 杈撳嚭杈圭晫闈欐€佹祴璇?    """
    def test_network_core_no_print(self):
        project_root = Path(__file__).resolve().parent.parent
        network_core_path = project_root / "network_core.py"

        self.assertTrue(network_core_path.exists(), f"network_core.py not found at {network_core_path}")

        def has_print_call(path: Path) -> bool:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "print":
                        return True
            return False

        self.assertFalse(has_print_call(network_core_path), "network_core.py contains actual print() calls")


class TestConfigDefaults(unittest.TestCase):
    """
    涓冦€侀厤缃粯璁ゅ€兼祴璇?    """
    def test_config_defaults(self):
        config = S2PassConfig(host="127.0.0.1")
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9000)
        self.assertEqual(config.udp_port, 9001)
        self.assertEqual(config.player_name, "Player")
        self.assertEqual(config.role, "create")
        self.assertFalse(config.force_relay)
        self.assertEqual(config.lobby_timeout, 300)
        self.assertEqual(config.start_delay, 1.0)
        self.assertFalse(config.send_test)
        self.assertEqual(config.pps, 10)
        self.assertEqual(config.duration, 10)
        self.assertEqual(config.packet_size, 64)
        self.assertIsNone(config.protocol_version)
        self.assertEqual(config.max_players, 4)
        self.assertEqual(config.udp_reg_count, 3)
        self.assertEqual(config.udp_reg_interval, 0.05)


class TestPayloadAPI(unittest.TestCase):
    """
    鍏€丳ayload API 娴嬭瘯 (is_payload_mode / send_payload / set_payload_callback)
    """
    def test_payload_mode_defaults(self):
        config = S2PassConfig(host="127.0.0.1")
        self.assertFalse(config.is_payload_mode)

    def test_mutual_exclusion(self):
        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True, send_test=True)
        with self.assertRaises(ValueError):
            S2PassClientCore(config)

    def test_set_payload_callback(self):
        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True)
        core = S2PassClientCore(config)
        callback = lambda data: None
        core.set_payload_callback(callback)
        self.assertEqual(core._payload_callback, callback)

    def test_send_payload_before_relay_ready_raises(self):
        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True)
        core = S2PassClientCore(config)

        # Test 1: No transport, no token, no target -> raises
        with self.assertRaises(RuntimeError) as ctx:
            core.send_payload(b"hello")
        self.assertIn("Relay path is not ready", str(ctx.exception))

        # Test 2: Has token, no target -> raises
        core.relay_token = "rtk_123"
        with self.assertRaises(RuntimeError) as ctx:
            core.send_payload(b"hello")
        self.assertIn("Relay path is not ready", str(ctx.exception))

        # Test 3: Has token and target, but no UDP transport -> raises
        core._relay_target_host = "1.2.3.4"
        core._relay_target_port = 12345
        with self.assertRaises(RuntimeError) as ctx:
            core.send_payload(b"hello")
        self.assertIn("UDP transport is not initialized", str(ctx.exception))

    def test_send_payload_success(self):
        class MockUDPTransport:
            def __init__(self):
                self.sent_packets = []
            def sendto(self, packet, addr):
                self.sent_packets.append((packet, addr))

        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True)
        core = S2PassClientCore(config)
        core.player_id = "p_111111111111"
        core.relay_token = "rtk_abcdef0123456789"
        core._relay_target_host = "120.27.210.184"
        core._relay_target_port = 9001

        mock_transport = MockUDPTransport()
        core._udp_transport = mock_transport

        raw_payload = b"raw game payload"
        core.send_payload(raw_payload)

        self.assertEqual(len(mock_transport.sent_packets), 1)
        sent_packet, sent_addr = mock_transport.sent_packets[0]
        self.assertEqual(sent_addr, ("120.27.210.184", 9001))

        # Verify packet format: b"RELAY\n" + json_header + b"\n" + raw_payload
        prefix = b"RELAY\n"
        self.assertTrue(sent_packet.startswith(prefix))
        remaining = sent_packet[len(prefix):]
        newline_idx = remaining.find(b"\n")
        self.assertGreater(newline_idx, 0)

        import json
        header = json.loads(remaining[:newline_idx].decode('utf-8'))
        self.assertEqual(header.get("relay_token"), "rtk_abcdef0123456789")
        self.assertEqual(header.get("player_id"), "p_111111111111")

        payload_part = remaining[newline_idx + 1:]
        self.assertEqual(payload_part, raw_payload)

    def test_receive_relay_packet_invokes_callback(self):
        received_data = []
        def my_callback(data):
            received_data.append(data)

        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True)
        core = S2PassClientCore(config)
        core.relay_token = "rtk_abcdef0123456789"
        core.set_payload_callback(my_callback)

        # Construct a valid RELAY packet
        json_header = {
            "relay_token": "rtk_abcdef0123456789",
            "player_id": "p_222222222222"
        }
        header_bytes = json.dumps(json_header, separators=(',', ':')).encode('utf-8')
        raw_payload = b"\x00\x01\x02\x03\xff"
        packet = b"RELAY\n" + header_bytes + b"\n" + raw_payload

        # Invoke _handle_udp_packet
        core._handle_udp_packet(packet, ("1.2.3.4", 5678))

        self.assertEqual(received_data, [raw_payload])
        self.assertEqual(core.packets_received_count, 0) # Payload mode should not increment stats
        self.assertEqual(core.packets_echoed_count, 0)

    def test_send_payload_outside_payload_mode_raises(self):
        config = S2PassConfig(host="127.0.0.1", is_payload_mode=False)
        core = S2PassClientCore(config)
        with self.assertRaises(RuntimeError) as ctx:
            core.send_payload(b"hello")
        self.assertIn("not in payload mode", str(ctx.exception))

    def test_payload_callback_exception_propagates(self):
        def bad_callback(data):
            raise ValueError("bad payload")

        config = S2PassConfig(host="127.0.0.1", is_payload_mode=True)
        core = S2PassClientCore(config)
        core.relay_token = "rtk_abcdef0123456789"
        core.set_payload_callback(bad_callback)

        json_header = {
            "relay_token": "rtk_abcdef0123456789",
            "player_id": "p_222222222222"
        }
        header_bytes = json.dumps(json_header, separators=(',', ':')).encode('utf-8')
        packet = b"RELAY\n" + header_bytes + b"\n" + b"hello"

        with self.assertRaises(ValueError) as ctx:
            core._handle_udp_packet(packet, ("1.2.3.4", 5678))
        self.assertEqual(str(ctx.exception), "bad payload")

    def test_non_payload_echo_responder_preserved(self):
        class MockUDPTransport:
            def __init__(self):
                self.sent_packets = []
            def sendto(self, packet, addr):
                self.sent_packets.append((packet, addr))

        config = S2PassConfig(host="127.0.0.1", is_payload_mode=False, send_test=False)
        core = S2PassClientCore(config)
        core.player_id = "p_111111111111"
        core.relay_token = "rtk_abcdef0123456789"
        core._relay_target_host = "120.27.210.184"
        core._relay_target_port = 9001

        mock_transport = MockUDPTransport()
        core._udp_transport = mock_transport

        raw_payload = b"echo me back"
        core._on_relay_packet("p_222222222222", raw_payload)

        self.assertEqual(core.packets_received_count, 1)
        self.assertEqual(core.packets_echoed_count, 1)
        self.assertEqual(len(mock_transport.sent_packets), 1)

        sent_packet, sent_addr = mock_transport.sent_packets[0]
        self.assertEqual(sent_addr, ("120.27.210.184", 9001))

        # Verify prefix and inner payload
        prefix = b"RELAY\n"
        self.assertTrue(sent_packet.startswith(prefix))
        remaining = sent_packet[len(prefix):]
        newline_idx = remaining.find(b"\n")
        self.assertGreater(newline_idx, 0)

        import json
        header = json.loads(remaining[:newline_idx].decode('utf-8'))
        self.assertEqual(header.get("relay_token"), "rtk_abcdef0123456789")
        self.assertEqual(header.get("player_id"), "p_111111111111")

        payload_part = remaining[newline_idx + 1:]
        self.assertEqual(payload_part, raw_payload)


if __name__ == "__main__":
    unittest.main()
