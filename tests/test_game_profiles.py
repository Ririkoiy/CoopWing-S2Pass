# -*- coding: utf-8 -*-
"""Tests for backend.game_profiles — CRUD and scan/confirm flows."""

import json as _json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.game_profiles import (
    ConfirmPortsRequest,
    CreateGameRequest,
    GameProfile,
    GameProfileStore,
)

from backend.process_port_detector import PortCandidate


class TestGameProfile(unittest.TestCase):
    def test_roundtrip(self) -> None:
        profile = GameProfile(
            game_id="abc123",
            display_name="Test Game",
            executable_path="C:/game/test.exe",
            working_directory="C:/game",
            launch_args=["-windowed"],
            confirmed_tcp_ports=[27015],
            confirmed_udp_ports=[27015],
            candidate_ports=[PortCandidate("tcp", 27015, confidence="high").to_dict()],
            notes="test notes",
            created_at=10.0,
            updated_at=20.0,
        )
        d = profile.to_dict()
        self.assertEqual(d["game_id"], "abc123")
        self.assertEqual(d["confirmed_tcp_ports"], [27015])
        self.assertIn("launch_args", d)
        p2 = GameProfile.from_dict(d)
        self.assertEqual(p2.game_id, "abc123")
        self.assertEqual(p2.launch_args, ["-windowed"])

    def test_from_dict_minimal(self) -> None:
        d = {"game_id": "x", "display_name": "Name", "executable_path": "p",
             "confirmed_tcp_ports": [], "confirmed_udp_ports": [],
             "candidate_ports": [], "created_at": 0, "updated_at": 0}
        p = GameProfile.from_dict(d)
        self.assertEqual(p.display_name, "Name")
        self.assertIsNone(p.launch_args)


class TestGameProfileStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="coopwing_test_games_")
        self._path = Path(self._tmpdir) / "games.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_list_empty(self) -> None:
        store = GameProfileStore(self._path, now=lambda: 100.0)
        self.assertEqual(store.list(), [])

    def test_create_and_list(self) -> None:
        store = GameProfileStore(self._path, now=lambda: 100.0)
        req = CreateGameRequest(display_name="HL2", executable_path="C:/hl2/hl2.exe")
        p = store.create(req)
        self.assertEqual(p.display_name, "HL2")
        self.assertEqual(p.created_at, 100.0)
        self.assertEqual(len(store.list()), 1)

    def test_get(self) -> None:
        store = GameProfileStore(self._path)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        created = store.create(req)
        found = store.get(created.game_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.display_name, "Game")

    def test_get_missing_returns_none(self) -> None:
        store = GameProfileStore(self._path)
        self.assertIsNone(store.get("nonexistent"))

    def test_delete(self) -> None:
        store = GameProfileStore(self._path)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        created = store.create(req)
        self.assertTrue(store.delete(created.game_id))
        self.assertEqual(store.list(), [])

    def test_delete_nonexistent_returns_false(self) -> None:
        store = GameProfileStore(self._path)
        self.assertFalse(store.delete("nonexistent"))

    def test_update_candidates(self) -> None:
        store = GameProfileStore(self._path, now=lambda: 100.0)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        created = store.create(req)
        candidates = [
            PortCandidate("tcp", 27015, confidence="high", reason="TCP LISTEN"),
            PortCandidate("udp", 27015, confidence="medium", reason="UDP bound"),
        ]
        updated = store.update_candidates(created.game_id, candidates)
        self.assertIsNotNone(updated)
        self.assertEqual(len(updated.candidate_ports), 2)
        self.assertEqual(updated.updated_at, 100.0)

    def test_update_candidates_missing_returns_none(self) -> None:
        store = GameProfileStore(self._path)
        self.assertIsNone(store.update_candidates("nonexistent", []))

    def test_confirm_ports(self) -> None:
        store = GameProfileStore(self._path, now=lambda: 100.0)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        created = store.create(req)
        confirm = ConfirmPortsRequest(tcp_ports=[27015, 27016], udp_ports=[27015, 27017])
        updated = store.confirm_ports(created.game_id, confirm)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.confirmed_tcp_ports, [27015, 27016])
        self.assertEqual(updated.confirmed_udp_ports, [27015, 27017])
        self.assertEqual(updated.updated_at, 100.0)

    def test_confirm_ports_dedupes_and_sorts(self) -> None:
        store = GameProfileStore(self._path)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        created = store.create(req)
        confirm = ConfirmPortsRequest(tcp_ports=[27016, 27015, 27016], udp_ports=[])
        updated = store.confirm_ports(created.game_id, confirm)
        self.assertEqual(updated.confirmed_tcp_ports, [27015, 27016])

    def test_confirm_ports_missing_returns_none(self) -> None:
        store = GameProfileStore(self._path)
        self.assertIsNone(store.confirm_ports("nonexistent", ConfirmPortsRequest()))

    def test_atomic_save(self) -> None:
        store = GameProfileStore(self._path)
        req = CreateGameRequest(display_name="Game", executable_path="/tmp/game")
        store.create(req)
        text = self._path.read_text(encoding="utf-8")
        data = _json.loads(text)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertNotIn("relay_token", _json.dumps(data))
        self.assertNotIn("player_id", _json.dumps(data))

    def test_load_malformed_returns_empty(self) -> None:
        self._path.write_text("not json", encoding="utf-8")
        store = GameProfileStore(self._path)
        self.assertEqual(store.list(), [])

    def test_load_missing_file_returns_empty(self) -> None:
        store = GameProfileStore(Path(self._tmpdir) / "nonexistent.json")
        self.assertEqual(store.list(), [])

    def test_multiple_profiles(self) -> None:
        store = GameProfileStore(self._path)
        store.create(CreateGameRequest("A", "/tmp/a"))
        store.create(CreateGameRequest("B", "/tmp/b"))
        store.create(CreateGameRequest("C", "/tmp/c"))
        self.assertEqual(len(store.list()), 3)


if __name__ == "__main__":
    unittest.main()
