#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  server.py 房间生命周期管理 — 集成测试
  遵循 PROTOCOL_LOCK v1.0.0
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Windows 事件循环策略
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 确保能从 tests/ 子目录导入 server.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as srv


# ══════════════════════════════════════════════════════════════════════════════
# 测试工具
# ══════════════════════════════════════════════════════════════════════════════

def _make_mock_writer() -> MagicMock:
    """创建模拟的 asyncio.StreamWriter"""
    w = MagicMock(spec=asyncio.StreamWriter)
    w.is_closing.return_value = False
    w.write = MagicMock()
    w.drain = AsyncMock()
    w.close = MagicMock()
    w.wait_closed = AsyncMock()
    return w


def _make_player(
    player_id: str = "p_aabbccddeeff",
    player_name: str = "TestPlayer",
    room_id: str | None = None,
    tcp_ip: str = "127.0.0.1",
    peer_addr: str = "127.0.0.1:12345",
    udp_addr: tuple[str, int] | None = None,
    writer: MagicMock | None = None,
) -> srv.Player:
    """创建测试用 Player"""
    return srv.Player(
        player_id=player_id,
        player_name=player_name,
        room_id=room_id,
        tcp_writer=writer or _make_mock_writer(),
        peer_addr=peer_addr,
        tcp_ip=tcp_ip,
        udp_addr=udp_addr,
        last_heartbeat=time.monotonic(),
    )


def _make_room(
    room_id: str = "ABCDEF",
    state: str = srv.STATE_WAITING,
    creator_id: str | None = None,
    joiner_id: str | None = None,
) -> srv.Room:
    """创建测试用 Room"""
    return srv.Room(
        room_id=room_id,
        state=state,
        creator_id=creator_id,
        joiner_id=joiner_id,
        created_at=time.monotonic(),
    )


def _make_relay_raw_data(relay_token: str, player_id: str, payload: bytes) -> memoryview:
    header = json.dumps(
        {"relay_token": relay_token, "player_id": player_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return memoryview(header + b"\n" + payload)


class _ServerTestBase(unittest.IsolatedAsyncioTestCase):
    """测试基类: 初始化 P2PServer 实例"""

    def setUp(self) -> None:
        self.server = srv.P2PServer()
        self.server._loop = asyncio.get_event_loop()


# ══════════════════════════════════════════════════════════════════════════════
# 1. 基础状态机测试
# ══════════════════════════════════════════════════════════════════════════════

class TestRoomStateMachine(_ServerTestBase):
    """房间状态机流转测试"""

    async def test_create_room_state_waiting(self) -> None:
        """CREATE_ROOM → 状态 WAITING"""
        writer = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, writer, "127.0.0.1", "127.0.0.1:1000",
        )
        self.assertIsNotNone(pid)
        player = self.server._players[pid]
        room = self.server._rooms[player.room_id]
        self.assertEqual(room.state, srv.STATE_WAITING)

    async def test_join_room_state_ready(self) -> None:
        """JOIN_ROOM → 状态 READY"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        pid_b = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "127.0.0.2", "127.0.0.2:2000",
        )
        self.assertIsNotNone(pid_b)
        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_READY)

    async def test_join_nonexistent_room(self) -> None:
        """JOIN 不存在的房间 → 返回 None, 发送 ERROR"""
        w = _make_mock_writer()
        pid = await self.server._handle_join_room(
            {"room_id": "ZZZZZZ", "player_name": "Bob"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        self.assertIsNone(pid)

    async def test_join_full_room(self) -> None:
        """JOIN 已满房间 → ERROR ROOM_FULL"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "127.0.0.2", "127.0.0.2:2000",
        )

        w3 = _make_mock_writer()
        pid_c = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Carol"}, w3, "127.0.0.3", "127.0.0.3:3000",
        )
        self.assertIsNone(pid_c)


# ══════════════════════════════════════════════════════════════════════════════
# 2. P2P 判定测试
# ══════════════════════════════════════════════════════════════════════════════

