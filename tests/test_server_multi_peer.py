#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests-first skeleton for v2 relay-only multi-peer rooms.

These tests define v2 multi-peer expected behavior. They are expected to fail
until implementation begins, so they are skipped to keep the default unittest
suite green during the v0.3-D acceptance-criteria phase.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as srv


PENDING_REASON = "v2 multi-peer implementation pending"


def _make_mock_writer() -> MagicMock:
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _last_message(writer: MagicMock) -> dict:
    return _messages(writer)[-1]


def _messages(writer: MagicMock) -> list[dict]:
    writer.write.assert_called()
    return [
        json.loads(call.args[0].decode("utf-8"))
        for call in writer.write.call_args_list
    ]


async def _create_room(
    server: srv.P2PServer,
    payload: dict,
    *,
    conn_key: str,
    client_ip: str = "127.0.0.1",
) -> tuple[str | None, MagicMock, list[dict]]:
    writer = _make_mock_writer()
    player_id = await server._handle_create_room(payload, writer, client_ip, conn_key)
    return player_id, writer, _messages(writer)


async def _join_room(
    server: srv.P2PServer,
    payload: dict,
    *,
    conn_key: str,
    client_ip: str = "127.0.0.1",
) -> tuple[str | None, MagicMock, list[dict]]:
    writer = _make_mock_writer()
    player_id = await server._handle_join_room(payload, writer, client_ip, conn_key)
    return player_id, writer, _messages(writer)


def _messages_of_type(writer: MagicMock, msg_type: str) -> list[dict]:
    return [message for message in _messages(writer) if message["type"] == msg_type]


def _udp_reg_payload(player_id: str, room_id: str) -> bytes:
    return json.dumps({"player_id": player_id, "room_id": room_id}).encode("utf-8")