class TestP2PJudgment(_ServerTestBase):
    """P2P_SUCCESS / P2P_FAILED 判定测试"""

    async def _setup_punching_room(self) -> tuple[str, str, str]:
        """创建并进入 PUNCHING 状态的房间，返回 (room_id, pid_a, pid_b)"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        pid_b = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "10.0.0.2", "10.0.0.2:2000",
        )

        # 强制进入 PUNCHING 状态
        room = self.server._rooms[room_id]
        room.state = srv.STATE_PUNCHING
        return room_id, pid_a, pid_b

    async def test_p2p_success_both_required(self) -> None:
        """PROTOCOL_LOCK §8: 双方均 P2P_SUCCESS 后才进入 DIRECT"""
        room_id, pid_a, pid_b = await self._setup_punching_room()

        # 仅 A 报告成功 → 仍为 PUNCHING
        await self.server._handle_p2p_success({"room_id": room_id}, pid_a)
        room = self.server._rooms[room_id]
        self.assertEqual(room.state, srv.STATE_PUNCHING)

        # B 也报告成功 → DIRECT
        await self.server._handle_p2p_success({"room_id": room_id}, pid_b)
        self.assertEqual(room.state, srv.STATE_DIRECT)

    async def test_p2p_failed_immediate_relay(self) -> None:
        """PROTOCOL_LOCK §8: 任一方 P2P_FAILED → 立即 RELAY (不等待2秒)"""
        room_id, pid_a, pid_b = await self._setup_punching_room()

        await self.server._handle_p2p_failed(
            {"room_id": room_id, "reason": "TIMEOUT"}, pid_a,
        )
        room = self.server._rooms.get(room_id)
        self.assertIsNotNone(room)
        self.assertEqual(room.state, srv.STATE_RELAY)

    async def test_punch_timeout_forced_relay(self) -> None:
        """PROTOCOL_LOCK §8: PUNCHING 超时 10s → 强制 RELAY"""
        room_id, pid_a, pid_b = await self._setup_punching_room()
        room = self.server._rooms[room_id]

        # 直接调用 _switch_to_relay 模拟超时
        await self.server._switch_to_relay(room)
        self.assertEqual(room.state, srv.STATE_RELAY)

    async def test_same_ip_creator_joiner_use_one_relay_session_reservation(self) -> None:
        """A same-public-IP pair can enter Relay without a false 1007."""
        server = srv.P2PServer(relay_max_sessions_per_ip=1)
        server._loop = asyncio.get_event_loop()
        same_ip = "198.51.100.10"
        creator = _make_player(
            player_id="p_aaaaaaaaaaaa",
            player_name="Alice",
            room_id="ABCDEF",
            tcp_ip=same_ip,
        )
        joiner = _make_player(
            player_id="p_bbbbbbbbbbbb",
            player_name="Bob",
            room_id="ABCDEF",
            tcp_ip=same_ip,
        )
        room = _make_room(
            room_id="ABCDEF",
            state=srv.STATE_PUNCHING,
            creator_id=creator.player_id,
            joiner_id=joiner.player_id,
        )
        server._players[creator.player_id] = creator
        server._players[joiner.player_id] = joiner
        server._rooms[room.room_id] = room

        await server._switch_to_relay(room)

        self.assertEqual(room.state, srv.STATE_RELAY)
        self.assertEqual(server._rate_limiter._relay_per_ip[same_ip], 1)
        self.assertEqual(room.relay_session.reserved_ips, (same_ip,))

        await server._close_room(room.room_id, reason="test")
        self.assertNotIn(same_ip, server._rate_limiter._relay_per_ip)

    async def test_global_relay_capacity_keeps_error_code_1007(self) -> None:
        """Capacity rejection keeps the frozen RELAY_UNAVAILABLE response."""
        server = srv.P2PServer(relay_max_sessions=1)
        server._loop = asyncio.get_event_loop()
        server._relay_sessions["rtk_0123456789abcdef"] = srv.RelaySession(
            relay_token="rtk_0123456789abcdef",
            room_id="EXIST1",
            player_a_id="p_111111111111",
            player_b_id="p_222222222222",
        )
        creator = _make_player(
            player_id="p_aaaaaaaaaaaa",
            player_name="Alice",
            room_id="ABCDEF",
            tcp_ip="198.51.100.11",
        )
        joiner = _make_player(
            player_id="p_bbbbbbbbbbbb",
            player_name="Bob",
            room_id="ABCDEF",
            tcp_ip="198.51.100.12",
        )
        room = _make_room(
            room_id="ABCDEF",
            state=srv.STATE_PUNCHING,
            creator_id=creator.player_id,
            joiner_id=joiner.player_id,
        )
        server._players[creator.player_id] = creator
        server._players[joiner.player_id] = joiner
        server._rooms[room.room_id] = room

        await server._switch_to_relay(room)

        self.assertEqual(room.state, srv.STATE_PUNCHING)
        for writer in (creator.tcp_writer, joiner.tcp_writer):
            written = writer.write.call_args[0][0]
            message = json.loads(written.decode("utf-8"))
            self.assertEqual(message["type"], srv.MSG_ERROR)
            self.assertEqual(message["payload"]["code"], 1007)


# ══════════════════════════════════════════════════════════════════════════════
# 3. _close_room 幂等性测试
# ══════════════════════════════════════════════════════════════════════════════

class TestCloseRoomIdempotent(_ServerTestBase):
    """_close_room 幂等性和完整性测试"""

    async def test_close_room_idempotent(self) -> None:
        """重复调用 _close_room 不抛异常，不重复清理"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 第一次关闭
        await self.server._close_room(room_id, reason="test1")
        self.assertNotIn(room_id, self.server._rooms)
        self.assertNotIn(pid, self.server._players)

        # 第二次关闭 — 必须安全无异常
        await self.server._close_room(room_id, reason="test2")

        # 第三次
        await self.server._close_room(room_id, reason="test3")

    async def test_close_room_cleans_all_mappings(self) -> None:
        """_close_room 清理所有映射: _rooms, _players, _conn_to_player, _udp_to_player"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        pid_b = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "10.0.0.2", "10.0.0.2:2000",
        )

        # 模拟 UDP 注册
        self.server._players[pid_a].udp_addr = ("10.0.0.1", 5000)
        self.server._udp_to_player[("10.0.0.1", 5000)] = pid_a
        self.server._players[pid_b].udp_addr = ("10.0.0.2", 5001)
        self.server._udp_to_player[("10.0.0.2", 5001)] = pid_b

        # 关闭房间
        await self.server._close_room(room_id, reason="test")

        # 验证所有映射已清理
        self.assertNotIn(room_id, self.server._rooms)
        self.assertNotIn(pid_a, self.server._players)
        self.assertNotIn(pid_b, self.server._players)
        self.assertNotIn("10.0.0.1:1000", self.server._conn_to_player)
        self.assertNotIn("10.0.0.2:2000", self.server._conn_to_player)
        self.assertNotIn(("10.0.0.1", 5000), self.server._udp_to_player)
        self.assertNotIn(("10.0.0.2", 5001), self.server._udp_to_player)

    async def test_close_room_closes_tcp_writers(self) -> None:
        """_close_room 关闭所有玩家的 TCP writer"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "10.0.0.2", "10.0.0.2:2000",
        )

        await self.server._close_room(room_id, reason="test")

        # 验证 TCP writer 被关闭
        w1.close.assert_called()
        w2.close.assert_called()

    async def test_close_room_sends_error_code(self) -> None:
        """_close_room 向玩家发送指定错误码"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        await self.server._close_room(
            room_id,
            reason="hb_timeout",
            error_code=srv.ErrorCode.HEARTBEAT_TIMEOUT,
        )

        # 验证 write 被调用且包含 HEARTBEAT_TIMEOUT 错误码 (1003)
        w.write.assert_called()
        written_data = w.write.call_args[0][0]
        self.assertIn(b'"code":1003', written_data)

    async def test_close_room_cancels_tasks(self) -> None:
        """_close_room 取消所有关联 asyncio 任务"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id
        room = self.server._rooms[room_id]

        # 添加模拟任务
        mock_task = MagicMock()
        mock_task.done.return_value = False
        room.tasks.append(mock_task)

        await self.server._close_room(room_id, reason="test")
        mock_task.cancel.assert_called_once()

    async def test_close_room_cleans_relay(self) -> None:
        """_close_room 清理 Relay 会话和 Token 映射"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        pid_b = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "10.0.0.2", "10.0.0.2:2000",
        )

        room = self.server._rooms[room_id]
        room.state = srv.STATE_PUNCHING

        # 触发 relay
        await self.server._switch_to_relay(room)
        self.assertIsNotNone(room.relay_session)
        token = room.relay_session.relay_token
        self.assertIn(token, self.server._relay_sessions)

        # 关闭房间
        await self.server._close_room(room_id, reason="test")
        self.assertNotIn(token, self.server._relay_sessions)

    async def test_close_room_returns_room_ip_count(self) -> None:
        """_close_room 归还 IP 房间计数"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # _handle_create_room 已调用 add_room, 计数应为 1
        count_before = self.server._rate_limiter._rooms_per_ip.get("10.0.0.1", 0)
        self.assertEqual(count_before, 1)

        await self.server._close_room(room_id, reason="test")

        # 计数应归还到 0
        count_after = self.server._rate_limiter._rooms_per_ip.get("10.0.0.1", 0)
        self.assertEqual(count_after, 0)


# ══════════════════════════════════════════════════════════════════════════════
# 4. _handle_disconnect 统一清理测试
# ══════════════════════════════════════════════════════════════════════════════

class TestHandleDisconnect(_ServerTestBase):
    """TCP 断开 = 隐式 LEAVE_ROOM 测试"""

    async def test_disconnect_with_room(self) -> None:
        """断开有房间的玩家 → 通过 _close_room 统一清理"""
        w1 = _make_mock_writer()
        pid_a = await self.server._handle_create_room(
            {"player_name": "Alice"}, w1, "10.0.0.1", "10.0.0.1:1000",
        )
        room_id = self.server._players[pid_a].room_id

        w2 = _make_mock_writer()
        pid_b = await self.server._handle_join_room(
            {"room_id": room_id, "player_name": "Bob"}, w2, "10.0.0.2", "10.0.0.2:2000",
        )

        # A 断开
        await self.server._handle_disconnect(pid_a, "10.0.0.1:1000", "10.0.0.1")

        # 房间关闭，双方都清理
        self.assertNotIn(room_id, self.server._rooms)
        self.assertNotIn(pid_a, self.server._players)
        self.assertNotIn(pid_b, self.server._players)

    async def test_disconnect_without_room(self) -> None:
        """断开无房间的玩家 → 直接清理玩家"""
        player = _make_player(player_id="p_111111111111", room_id=None)
        self.server._players["p_111111111111"] = player
        self.server._conn_to_player["127.0.0.1:12345"] = "p_111111111111"

        await self.server._handle_disconnect(
            "p_111111111111", "127.0.0.1:12345", "127.0.0.1",
        )

        self.assertNotIn("p_111111111111", self.server._players)
        self.assertNotIn("127.0.0.1:12345", self.server._conn_to_player)

    async def test_disconnect_player_already_cleaned(self) -> None:
        """已被清理的玩家再次断开 → 无异常"""
        await self.server._handle_disconnect(
            "p_nonexistent1", "1.2.3.4:5678", "1.2.3.4",
        )
        # 无异常即通过

    async def test_disconnect_none_player_id(self) -> None:
        """player_id 为 None 的断开 → 安全返回"""
        await self.server._handle_disconnect(None, "1.2.3.4:5678", "1.2.3.4")

    async def test_disconnect_then_close_room_no_double_cleanup(self) -> None:
        """断开触发 _close_room 后，再调 _close_room → 幂等安全"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 断开 → 触发 _close_room
        await self.server._handle_disconnect(pid, "127.0.0.1:1000", "127.0.0.1")

        # 再次调用 _close_room → 幂等
        await self.server._close_room(room_id, reason="duplicate")

        # 无异常，无残留
        self.assertNotIn(room_id, self.server._rooms)
        self.assertNotIn(pid, self.server._players)


# ══════════════════════════════════════════════════════════════════════════════
# 5. WAITING 房间超时测试
# ══════════════════════════════════════════════════════════════════════════════

class TestWaitingTimeout(_ServerTestBase):
    """WAITING 房间 5 分钟超时测试"""

    async def test_waiting_room_not_expired(self) -> None:
        """未超时的 WAITING 房间 → 不清理"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 模拟心跳检查器扫描 (房间刚创建，不应超时)
        now = time.monotonic()
        for rid, room in list(self.server._rooms.items()):
            if room.state == srv.STATE_WAITING:
                if now - room.created_at > srv.WAITING_ROOM_TIMEOUT:
                    await self.server._close_room(rid, reason="WAITING timeout")

        self.assertIn(room_id, self.server._rooms)

    async def test_waiting_room_expired(self) -> None:
        """超时的 WAITING 房间 → 关闭并清理所有映射"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 模拟 5 分钟前创建
        self.server._rooms[room_id].created_at = time.monotonic() - 301

        now = time.monotonic()
        for rid, room in list(self.server._rooms.items()):
            if room.state == srv.STATE_WAITING:
                if now - room.created_at > srv.WAITING_ROOM_TIMEOUT:
                    await self.server._close_room(rid, reason="WAITING timeout")

        # 房间和玩家都应被清理
        self.assertNotIn(room_id, self.server._rooms)
        self.assertNotIn(pid, self.server._players)
        self.assertNotIn("127.0.0.1:1000", self.server._conn_to_player)

    async def test_waiting_timeout_closes_tcp_writer(self) -> None:
        """WAITING 超时 → 关闭创建者的 TCP writer"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 模拟超时
        self.server._rooms[room_id].created_at = time.monotonic() - 301

        await self.server._close_room(room_id, reason="WAITING timeout")

        w.close.assert_called()

    async def test_waiting_timeout_cleans_udp(self) -> None:
        """WAITING 超时 → 清理 UDP 映射"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        # 模拟 UDP 注册
        self.server._players[pid].udp_addr = ("127.0.0.1", 6000)
        self.server._udp_to_player[("127.0.0.1", 6000)] = pid

        # 模拟超时
        self.server._rooms[room_id].created_at = time.monotonic() - 301
        await self.server._close_room(room_id, reason="WAITING timeout")

        self.assertNotIn(("127.0.0.1", 6000), self.server._udp_to_player)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Relay 包速率限制测试
# ══════════════════════════════════════════════════════════════════════════════

class TestRelayRateLimit(_ServerTestBase):
    """Relay 每会话包速率限制测试 (60 pkt/s)"""

    def _make_relay_session(self) -> srv.RelaySession:
        """创建测试用 RelaySession"""
        return srv.RelaySession(
            relay_token="rtk_0123456789abcdef",
            room_id="ABCDEF",
            player_a_id="p_aaaaaaaaaaaa",
            player_b_id="p_bbbbbbbbbbbb",
            created_at=time.monotonic(),
            last_activity=time.monotonic(),
            bandwidth_window_start=time.monotonic(),
            pkt_window_start=time.monotonic(),
        )

    def test_relay_session_has_pkt_fields(self) -> None:
        """RelaySession 包含 pkt_window_start 和 pkt_count 字段"""
        session = self._make_relay_session()
        self.assertIsInstance(session.pkt_window_start, float)
        self.assertIsInstance(session.pkt_count, int)
        self.assertEqual(session.pkt_count, 0)

    def test_pkt_rate_under_limit(self) -> None:
        """包速率未超限 → pkt_count 正常累加"""
        session = self._make_relay_session()
        session.pkt_window_start = time.monotonic()

        for i in range(srv.RELAY_RATE_LIMIT):
            session.pkt_count += 1

        self.assertEqual(session.pkt_count, srv.RELAY_RATE_LIMIT)
        # 不超限
        self.assertFalse(session.pkt_count > srv.RELAY_RATE_LIMIT)

    def test_pkt_rate_over_limit(self) -> None:
        """包速率超限 → 应丢弃"""
        session = self._make_relay_session()
        session.pkt_window_start = time.monotonic()
        session.pkt_count = srv.RELAY_RATE_LIMIT

        # 再加一个
        session.pkt_count += 1
        self.assertTrue(session.pkt_count > srv.RELAY_RATE_LIMIT)

    def test_pkt_window_reset(self) -> None:
        """窗口过期 → 重置计数"""
        session = self._make_relay_session()
        session.pkt_count = 100
        session.pkt_window_start = time.monotonic() - 2.0  # 2 秒前

        now = time.monotonic()
        if now - session.pkt_window_start >= 1.0:
            session.pkt_window_start = now
            session.pkt_count = 0

        self.assertEqual(session.pkt_count, 0)

    def test_bandwidth_fields_preserved(self) -> None:
        """带宽限制字段未被破坏"""
        session = self._make_relay_session()
        self.assertIsInstance(session.bandwidth_window_start, float)
        self.assertIsInstance(session.bandwidth_bytes, int)

    def _install_relay_path(
        self,
        server: srv.P2PServer,
        session: srv.RelaySession,
    ) -> MagicMock:
        transport = MagicMock()
        server._udp_transport = transport
        server._relay_sessions[session.relay_token] = session
        server._players[session.player_a_id] = _make_player(
            player_id=session.player_a_id,
            udp_addr=("198.51.100.20", 5000),
        )
        server._players[session.player_b_id] = _make_player(
            player_id=session.player_b_id,
            udp_addr=("198.51.100.21", 5001),
        )
        return transport

    def test_default_token_bucket_accepts_minecraft_like_burst(self) -> None:
        """Default burst accepts more than a 64KB TCP Relay round trip."""
        server = srv.P2PServer()
        session = self._make_relay_session()
        session.bandwidth_tokens = float(server._relay_burst_bytes)
        session.bandwidth_last_refill = time.monotonic()
        transport = self._install_relay_path(server, session)
        payload = b"x" * 1000

        for _ in range(200):
            server.handle_udp_relay(
                _make_relay_raw_data(session.relay_token, session.player_a_id, payload),
                ("198.51.100.20", 5000),
            )

        self.assertEqual(transport.sendto.call_count, 200)
        self.assertEqual(session.dropped_packets, 0)
        self.assertGreater(server._relay_burst_bytes, 128 * 1024)

    def test_low_token_bucket_limit_drops_with_counters(self) -> None:
        """Configured low capacity still drops and records the reason server-side."""
        server = srv.P2PServer(relay_bytes_per_second=100, relay_burst_bytes=100)
        session = self._make_relay_session()
        session.bandwidth_tokens = float(server._relay_burst_bytes)
        session.bandwidth_last_refill = time.monotonic()
        transport = self._install_relay_path(server, session)
        payload = b"x" * 101

        server.handle_udp_relay(
            _make_relay_raw_data(session.relay_token, session.player_a_id, payload),
            ("198.51.100.20", 5000),
        )

        transport.sendto.assert_not_called()
        self.assertEqual(session.dropped_packets, 1)
        self.assertEqual(session.dropped_bytes, len(payload))

    def test_udp_relay_forwarding_format_unchanged(self) -> None:
        """The existing UDP RELAY packet path still forwards the same payload."""
        server = srv.P2PServer()
        session = self._make_relay_session()
        session.bandwidth_tokens = float(server._relay_burst_bytes)
        session.bandwidth_last_refill = time.monotonic()
        transport = self._install_relay_path(server, session)
        payload = b"legacy-udp-payload"

        server.handle_udp_relay(
            _make_relay_raw_data(session.relay_token, session.player_a_id, payload),
            ("198.51.100.20", 5000),
        )

        forwarded, target = transport.sendto.call_args[0]
        self.assertTrue(forwarded.startswith(b"RELAY\n"))
        self.assertTrue(forwarded.endswith(payload))
        self.assertEqual(target, ("198.51.100.21", 5001))


# ══════════════════════════════════════════════════════════════════════════════
# 7. HEARTBEAT 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestHeartbeat(_ServerTestBase):
    """心跳处理测试"""

    async def test_heartbeat_updates_timestamp(self) -> None:
        """HEARTBEAT 更新 last_heartbeat"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        old_hb = self.server._players[pid].last_heartbeat

        await asyncio.sleep(0.01)
        await self.server._handle_heartbeat(
            {"timestamp": int(time.time())}, w, pid,
        )

        new_hb = self.server._players[pid].last_heartbeat
        self.assertGreaterEqual(new_hb, old_hb)

    async def test_heartbeat_echoes_response(self) -> None:
        """HEARTBEAT 回复包含 timestamp"""
        w = _make_mock_writer()
        await self.server._handle_heartbeat(
            {"timestamp": 1716192000}, w, None,
        )
        w.write.assert_called()
        written = w.write.call_args[0][0]
        self.assertIn(b'"type":"HEARTBEAT"', written)
        self.assertIn(b'"timestamp":', written)