def _relay_packet(relay_token: str, player_id: str, payload: bytes = b"hello") -> bytes:
    header = json.dumps(
        {"relay_token": relay_token, "player_id": player_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return b"RELAY\n" + header + b"\n" + payload


def _decode_relay_packet(data: bytes) -> tuple[dict, bytes]:
    prefix = b"RELAY\n"
    assert data.startswith(prefix)
    header_bytes, payload = data[len(prefix):].split(b"\n", 1)
    return json.loads(header_bytes.decode("utf-8")), payload


def _relay_raw(packet: bytes) -> memoryview:
    return memoryview(packet[len(b"RELAY\n"):])


def _make_udp_transport_mock() -> MagicMock:
    transport = MagicMock()
    transport.sendto = MagicMock()
    return transport


class TestV2ProtocolParsing(unittest.IsolatedAsyncioTestCase):
    """Expected v2 protocol parsing and creation behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def _create_room(
        self,
        payload: dict,
        *,
        server: srv.P2PServer | None = None,
        conn_key: str = "127.0.0.1:1000",
    ) -> tuple[str | None, MagicMock, dict]:
        target = server or self.server
        writer = _make_mock_writer()
        player_id = await target._handle_create_room(payload, writer, "127.0.0.1", conn_key)
        return player_id, writer, _last_message(writer)

    def _assert_v2_room_created(
        self,
        message: dict,
        *,
        player_name: str,
        max_players: int,
    ) -> None:
        self.assertEqual(message["type"], srv.MSG_ROOM_CREATED)
        payload = message["payload"]
        self.assertEqual(
            set(payload),
            {
                "room_id",
                "player_id",
                "protocol_version",
                "max_players",
                "participants",
                "participant_count",
            },
        )
        self.assertEqual(payload["protocol_version"], 2)
        self.assertEqual(payload["max_players"], max_players)
        self.assertEqual(payload["participant_count"], 1)
        self.assertEqual(len(payload["participants"]), 1)
        participant = payload["participants"][0]
        self.assertEqual(
            set(participant),
            {"player_id", "player_name", "is_host"},
        )
        self.assertEqual(participant["player_id"], payload["player_id"])
        self.assertEqual(participant["player_name"], player_name)
        self.assertIs(participant["is_host"], True)
        self.assertNotIn("participant_id", participant)

    async def test_v2_create_room_defaults_max_players_4(self) -> None:
        """A v2 CREATE_ROOM without max_players creates max_players=4."""
        player_id, _, message = await self._create_room(
            {"player_name": "Alice", "protocol_version": 2},
        )

        self.assertIsNotNone(player_id)
        self._assert_v2_room_created(message, player_name="Alice", max_players=4)
        room_id = message["payload"]["room_id"]
        room = self.server._rooms[room_id]
        self.assertEqual(room.protocol_version, 2)
        self.assertEqual(room.max_players, 4)
        self.assertIn(player_id, room.participants)

        _, _, legacy_missing = await self._create_room(
            {"player_name": "Legacy"},
            conn_key="127.0.0.1:1001",
        )
        legacy_payload = legacy_missing["payload"]
        self.assertEqual(set(legacy_payload), {"room_id", "player_id"})
        self.assertNotIn("protocol_version", legacy_payload)
        self.assertNotIn("max_players", legacy_payload)
        self.assertNotIn("participants", legacy_payload)

        _, _, legacy_explicit = await self._create_room(
            {"player_name": "LegacyOne", "protocol_version": 1, "max_players": 8},
            conn_key="127.0.0.1:1002",
        )
        explicit_payload = legacy_explicit["payload"]
        self.assertEqual(set(explicit_payload), {"room_id", "player_id"})
        self.assertNotIn("protocol_version", explicit_payload)
        self.assertNotIn("participants", explicit_payload)
        self.assertNotIn("max_players", explicit_payload)

    async def test_v2_create_room_accepts_max_players_2_to_8(self) -> None:
        """A v2 CREATE_ROOM accepts every integer max_players value from 2 to 8."""
        for max_players in range(2, 9):
            with self.subTest(max_players=max_players):
                server = srv.P2PServer()
                server._loop = asyncio.get_event_loop()
                player_id, _, message = await self._create_room(
                    {
                        "player_name": "Alice",
                        "protocol_version": 2,
                        "max_players": max_players,
                    },
                    server=server,
                )
                self.assertIsNotNone(player_id)
                self._assert_v2_room_created(
                    message,
                    player_name="Alice",
                    max_players=max_players,
                )

    async def test_v2_create_room_rejects_invalid_max_players(self) -> None:
        """Invalid v2 max_players values are rejected with 1005 INVALID_MESSAGE."""
        for max_players in (1, 9, 0, -1, "4", 4.5, True, None):
            with self.subTest(max_players=max_players):
                server = srv.P2PServer()
                server._loop = asyncio.get_event_loop()
                player_id, _, message = await self._create_room(
                    {
                        "player_name": "Alice",
                        "protocol_version": 2,
                        "max_players": max_players,
                    },
                    server=server,
                )
                self.assertIsNone(player_id)
                self.assertEqual(message["type"], srv.MSG_ERROR)
                self.assertEqual(message["payload"]["code"], 1005)

    async def test_unsupported_protocol_version_rejected(self) -> None:
        """Unsupported protocol_version values are rejected with 1011."""
        for protocol_version in (0, 3, 99, "2", 2.0, True, None):
            with self.subTest(protocol_version=protocol_version):
                server = srv.P2PServer()
                server._loop = asyncio.get_event_loop()
                player_id, _, message = await self._create_room(
                    {
                        "player_name": "Alice",
                        "protocol_version": protocol_version,
                    },
                    server=server,
                )
                self.assertIsNone(player_id)
                self.assertEqual(message["type"], srv.MSG_ERROR)
                self.assertEqual(message["payload"]["code"], 1011)


class TestV2RoomLifecycle(unittest.IsolatedAsyncioTestCase):
    """Expected v2 room lifecycle and participant snapshot behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def _create_v2_room(
        self,
        *,
        max_players: int = 4,
    ) -> tuple[str, str, MagicMock]:
        host_id, host_writer, messages = await _create_room(
            self.server,
            {
                "player_name": "Alice",
                "protocol_version": 2,
                "max_players": max_players,
            },
            conn_key="127.0.0.1:1000",
        )
        self.assertIsNotNone(host_id)
        room_id = messages[-1]["payload"]["room_id"]
        return room_id, host_id, host_writer

    async def _join_v2(
        self,
        room_id: str,
        player_name: str,
        *,
        conn_index: int,
    ) -> tuple[str, MagicMock, list[dict]]:
        player_id, writer, messages = await _join_room(
            self.server,
            {
                "room_id": room_id,
                "player_name": player_name,
                "protocol_version": 2,
            },
            conn_key=f"127.0.0.1:{1000 + conn_index}",
        )
        self.assertIsNotNone(player_id)
        assert player_id is not None
        return player_id, writer, messages

    def _assert_participants(
        self,
        payload: dict,
        expected_names: list[str],
    ) -> None:
        participants = payload["participants"]
        self.assertEqual([p["player_name"] for p in participants], expected_names)
        self.assertEqual(payload["participant_count"], len(expected_names))
        self.assertEqual(sum(1 for p in participants if p["is_host"]), 1)
        self.assertIs(participants[0]["is_host"], True)
        for participant in participants:
            self.assertEqual(set(participant), {"player_id", "player_name", "is_host"})
            self.assertNotIn("participant_id", participant)

    async def _flush_udp_reg_tasks(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _register_udp(
        self,
        player_id: str,
        room_id: str,
        addr: tuple[str, int],
    ) -> None:
        self.server.handle_udp_register(_udp_reg_payload(player_id, room_id), addr)
        await self._flush_udp_reg_tasks()

    async def test_v2_join_third_player_updates_participants(self) -> None:
        """The third v2 participant receives and triggers a 3-player snapshot."""
        room_id, _, _ = await self._create_v2_room()
        await self._join_v2(room_id, "Bob", conn_index=1)
        _, carol_writer, carol_messages = await self._join_v2(
            room_id,
            "Carol",
            conn_index=2,
        )

        joined = carol_messages[0]
        self.assertEqual(joined["type"], srv.MSG_ROOM_JOINED)
        self._assert_participants(joined["payload"], ["Alice", "Bob", "Carol"])
        self.assertEqual(joined["payload"]["protocol_version"], 2)
        self.assertEqual(joined["payload"]["max_players"], 4)
        self.assertEqual(
            [m["payload"]["event"] for m in _messages_of_type(carol_writer, srv.MSG_ROOM_UPDATED)],
            ["participant_joined"],
        )

    async def test_v2_join_fourth_player_updates_participants(self) -> None:
        """The fourth v2 participant fills a default v2 room."""
        room_id, _, _ = await self._create_v2_room()
        await self._join_v2(room_id, "Bob", conn_index=1)
        await self._join_v2(room_id, "Carol", conn_index=2)
        _, dave_writer, dave_messages = await self._join_v2(
            room_id,
            "Dave",
            conn_index=3,
        )

        joined = dave_messages[0]
        self.assertEqual(joined["type"], srv.MSG_ROOM_JOINED)
        self._assert_participants(joined["payload"], ["Alice", "Bob", "Carol", "Dave"])
        self.assertEqual(joined["payload"]["participant_count"], 4)
        self.assertEqual(
            [m["payload"]["event"] for m in _messages_of_type(dave_writer, srv.MSG_ROOM_UPDATED)],
            ["participant_joined"],
        )

    async def test_v2_room_full_rejects_fifth_player(self) -> None:
        """A default v2 room rejects the fifth participant with 1002 ROOM_FULL."""
        room_id, _, _ = await self._create_v2_room()
        await self._join_v2(room_id, "Bob", conn_index=1)
        await self._join_v2(room_id, "Carol", conn_index=2)
        await self._join_v2(room_id, "Dave", conn_index=3)

        eve_id, _, eve_messages = await _join_room(
            self.server,
            {"room_id": room_id, "player_name": "Eve", "protocol_version": 2},
            conn_key="127.0.0.1:1004",
        )
        self.assertIsNone(eve_id)
        self.assertEqual(eve_messages[-1]["type"], srv.MSG_ERROR)
        self.assertEqual(eve_messages[-1]["payload"]["code"], 1002)

        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()
        room2_id, _, _ = await self._create_v2_room(max_players=2)
        await self._join_v2(room2_id, "Bob", conn_index=5)
        carol_id, _, carol_messages = await _join_room(
            self.server,
            {"room_id": room2_id, "player_name": "Carol", "protocol_version": 2},
            conn_key="127.0.0.1:1006",
        )
        self.assertIsNone(carol_id)
        self.assertEqual(carol_messages[-1]["payload"]["code"], 1002)

    async def test_v2_participant_list_uses_player_id_not_participant_id(self) -> None:
        """Participant objects use player_id and never participant_id."""
        room_id, _, _ = await self._create_v2_room()
        await self._join_v2(room_id, "Bob", conn_index=1)
        _, _, messages = await self._join_v2(room_id, "Carol", conn_index=2)

        for message in messages:
            payload = message["payload"]
            if "participants" not in payload:
                continue
            for participant in payload["participants"]:
                self.assertIn("player_id", participant)
                self.assertNotIn("participant_id", participant)

    async def test_v2_room_updated_broadcast_on_join(self) -> None:
        """Every successful v2 join broadcasts ROOM_UPDATED participant_joined."""
        room_id, _, host_writer = await self._create_v2_room()
        _, bob_writer, _ = await self._join_v2(room_id, "Bob", conn_index=1)

        for writer in (host_writer, bob_writer):
            updates = _messages_of_type(writer, srv.MSG_ROOM_UPDATED)
            self.assertEqual(updates[0]["payload"]["event"], "participant_joined")
            self.assertEqual(updates[0]["payload"]["room_id"], room_id)
            self.assertEqual(updates[0]["payload"]["participant_count"], 2)
            self.assertEqual(updates[0]["payload"]["host_player_id"], self.server._rooms[room_id].creator_id)
            self._assert_participants(updates[0]["payload"], ["Alice", "Bob"])
            self.assertIsInstance(updates[0]["payload"]["server_time"], (int, float))

    async def test_v2_room_ready_event_at_two_participants(self) -> None:
        """Joining the second v2 participant emits room_ready."""
        room_id, _, host_writer = await self._create_v2_room()
        _, bob_writer, _ = await self._join_v2(room_id, "Bob", conn_index=1)

        for writer in (host_writer, bob_writer):
            updates = _messages_of_type(writer, srv.MSG_ROOM_UPDATED)
            self.assertEqual(
                [update["payload"]["event"] for update in updates],
                ["participant_joined", "room_ready"],
            )
            ready_payload = updates[1]["payload"]
            self.assertEqual(ready_payload["participant_count"], 2)
            self._assert_participants(ready_payload, ["Alice", "Bob"])
        self.assertEqual(self.server._rooms[room_id].state, srv.STATE_READY)

    async def test_v2_late_join_in_relay_allowed_when_not_full(self) -> None:
        """A not-full v2 room in RELAY accepts a late v2 join."""
        room_id, alice_id, alice_writer = await self._create_v2_room(max_players=4)
        bob_id, bob_writer, _ = await self._join_v2(room_id, "Bob", conn_index=1)
        await self._register_udp(alice_id, room_id, ("198.51.100.10", 40000))
        await self._register_udp(bob_id, room_id, ("198.51.100.11", 40001))

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        alice_update_count = len(_messages_of_type(alice_writer, srv.MSG_ROOM_UPDATED))
        bob_update_count = len(_messages_of_type(bob_writer, srv.MSG_ROOM_UPDATED))
        alice_relay_count = len(_messages_of_type(alice_writer, srv.MSG_RELAY_ENABLED))
        bob_relay_count = len(_messages_of_type(bob_writer, srv.MSG_RELAY_ENABLED))

        carol_id, carol_writer, carol_messages = await self._join_v2(
            room_id,
            "Carol",
            conn_index=2,
        )

        joined = carol_messages[0]
        self.assertEqual(joined["type"], srv.MSG_ROOM_JOINED)
        self.assertEqual(joined["payload"]["protocol_version"], 2)
        self._assert_participants(joined["payload"], ["Alice", "Bob", "Carol"])
        self.assertEqual(joined["payload"]["participant_count"], 3)
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIn(carol_id, room.participants)
        self.assertIsNone(self.server._players[carol_id].udp_addr)
        self.assertEqual(_messages_of_type(carol_writer, srv.MSG_RELAY_ENABLED), [])
        self.assertEqual(len(_messages_of_type(alice_writer, srv.MSG_RELAY_ENABLED)), alice_relay_count)
        self.assertEqual(len(_messages_of_type(bob_writer, srv.MSG_RELAY_ENABLED)), bob_relay_count)

        new_alice_updates = _messages_of_type(alice_writer, srv.MSG_ROOM_UPDATED)[alice_update_count:]
        new_bob_updates = _messages_of_type(bob_writer, srv.MSG_ROOM_UPDATED)[bob_update_count:]
        carol_updates = _messages_of_type(carol_writer, srv.MSG_ROOM_UPDATED)
        for updates in (new_alice_updates, new_bob_updates, carol_updates):
            self.assertEqual([m["payload"]["event"] for m in updates], ["participant_joined"])
            self._assert_participants(updates[0]["payload"], ["Alice", "Bob", "Carol"])
            self.assertEqual(updates[0]["payload"]["room_id"], room_id)
            self.assertEqual(updates[0]["payload"]["participant_count"], 3)
        self.assertNotIn(
            "room_ready",
            [m["payload"]["event"] for m in new_alice_updates + new_bob_updates + carol_updates],
        )

    async def test_v2_host_leave_closes_room(self) -> None:
        """If the v2 host leaves, the room closes and cleans up."""
        room_id, alice_id, alice_writer = await self._create_v2_room(max_players=4)
        bob_id, bob_writer, _ = await self._join_v2(room_id, "Bob", conn_index=1)
        carol_id, carol_writer, _ = await self._join_v2(room_id, "Carol", conn_index=2)

        alice_addr = ("198.51.100.10", 40000)
        bob_addr = ("198.51.100.11", 40001)
        carol_addr = ("198.51.100.12", 40002)
        await self._register_udp(alice_id, room_id, alice_addr)
        await self._register_udp(bob_id, room_id, bob_addr)
        await self._register_udp(carol_id, room_id, carol_addr)

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        token = room.relay_session.relay_token
        self.assertEqual(
            room.relay_enabled_players,
            {alice_id, bob_id, carol_id},
        )

        await self.server._handle_leave_room({"room_id": room_id}, alice_id)

        self.assertNotIn(room_id, self.server._rooms)
        for player_id in (alice_id, bob_id, carol_id):
            self.assertNotIn(player_id, self.server._players)
            self.assertNotIn(player_id, self.server._conn_to_player.values())
        for addr in (alice_addr, bob_addr, carol_addr):
            self.assertNotIn(addr, self.server._udp_to_player)
        self.assertNotIn(token, self.server._relay_sessions)

        alice_closed = [
            m for m in _messages_of_type(alice_writer, srv.MSG_ROOM_UPDATED)
            if m["payload"]["event"] == "room_closed"
        ]
        self.assertEqual(alice_closed, [])
        for writer in (bob_writer, carol_writer):
            closed = [
                m for m in _messages_of_type(writer, srv.MSG_ROOM_UPDATED)
                if m["payload"]["event"] == "room_closed"
            ]
            self.assertEqual(len(closed), 1)
            self.assertEqual(closed[0]["payload"]["room_id"], room_id)
            self.assertEqual(closed[0]["payload"]["participant_count"], 3)
            self._assert_participants(closed[0]["payload"], ["Alice", "Bob", "Carol"])
            self.assertNotIn("participant_id", json.dumps(closed[0]["payload"]))

    async def test_v2_non_host_leave_keeps_room_if_two_or_more_remain(self) -> None:
        """A non-host leave keeps the v2 room active when at least two remain."""
        room_id, alice_id, alice_writer = await self._create_v2_room(max_players=4)
        bob_id, bob_writer, _ = await self._join_v2(room_id, "Bob", conn_index=1)
        carol_id, carol_writer, _ = await self._join_v2(room_id, "Carol", conn_index=2)

        alice_addr = ("198.51.100.20", 41000)
        bob_addr = ("198.51.100.21", 41001)
        carol_addr = ("198.51.100.22", 41002)
        await self._register_udp(alice_id, room_id, alice_addr)
        await self._register_udp(bob_id, room_id, bob_addr)
        await self._register_udp(carol_id, room_id, carol_addr)

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        token = room.relay_session.relay_token
        alice_update_count = len(_messages_of_type(alice_writer, srv.MSG_ROOM_UPDATED))
        carol_update_count = len(_messages_of_type(carol_writer, srv.MSG_ROOM_UPDATED))

        await self.server._handle_leave_room({"room_id": room_id}, bob_id)

        self.assertIn(room_id, self.server._rooms)
        self.assertIs(self.server._rooms[room_id], room)
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        self.assertIn(token, self.server._relay_sessions)
        self.assertEqual(list(room.participants.keys()), [alice_id, carol_id])
        self.assertEqual(room.joiner_id, carol_id)
        self.assertEqual(room.relay_enabled_players, {alice_id, carol_id})
        self.assertNotIn(bob_id, self.server._players)
        self.assertNotIn(bob_addr, self.server._udp_to_player)
        self.assertNotIn(bob_id, self.server._udp_to_player.values())
        self.assertNotIn(bob_id, room.participants)

        new_alice_updates = _messages_of_type(alice_writer, srv.MSG_ROOM_UPDATED)[alice_update_count:]
        new_carol_updates = _messages_of_type(carol_writer, srv.MSG_ROOM_UPDATED)[carol_update_count:]
        for updates in (new_alice_updates, new_carol_updates):
            self.assertEqual([m["payload"]["event"] for m in updates], ["participant_left"])
            self.assertEqual(updates[0]["payload"]["room_id"], room_id)
            self._assert_participants(updates[0]["payload"], ["Alice", "Carol"])

        bob_leave_updates = [
            m for m in _messages_of_type(bob_writer, srv.MSG_ROOM_UPDATED)
            if m["payload"]["event"] == "participant_left"
        ]
        self.assertEqual(bob_leave_updates, [])


class TestV2RelayActivation(unittest.IsolatedAsyncioTestCase):
    """Implemented E3 UDP REG and RELAY_ENABLED activation behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def _create_v2_room(
        self,
        *,
        max_players: int = 4,
    ) -> tuple[str, str, MagicMock]:
        host_id, host_writer, messages = await _create_room(
            self.server,
            {
                "player_name": "Alice",
                "protocol_version": 2,
                "max_players": max_players,
            },
            conn_key="127.0.0.1:1000",
        )
        self.assertIsNotNone(host_id)
        assert host_id is not None
        return messages[-1]["payload"]["room_id"], host_id, host_writer

    async def _join_v2(
        self,
        room_id: str,
        player_name: str,
        *,
        conn_index: int,
    ) -> tuple[str, MagicMock]:
        player_id, writer, _ = await _join_room(
            self.server,
            {
                "room_id": room_id,
                "player_name": player_name,
                "protocol_version": 2,
            },
            conn_key=f"127.0.0.1:{1000 + conn_index}",
        )
        self.assertIsNotNone(player_id)
        assert player_id is not None
        return player_id, writer

    async def _flush_udp_reg_tasks(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_v2_participants_udp_reg(self) -> None:
        """v2 UDP REG records participant UDP state without v1 peer signaling."""
        room_id, host_id, host_writer = await self._create_v2_room()
        bob_id, bob_writer = await self._join_v2(room_id, "Bob", conn_index=1)

        host_addr = ("198.51.100.10", 40000)
        bob_addr = ("198.51.100.11", 40001)
        self.server.handle_udp_register(_udp_reg_payload(host_id, room_id), host_addr)
        await self._flush_udp_reg_tasks()

        room = self.server._rooms[room_id]
        self.assertEqual(self.server._players[host_id].udp_addr, host_addr)
        self.assertIsNone(self.server._players[bob_id].udp_addr)
        self.assertEqual(self.server._udp_to_player[host_addr], host_id)
        self.assertEqual(room.state, srv.STATE_READY)
        self.assertIsNone(room.relay_session)
        self.assertEqual(_messages_of_type(host_writer, srv.MSG_RELAY_ENABLED), [])
        self.assertEqual(_messages_of_type(host_writer, srv.MSG_PEER_INFO), [])

        self.server.handle_udp_register(_udp_reg_payload(bob_id, room_id), bob_addr)
        await self._flush_udp_reg_tasks()

        self.assertEqual(self.server._players[bob_id].udp_addr, bob_addr)
        self.assertEqual(self.server._udp_to_player[bob_addr], bob_id)
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        self.assertEqual(_messages_of_type(host_writer, srv.MSG_PEER_INFO), [])
        self.assertEqual(_messages_of_type(bob_writer, srv.MSG_PEER_INFO), [])

    async def test_v2_relay_enabled_sent_to_registered(self) -> None:
        """RELAY_ENABLED goes only to v2 participants that completed UDP REG."""
        room_id, host_id, host_writer = await self._create_v2_room()
        bob_id, bob_writer = await self._join_v2(room_id, "Bob", conn_index=1)
        carol_id, carol_writer = await self._join_v2(room_id, "Carol", conn_index=2)

        self.server.handle_udp_register(
            _udp_reg_payload(host_id, room_id),
            ("198.51.100.10", 40000),
        )
        self.server.handle_udp_register(
            _udp_reg_payload(bob_id, room_id),
            ("198.51.100.11", 40001),
        )
        await self._flush_udp_reg_tasks()

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None

        host_relay = _messages_of_type(host_writer, srv.MSG_RELAY_ENABLED)
        bob_relay = _messages_of_type(bob_writer, srv.MSG_RELAY_ENABLED)
        carol_relay = _messages_of_type(carol_writer, srv.MSG_RELAY_ENABLED)
        self.assertEqual(len(host_relay), 1)
        self.assertEqual(len(bob_relay), 1)
        self.assertEqual(carol_relay, [])
        self.assertIsNone(self.server._players[carol_id].udp_addr)

        expected_keys = {"room_id", "relay_token", "relay_ip", "relay_port"}
        host_payload = host_relay[0]["payload"]
        bob_payload = bob_relay[0]["payload"]
        self.assertEqual(set(host_payload), expected_keys)
        self.assertEqual(set(bob_payload), expected_keys)
        self.assertEqual(host_payload, bob_payload)
        self.assertEqual(host_payload["room_id"], room_id)
        self.assertEqual(host_payload["relay_token"], room.relay_session.relay_token)
        self.assertEqual(host_payload["relay_ip"], self.server._public_ip)
        self.assertEqual(host_payload["relay_port"], srv.UDP_PORT)

        for writer in (host_writer, bob_writer, carol_writer):
            self.assertEqual(_messages_of_type(writer, srv.MSG_PEER_INFO), [])
            self.assertEqual(_messages_of_type(writer, srv.MSG_P2P_SUCCESS), [])
            self.assertEqual(_messages_of_type(writer, srv.MSG_P2P_FAILED), [])


class TestV2RelayFanout(unittest.IsolatedAsyncioTestCase):
    """Expected v2 relay-only activation and fanout behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def _flush_udp_reg_tasks(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _create_v2_room_with_players(
        self,
        player_names: list[str],
    ) -> tuple[str, list[str], list[MagicMock]]:
        self.assertGreaterEqual(len(player_names), 2)
        host_id, host_writer, create_messages = await _create_room(
            self.server,
            {
                "player_name": player_names[0],
                "protocol_version": 2,
                "max_players": 4,
            },
            conn_key="127.0.0.1:1000",
        )
        self.assertIsNotNone(host_id)
        assert host_id is not None
        room_id = create_messages[-1]["payload"]["room_id"]
        player_ids = [host_id]
        writers = [host_writer]

        for index, player_name in enumerate(player_names[1:], start=1):
            player_id, writer, _ = await _join_room(
                self.server,
                {
                    "room_id": room_id,
                    "player_name": player_name,
                    "protocol_version": 2,
                },
                conn_key=f"127.0.0.1:{1000 + index}",
            )
            self.assertIsNotNone(player_id)
            assert player_id is not None
            player_ids.append(player_id)
            writers.append(writer)

        return room_id, player_ids, writers

    async def _register_udp(
        self,
        player_id: str,
        room_id: str,
        addr: tuple[str, int],
    ) -> None:
        self.server.handle_udp_register(_udp_reg_payload(player_id, room_id), addr)
        await self._flush_udp_reg_tasks()

    @unittest.skip(PENDING_REASON)
    def test_v2_relay_enabled_sent_to_all_current_participants(self) -> None:
        """RELAY_ENABLED is delivered individually to every current participant."""
        self.fail("Assert all current participants receive RELAY_ENABLED.")

    async def test_v2_relay_fanout_excludes_sender(self) -> None:
        """A v2 RELAY packet fans out to registered peers and excludes sender."""
        room_id, player_ids, _ = await self._create_v2_room_with_players(
            ["Alice", "Bob", "Carol"],
        )
        alice_id, bob_id, carol_id = player_ids
        alice_addr = ("198.51.100.10", 40000)
        bob_addr = ("198.51.100.11", 40001)
        carol_addr = ("198.51.100.12", 40002)

        await self._register_udp(alice_id, room_id, alice_addr)
        await self._register_udp(bob_id, room_id, bob_addr)
        await self._register_udp(carol_id, room_id, carol_addr)

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None

        transport = _make_udp_transport_mock()
        self.server._udp_transport = transport
        packet = _relay_packet(
            room.relay_session.relay_token,
            alice_id,
            payload=b"game-data",
        )

        self.server.handle_udp_relay(_relay_raw(packet), alice_addr)

        calls = transport.sendto.call_args_list
        self.assertEqual([call.args[1] for call in calls], [bob_addr, carol_addr])
        self.assertNotIn(alice_addr, [call.args[1] for call in calls])
        self.assertEqual([call.args[0] for call in calls], [packet, packet])
        header, payload = _decode_relay_packet(calls[0].args[0])
        self.assertEqual(header["player_id"], alice_id)
        self.assertEqual(header["relay_token"], room.relay_session.relay_token)
        self.assertEqual(payload, b"game-data")
        self.assertEqual(room.relay_session.relay_packets_forwarded, 2)
        self.assertEqual(room.relay_session.relay_bytes_forwarded, len(packet) * 2)

    async def test_v2_relay_fanout_skips_unregistered_participants(self) -> None:
        """Participants without UDP REG are skipped during relay fanout."""
        room_id, player_ids, _ = await self._create_v2_room_with_players(
            ["Alice", "Bob", "Carol"],
        )
        alice_id, bob_id, carol_id = player_ids
        alice_addr = ("198.51.100.10", 40000)
        bob_addr = ("198.51.100.11", 40001)
        carol_addr = ("198.51.100.12", 40002)

        await self._register_udp(alice_id, room_id, alice_addr)
        await self._register_udp(bob_id, room_id, bob_addr)

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None
        self.assertIsNone(self.server._players[carol_id].udp_addr)

        transport = _make_udp_transport_mock()
        self.server._udp_transport = transport
        packet = _relay_packet(room.relay_session.relay_token, alice_id)

        self.server.handle_udp_relay(_relay_raw(packet), alice_addr)

        transport.sendto.assert_called_once_with(packet, bob_addr)
        sent_addrs = [call.args[1] for call in transport.sendto.call_args_list]
        self.assertNotIn(alice_addr, sent_addrs)
        self.assertNotIn(carol_addr, sent_addrs)
        self.assertEqual(room.relay_session.relay_packets_forwarded, 1)
        self.assertEqual(room.relay_session.relay_drop_no_target_udp_addr, 1)

    async def test_v2_late_join_receives_relay_enabled_after_reg(self) -> None:
        """A late joiner in RELAY receives RELAY_ENABLED only after UDP REG."""
        room_id, player_ids, writers = await self._create_v2_room_with_players(
            ["Alice", "Bob"],
        )
        alice_id, bob_id = player_ids
        alice_writer, bob_writer = writers
        alice_addr = ("198.51.100.10", 40000)
        bob_addr = ("198.51.100.11", 40001)
        carol_addr = ("198.51.100.12", 40002)

        await self._register_udp(alice_id, room_id, alice_addr)
        await self._register_udp(bob_id, room_id, bob_addr)

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None
        existing_token = room.relay_session.relay_token
        self.assertEqual(len(_messages_of_type(alice_writer, srv.MSG_RELAY_ENABLED)), 1)
        self.assertEqual(len(_messages_of_type(bob_writer, srv.MSG_RELAY_ENABLED)), 1)

        carol_id, carol_writer, _ = await _join_room(
            self.server,
            {"room_id": room_id, "player_name": "Carol", "protocol_version": 2},
            conn_key="127.0.0.1:1002",
        )
        self.assertIsNotNone(carol_id)
        assert carol_id is not None
        self.assertEqual(_messages_of_type(carol_writer, srv.MSG_RELAY_ENABLED), [])

        transport = _make_udp_transport_mock()
        self.server._udp_transport = transport
        packet = _relay_packet(existing_token, alice_id, payload=b"before-carol-reg")
        self.server.handle_udp_relay(_relay_raw(packet), alice_addr)

        transport.sendto.assert_called_once_with(packet, bob_addr)
        sent_addrs = [call.args[1] for call in transport.sendto.call_args_list]
        self.assertNotIn(alice_addr, sent_addrs)
        self.assertNotIn(carol_addr, sent_addrs)

        await self._register_udp(carol_id, room_id, carol_addr)
        carol_relay = _messages_of_type(carol_writer, srv.MSG_RELAY_ENABLED)
        self.assertEqual(len(carol_relay), 1)
        self.assertEqual(
            set(carol_relay[0]["payload"]),
            {"room_id", "relay_token", "relay_ip", "relay_port"},
        )
        self.assertEqual(carol_relay[0]["payload"]["room_id"], room_id)
        self.assertEqual(carol_relay[0]["payload"]["relay_token"], existing_token)
        self.assertEqual(carol_relay[0]["payload"]["relay_ip"], self.server._public_ip)
        self.assertEqual(carol_relay[0]["payload"]["relay_port"], srv.UDP_PORT)
        self.assertEqual(len(_messages_of_type(alice_writer, srv.MSG_RELAY_ENABLED)), 1)
        self.assertEqual(len(_messages_of_type(bob_writer, srv.MSG_RELAY_ENABLED)), 1)

        await self._register_udp(carol_id, room_id, carol_addr)
        self.assertEqual(len(_messages_of_type(carol_writer, srv.MSG_RELAY_ENABLED)), 1)

        transport.sendto.reset_mock()
        packet = _relay_packet(existing_token, alice_id, payload=b"after-carol-reg")
        self.server.handle_udp_relay(_relay_raw(packet), alice_addr)

        calls = transport.sendto.call_args_list
        self.assertEqual([call.args[1] for call in calls], [bob_addr, carol_addr])
        self.assertNotIn(alice_addr, [call.args[1] for call in calls])
        self.assertEqual([call.args[0] for call in calls], [packet, packet])


class TestV2Compatibility(unittest.IsolatedAsyncioTestCase):
    """Expected v1/v2 compatibility behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def test_v1_client_rejected_from_v2_room(self) -> None:
        """A missing or v1 protocol_version join cannot enter a v2 room."""
        _, _, create_messages = await _create_room(
            self.server,
            {"player_name": "Alice", "protocol_version": 2},
            conn_key="127.0.0.1:1000",
        )
        room_id = create_messages[-1]["payload"]["room_id"]

        for payload in (
            {"room_id": room_id, "player_name": "Bob"},
            {"room_id": room_id, "player_name": "Bob", "protocol_version": 1},
        ):
            with self.subTest(payload=payload):
                player_id, _, messages = await _join_room(
                    self.server,
                    payload,
                    conn_key=f"127.0.0.1:{2000 + len(str(payload))}",
                )
                self.assertIsNone(player_id)
                self.assertEqual(messages[-1]["type"], srv.MSG_ERROR)
                self.assertEqual(messages[-1]["payload"]["code"], 1011)

    async def test_v2_client_joining_v1_room_uses_legacy_semantics(self) -> None:
        """A v2 client joining a v1 room receives protocol_version=1 semantics."""
        _, host_writer, create_messages = await _create_room(
            self.server,
            {"player_name": "Alice"},
            conn_key="127.0.0.1:1000",
        )
        room_id = create_messages[-1]["payload"]["room_id"]

        player_id, _, messages = await _join_room(
            self.server,
            {"room_id": room_id, "player_name": "Bob", "protocol_version": 2},
            conn_key="127.0.0.1:1001",
        )
        self.assertIsNotNone(player_id)
        joined = messages[-1]
        self.assertEqual(joined["type"], srv.MSG_ROOM_JOINED)
        self.assertEqual(set(joined["payload"]), {"room_id", "player_id", "protocol_version"})
        self.assertEqual(joined["payload"]["protocol_version"], 1)
        self.assertNotIn("participants", joined["payload"])
        self.assertNotIn("max_players", joined["payload"])
        self.assertEqual(self.server._rooms[room_id].protocol_version, 1)
        self.assertEqual(self.server._rooms[room_id].state, srv.STATE_READY)
        self.assertEqual(_messages_of_type(host_writer, srv.MSG_ROOM_UPDATED), [])

        legacy_server = srv.P2PServer()
        legacy_server._loop = asyncio.get_event_loop()
        _, _, legacy_create = await _create_room(
            legacy_server,
            {"player_name": "Alice"},
            conn_key="127.0.0.1:3000",
        )
        legacy_room_id = legacy_create[-1]["payload"]["room_id"]
        _, _, legacy_join_messages = await _join_room(
            legacy_server,
            {"room_id": legacy_room_id, "player_name": "Bob"},
            conn_key="127.0.0.1:3001",
        )
        legacy_payload = legacy_join_messages[-1]["payload"]
        self.assertEqual(set(legacy_payload), {"room_id", "player_id"})


class TestV2ProtocolCompliance(unittest.IsolatedAsyncioTestCase):
    """Expected v2 protocol-lock compliance behavior."""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()

    async def _flush_udp_reg_tasks(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def _create_active_v2_room(
        self,
    ) -> tuple[str, str, str, MagicMock, MagicMock]:
        host_id, host_writer, create_messages = await _create_room(
            self.server,
            {"player_name": "Alice", "protocol_version": 2, "max_players": 4},
            conn_key="127.0.0.1:1000",
        )
        self.assertIsNotNone(host_id)
        assert host_id is not None
        room_id = create_messages[-1]["payload"]["room_id"]
        bob_id, bob_writer, _ = await _join_room(
            self.server,
            {"room_id": room_id, "player_name": "Bob", "protocol_version": 2},
            conn_key="127.0.0.1:1001",
        )
        self.assertIsNotNone(bob_id)
        assert bob_id is not None

        self.server.handle_udp_register(
            _udp_reg_payload(host_id, room_id),
            ("198.51.100.10", 40000),
        )
        self.server.handle_udp_register(
            _udp_reg_payload(bob_id, room_id),
            ("198.51.100.11", 40001),
        )
        await self._flush_udp_reg_tasks()
        return room_id, host_id, bob_id, host_writer, bob_writer

    async def test_v2_room_does_not_emit_peer_info(self) -> None:
        """v2 multi-peer rooms do not emit PEER_INFO, P2P_SUCCESS, or P2P_FAILED."""
        room_id, _, _, host_writer, bob_writer = await self._create_active_v2_room()

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        for writer in (host_writer, bob_writer):
            self.assertEqual(_messages_of_type(writer, srv.MSG_PEER_INFO), [])
            self.assertEqual(_messages_of_type(writer, srv.MSG_P2P_SUCCESS), [])
            self.assertEqual(_messages_of_type(writer, srv.MSG_P2P_FAILED), [])

    async def test_v2_room_does_not_enter_punching_or_direct(self) -> None:
        """v2 multi-peer rooms never enter PUNCHING or DIRECT."""
        room_id, host_id, bob_id, _, _ = await self._create_active_v2_room()

        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertNotEqual(room.state, srv.STATE_PUNCHING)
        self.assertNotEqual(room.state, srv.STATE_DIRECT)

        await self.server._handle_p2p_success({"room_id": room_id}, host_id)
        await self.server._handle_p2p_failed(
            {"room_id": room_id, "reason": "test"},
            bob_id,
        )

        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertNotEqual(room.state, srv.STATE_PUNCHING)
        self.assertNotEqual(room.state, srv.STATE_DIRECT)

    async def test_relay_enabled_payload_shape_unchanged(self) -> None:
        """RELAY_ENABLED payload remains room_id, relay_token, relay_ip, relay_port."""
        room_id, _, _, host_writer, bob_writer = await self._create_active_v2_room()

        room = self.server._rooms[room_id]
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None
        for writer in (host_writer, bob_writer):
            relay_enabled = _messages_of_type(writer, srv.MSG_RELAY_ENABLED)
            self.assertEqual(len(relay_enabled), 1)
            payload = relay_enabled[0]["payload"]
            self.assertEqual(set(payload), {"room_id", "relay_token", "relay_ip", "relay_port"})
            self.assertEqual(payload["room_id"], room_id)
            self.assertEqual(payload["relay_token"], room.relay_session.relay_token)
            self.assertEqual(payload["relay_ip"], self.server._public_ip)
            self.assertEqual(payload["relay_port"], srv.UDP_PORT)

    async def test_relay_wire_format_unchanged(self) -> None:
        """RELAY UDP wire format remains RELAY\\n{json}\\n{game data}."""
        room_id, host_id, _, _, _ = await self._create_active_v2_room()
        room = self.server._rooms[room_id]
        self.assertIsNotNone(room.relay_session)
        assert room.relay_session is not None

        transport = _make_udp_transport_mock()
        self.server._udp_transport = transport
        packet = _relay_packet(
            room.relay_session.relay_token,
            host_id,
            payload=b"\x00game\nbytes",
        )

        self.server.handle_udp_relay(_relay_raw(packet), ("198.51.100.10", 40000))

        forwarded = transport.sendto.call_args.args[0]
        self.assertEqual(forwarded, packet)
        header, payload = _decode_relay_packet(forwarded)
        self.assertEqual(
            set(header),
            {"relay_token", "player_id"},
        )
        self.assertEqual(header["relay_token"], room.relay_session.relay_token)
        self.assertEqual(header["player_id"], host_id)
        self.assertEqual(payload, b"\x00game\nbytes")


if __name__ == "__main__":
    unittest.main()