# ══════════════════════════════════════════════════════════════════════════════
# 8. LEAVE_ROOM 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestLeaveRoom(_ServerTestBase):
    """LEAVE_ROOM 处理测试"""

    async def test_leave_room_closes_room(self) -> None:
        """LEAVE_ROOM → 关闭房间"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        await self.server._handle_leave_room({"room_id": room_id}, pid)

        self.assertNotIn(room_id, self.server._rooms)

    async def test_leave_wrong_room(self) -> None:
        """LEAVE 错误的 room_id → 无操作"""
        w = _make_mock_writer()
        pid = await self.server._handle_create_room(
            {"player_name": "Alice"}, w, "127.0.0.1", "127.0.0.1:1000",
        )
        room_id = self.server._players[pid].room_id

        await self.server._handle_leave_room({"room_id": "ZZZZZZ"}, pid)

        # 房间仍在
        self.assertIn(room_id, self.server._rooms)


# ══════════════════════════════════════════════════════════════════════════════
# 9. 协议合规性测试
# ══════════════════════════════════════════════════════════════════════════════

class TestProtocolCompliance(_ServerTestBase):
    """PROTOCOL_LOCK 合规性测试"""

    def test_constants_frozen(self) -> None:
        """协议常量值冻结"""
        self.assertEqual(srv.TCP_PORT, 9000)
        self.assertEqual(srv.UDP_PORT, 9001)
        self.assertEqual(srv.HEARTBEAT_INTERVAL, 5)
        self.assertEqual(srv.HEARTBEAT_TIMEOUT, 15)
        self.assertEqual(srv.SERVER_PUNCH_TIMEOUT, 10)
        self.assertEqual(srv.RELAY_BANDWIDTH_LIMIT, 256)
        self.assertEqual(srv.RELAY_SESSION_TIMEOUT, 7200)
        self.assertEqual(srv.RELAY_IDLE_TIMEOUT, 30)
        self.assertEqual(srv.MAX_UDP_PACKET, 1500)
        self.assertEqual(srv.MAX_GAME_DATA, 1200)
        self.assertEqual(srv.MAX_TCP_MESSAGE, 4096)
        self.assertEqual(srv.ROOM_ID_LENGTH, 6)

    def test_server_local_relay_idle_timeout_extended(self) -> None:
        """Server-local relay cleanup timeout is not the frozen protocol constant."""
        self.assertEqual(srv._RELAY_IDLE_TIMEOUT_SECONDS, 1800)
        self.assertEqual(srv.RELAY_IDLE_TIMEOUT, 30)

    def test_server_local_relay_capacity_defaults(self) -> None:
        """Preview capacity defaults do not modify frozen protocol constants."""
        server = srv.P2PServer()
        self.assertEqual(server._relay_max_sessions_per_ip, 2)
        self.assertEqual(server._relay_max_sessions, 500)
        self.assertEqual(server._relay_bytes_per_second, 32768)
        self.assertEqual(server._relay_burst_bytes, 512 * 1024)
        self.assertEqual(srv.RELAY_BANDWIDTH_LIMIT, 256)

    def test_message_types_frozen(self) -> None:
        """消息类型名冻结"""
        self.assertEqual(srv.MSG_CREATE_ROOM, "CREATE_ROOM")
        self.assertEqual(srv.MSG_ROOM_CREATED, "ROOM_CREATED")
        self.assertEqual(srv.MSG_JOIN_ROOM, "JOIN_ROOM")
        self.assertEqual(srv.MSG_ROOM_JOINED, "ROOM_JOINED")
        self.assertEqual(srv.MSG_PEER_INFO, "PEER_INFO")
        self.assertEqual(srv.MSG_HEARTBEAT, "HEARTBEAT")
        self.assertEqual(srv.MSG_LEAVE_ROOM, "LEAVE_ROOM")
        self.assertEqual(srv.MSG_P2P_SUCCESS, "P2P_SUCCESS")
        self.assertEqual(srv.MSG_P2P_FAILED, "P2P_FAILED")
        self.assertEqual(srv.MSG_RELAY_ENABLED, "RELAY_ENABLED")
        self.assertEqual(srv.MSG_ERROR, "ERROR")

    def test_error_codes_frozen(self) -> None:
        """错误码数值冻结"""
        self.assertEqual(srv.ErrorCode.ROOM_NOT_FOUND, 1001)
        self.assertEqual(srv.ErrorCode.ROOM_FULL, 1002)
        self.assertEqual(srv.ErrorCode.HEARTBEAT_TIMEOUT, 1003)
        self.assertEqual(srv.ErrorCode.RATE_LIMIT, 1004)
        self.assertEqual(srv.ErrorCode.INVALID_MESSAGE, 1005)
        self.assertEqual(srv.ErrorCode.SERVER_FULL, 1006)
        self.assertEqual(srv.ErrorCode.RELAY_UNAVAILABLE, 1007)
        self.assertEqual(srv.ErrorCode.INVALID_TOKEN, 1008)
        self.assertEqual(srv.ErrorCode.PLAYER_NAME_INVALID, 1009)
        self.assertEqual(srv.ErrorCode.DUPLICATE_ROOM, 1010)

    def test_states_frozen(self) -> None:
        """状态机状态值冻结"""
        self.assertEqual(srv.STATE_WAITING, "WAITING")
        self.assertEqual(srv.STATE_READY, "READY")
        self.assertEqual(srv.STATE_PUNCHING, "PUNCHING")
        self.assertEqual(srv.STATE_DIRECT, "DIRECT")
        self.assertEqual(srv.STATE_RELAY, "RELAY")
        self.assertEqual(srv.STATE_CLOSED, "CLOSED")

    def test_id_formats(self) -> None:
        """ID 格式验证"""
        pid = srv.generate_player_id()
        self.assertTrue(srv._RE_PLAYER_ID.match(pid), f"Invalid player_id: {pid}")

        rid = srv.generate_room_id()
        self.assertTrue(srv._RE_ROOM_ID.match(rid), f"Invalid room_id: {rid}")

        rtk = srv.generate_relay_token()
        self.assertTrue(srv._RE_RELAY_TOKEN.match(rtk), f"Invalid relay_token: {rtk}")

    def test_message_envelope_format(self) -> None:
        """信封格式: {\"type\": \"<TYPE>\", \"payload\": {...}}\\n"""
        import json
        data = srv.encode_message("CREATE_ROOM", {"player_name": "test"})
        text = data.decode('utf-8')
        self.assertTrue(text.endswith('\n'))
        obj = json.loads(text.strip())
        self.assertIn("type", obj)
        self.assertIn("payload", obj)
        self.assertEqual(len(obj), 2)  # 只有 type 和 payload, 无其他顶层字段

    def test_udp_prefixes_frozen(self) -> None:
        """UDP 前缀冻结"""
        self.assertEqual(srv.UDP_PREFIX_REG, b"REG\n")
        self.assertEqual(srv.UDP_PREFIX_PUNCH, b"PUNCH\n")
        self.assertEqual(srv.UDP_PREFIX_PING, b"PING\n")
        self.assertEqual(srv.UDP_PREFIX_PONG, b"PONG\n")
        self.assertEqual(srv.UDP_PREFIX_DATA, b"DATA\n")
        self.assertEqual(srv.UDP_PREFIX_RELAY, b"RELAY\n")

    def test_windows_event_loop_policy(self) -> None:
        """Windows 使用 SelectorEventLoopPolicy"""
        if sys.platform == 'win32':
            policy = asyncio.get_event_loop_policy()
            self.assertIsInstance(policy, asyncio.WindowsSelectorEventLoopPolicy)


class TestRelayCapacityConfig(unittest.TestCase):
    """Server-local Relay capacity CLI and environment parsing."""

    def test_cli_relay_capacity_args_parse(self) -> None:
        argv = [
            "server.py",
            "--relay-max-sessions-per-ip", "4",
            "--relay-max-sessions", "600",
            "--relay-bytes-per-second", "2097152",
            "--relay-burst-bytes", "1048576",
        ]
        with patch.object(sys, "argv", argv):
            args = srv._parse_args()
        self.assertEqual(args.relay_max_sessions_per_ip, 4)
        self.assertEqual(args.relay_max_sessions, 600)
        self.assertEqual(args.relay_bytes_per_second, 2097152)
        self.assertEqual(args.relay_burst_bytes, 1048576)

    def test_environment_relay_capacity_values_parse(self) -> None:
        env = {
            "S2PASS_RELAY_MAX_SESSIONS_PER_IP": "5",
            "S2PASS_RELAY_MAX_SESSIONS": "700",
            "S2PASS_RELAY_BYTES_PER_SECOND": "3145728",
            "S2PASS_RELAY_BURST_BYTES": "1572864",
        }
        with patch.dict(os.environ, env, clear=False):
            server = srv.P2PServer()
        self.assertEqual(server._relay_max_sessions_per_ip, 5)
        self.assertEqual(server._relay_max_sessions, 700)
        self.assertEqual(server._relay_bytes_per_second, 3145728)
        self.assertEqual(server._relay_burst_bytes, 1572864)

    def test_cli_values_override_environment(self) -> None:
        env = {
            "S2PASS_RELAY_BYTES_PER_SECOND": "100",
            "S2PASS_RELAY_BURST_BYTES": "100",
        }
        with patch.dict(os.environ, env, clear=False):
            server = srv.P2PServer(
                relay_bytes_per_second=200,
                relay_burst_bytes=300,
            )
        self.assertEqual(server._relay_bytes_per_second, 200)
        self.assertEqual(server._relay_burst_bytes, 300)

    def test_direct_capacity_values_must_remain_positive(self) -> None:
        with self.assertRaises(ValueError):
            srv.P2PServer(relay_burst_bytes=0)


# ══════════════════════════════════════════════════════════════════════════════
# 10. 消息编解码测试
# ══════════════════════════════════════════════════════════════════════════════

class TestMessageCodec(unittest.TestCase):
    """消息编解码测试"""

    def test_encode_single_line(self) -> None:
        """编码结果为单行 JSON + \\n"""
        data = srv.encode_message("HEARTBEAT", {"timestamp": 123})
        text = data.decode('utf-8')
        lines = text.split('\n')
        # 最后一个元素应为空 (因为以 \n 结尾)
        self.assertEqual(lines[-1], '')
        self.assertEqual(len(lines), 2)

    def test_decode_valid(self) -> None:
        """有效消息解码"""
        data = b'{"type":"CREATE_ROOM","payload":{"player_name":"test"}}\n'
        result = srv.decode_message(data)
        self.assertIsNotNone(result)
        msg_type, payload = result
        self.assertEqual(msg_type, "CREATE_ROOM")
        self.assertEqual(payload["player_name"], "test")

    def test_decode_invalid_json(self) -> None:
        """无效 JSON → None"""
        self.assertIsNone(srv.decode_message(b'not json\n'))

    def test_decode_missing_type(self) -> None:
        """缺少 type → None"""
        self.assertIsNone(srv.decode_message(b'{"payload":{}}\n'))

    def test_decode_missing_payload(self) -> None:
        """缺少 payload → None"""
        self.assertIsNone(srv.decode_message(b'{"type":"TEST"}\n'))


# ══════════════════════════════════════════════════════════════════════════════
# 11. 限流器测试
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimiter(unittest.TestCase):
    """限流器基础测试"""

    def test_token_bucket_consume(self) -> None:
        bucket = srv.TokenBucket(10.0, 10.0)
        for _ in range(10):
            self.assertTrue(bucket.consume())
        # 第 11 个应失败
        self.assertFalse(bucket.consume())

    def test_sliding_window(self) -> None:
        window = srv.SlidingWindowCounter(10.0, 3)
        self.assertTrue(window.check_and_add())
        self.assertTrue(window.check_and_add())
        self.assertTrue(window.check_and_add())
        self.assertFalse(window.check_and_add())

    def test_ip_ban(self) -> None:
        rl = srv.IPRateLimiter()
        self.assertFalse(rl.is_banned("1.2.3.4"))
        rl.ban_ip("1.2.3.4", 10)
        self.assertTrue(rl.is_banned("1.2.3.4"))

    def test_localhost_whitelist(self) -> None:
        """测试 localhost 速率限制白名单特性"""
        rl = srv.IPRateLimiter()
        # 1. 127.0.0.1、127.1.2.3、::1 应被 _is_whitelist 识别为 True。
        self.assertTrue(rl._is_whitelist("127.0.0.1"))
        self.assertTrue(rl._is_whitelist("127.1.2.3"))
        self.assertTrue(rl._is_whitelist("::1"))

        # 2. IPv6-mapped IPv4 localhost 必须识别为白名单
        self.assertTrue(rl._is_whitelist("::ffff:127.0.0.1"),
                        "::ffff:127.0.0.1 must be whitelisted")

        # 3. 192.168.1.1、10.0.0.1、公网 IP 应为 False。
        self.assertFalse(rl._is_whitelist("192.168.1.1"))
        self.assertFalse(rl._is_whitelist("10.0.0.1"))
        self.assertFalse(rl._is_whitelist("8.8.8.8"))
        # IPv6-mapped 公网 IP 应为 False
        self.assertFalse(rl._is_whitelist("::ffff:8.8.8.8"))
        self.assertFalse(rl._is_whitelist("::ffff:192.168.1.1"))

        # 4. 验证白名单 IP 能够绕过限流和封禁
        # 封禁 127.0.0.1 应无效果 (仍返回 False)
        rl.ban_ip("127.0.0.1", 10)
        self.assertFalse(rl.is_banned("127.0.0.1"))

        # 127.0.0.1 的 UDP 速率应始终允许 (返回 True)
        # UDP 限制为 100 包/秒，连续消耗 101 次都不应被限流
        for _ in range(101):
            self.assertTrue(rl.check_udp_rate("127.0.0.1"))

        # 127.0.0.1 的连接和房间计数应不受限制 (可以无限加)
        # 额度上限是 10，直接加 15 次都不应被阻断
        for _ in range(15):
            self.assertTrue(rl.add_connection("127.0.0.1"))
            self.assertTrue(rl.add_room("127.0.0.1"))


# ══════════════════════════════════════════════════════════════════════════════
# 12. 玩家名验证测试
# ══════════════════════════════════════════════════════════════════════════════

class TestPlayerNameValidation(unittest.TestCase):
    """玩家名验证测试"""

    def test_valid_name(self) -> None:
        self.assertTrue(srv.P2PServer._validate_player_name("Alice"))
        self.assertTrue(srv.P2PServer._validate_player_name("玩家1"))
        self.assertTrue(srv.P2PServer._validate_player_name("A" * 32))

    def test_invalid_empty(self) -> None:
        self.assertFalse(srv.P2PServer._validate_player_name(""))

    def test_invalid_too_long(self) -> None:
        self.assertFalse(srv.P2PServer._validate_player_name("A" * 33))

    def test_invalid_whitespace(self) -> None:
        self.assertFalse(srv.P2PServer._validate_player_name("   "))

    def test_invalid_type(self) -> None:
        self.assertFalse(srv.P2PServer._validate_player_name(123))
        self.assertFalse(srv.P2PServer._validate_player_name(None))


# ══════════════════════════════════════════════════════════════════════════════
# 13. 房间 ID 生成可读性测试
# ══════════════════════════════════════════════════════════════════════════════

class TestRoomIdGeneration(unittest.TestCase):
    """房间 ID 生成可读性: 排除 O/0/I/1"""

    def test_room_id_length_is_6(self) -> None:
        """生成房间 ID 长度仍为 ROOM_ID_LENGTH (6)"""
        for _ in range(50):
            rid = srv.generate_room_id()
            self.assertEqual(len(rid), srv.ROOM_ID_LENGTH)

    def test_room_id_allowed_chars_only(self) -> None:
        """生成房间 ID 仅使用允许字符集 (不含 O/0/I/1)"""
        allowed = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        for _ in range(100):
            rid = srv.generate_room_id()
            for ch in rid:
                self.assertIn(ch, allowed,
                              f"room_id {rid} contains forbidden char '{ch}'")

    def test_room_id_excludes_O(self) -> None:
        """生成房间 ID 不含 'O'"""
        for _ in range(100):
            rid = srv.generate_room_id()
            self.assertNotIn('O', rid)

    def test_room_id_excludes_0(self) -> None:
        """生成房间 ID 不含 '0'"""
        for _ in range(100):
            rid = srv.generate_room_id()
            self.assertNotIn('0', rid)

    def test_room_id_excludes_I(self) -> None:
        """生成房间 ID 不含 'I'"""
        for _ in range(100):
            rid = srv.generate_room_id()
            self.assertNotIn('I', rid)

    def test_room_id_excludes_1(self) -> None:
        """生成房间 ID 不含 '1'"""
        for _ in range(100):
            rid = srv.generate_room_id()
            self.assertNotIn('1', rid)

    def test_room_id_matches_regex(self) -> None:
        """生成房间 ID 仍匹配协议正则 [A-Z0-9]{6}"""
        for _ in range(50):
            rid = srv.generate_room_id()
            self.assertTrue(srv._RE_ROOM_ID.match(rid),
                            f"room_id {rid} does not match _RE_ROOM_ID")

    def test_join_still_accepts_legacy_room_ids(self) -> None:
        """JOIN 仍接受含有 O/0/I/1 的旧格式 room_id"""
        # 旧格式 room_id 应仍然通过正则验证
        legacy_ids = ["O0I1AB", "ABCDEF", "123456", "Z9Y8X7", "OOOOOO", "000000"]
        for rid in legacy_ids:
            self.assertTrue(srv._RE_ROOM_ID.match(rid),
                            f"Legacy room_id {rid} should still pass regex")


# ══════════════════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
