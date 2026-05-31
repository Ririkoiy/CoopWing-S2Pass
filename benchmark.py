#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  P2P UDP Relay 性能基准测试工具
  遵循 PROTOCOL_LOCK v1.0.0
═══════════════════════════════════════════════════════════════════════════════

测试指标:
  1. RTT (Round-Trip Time)        — 平均 / 最大 / P50 / P95 / P99
  2. Packet Loss                  — 发送/接收/丢包率
  3. Jitter                       — 连续包间 RTT 抖动
  4. Packets Per Second (PPS)     — 实际达到的包速率
  5. Relay CPU 压力               — 多连接并发压力测试
  6. 高频小包场景                  — 小包高频发送模式

测试模式:
  - localhost   本地回环测试
  - lan         局域网测试
  - vps         公网 VPS 测试

压力等级:
  PPS:  60 / 120 / 240 / 1000
  包大小: 64B / 256B / 1024B

依赖: 仅 Python 标准库
兼容: Windows (WindowsSelectorEventLoopPolicy) + Linux
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
# Windows 事件循环策略 — 必须在所有入口文件顶部执行
# ══════════════════════════════════════════════════════════════════════════════
import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import json
import logging
import math
import os
import secrets
import statistics
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# 协议常量 — 冻结，禁止修改任何值 (from PROTOCOL_LOCK v1.0.0)
# ══════════════════════════════════════════════════════════════════════════════

TCP_PORT:                int = 9000
UDP_PORT:                int = 9001
HEARTBEAT_INTERVAL:      int = 5        # 秒
HEARTBEAT_TIMEOUT:       int = 15       # 秒
RECONNECT_DELAY:         int = 3        # 秒
RECONNECT_MAX_RETRIES:   int = 3
RECONNECT_BACKOFF:       int = 2        # 倍数
UDP_KEEPALIVE_INTERVAL:  int = 3        # 秒
UDP_KEEPALIVE_TIMEOUT:   int = 10       # 秒
PUNCH_INTERVAL:        float = 0.2      # 秒
PUNCH_MAX_COUNT:         int = 25
PUNCH_TIMEOUT:           int = 5        # 秒
PING_TIMEOUT:            int = 3        # 秒
RELAY_BANDWIDTH_LIMIT:   int = 256      # Kbps
RELAY_SESSION_TIMEOUT:   int = 7200     # 秒
RELAY_IDLE_TIMEOUT:      int = 30       # 秒
SERVER_PUNCH_TIMEOUT:    int = 10       # 秒
MAX_UDP_PACKET:          int = 1500     # 字节
MAX_GAME_DATA:           int = 1200     # 字节
MAX_TCP_MESSAGE:         int = 4096     # 字节
ROOM_ID_LENGTH:          int = 6

# ══════════════════════════════════════════════════════════════════════════════
# 消息类型 — 冻结
# ══════════════════════════════════════════════════════════════════════════════

MSG_CREATE_ROOM:   str = "CREATE_ROOM"
MSG_ROOM_CREATED:  str = "ROOM_CREATED"
MSG_JOIN_ROOM:     str = "JOIN_ROOM"
MSG_ROOM_JOINED:   str = "ROOM_JOINED"
MSG_PEER_INFO:     str = "PEER_INFO"
MSG_HEARTBEAT:     str = "HEARTBEAT"
MSG_LEAVE_ROOM:    str = "LEAVE_ROOM"
MSG_P2P_SUCCESS:   str = "P2P_SUCCESS"
MSG_P2P_FAILED:    str = "P2P_FAILED"
MSG_RELAY_ENABLED: str = "RELAY_ENABLED"
MSG_ERROR:         str = "ERROR"

# ══════════════════════════════════════════════════════════════════════════════
# UDP 前缀 — 冻结
# ══════════════════════════════════════════════════════════════════════════════

UDP_PREFIX_REG:   bytes = b"REG\n"       # 4B
UDP_PREFIX_PUNCH: bytes = b"PUNCH\n"     # 6B
UDP_PREFIX_PING:  bytes = b"PING\n"      # 5B
UDP_PREFIX_PONG:  bytes = b"PONG\n"      # 5B
UDP_PREFIX_DATA:  bytes = b"DATA\n"      # 5B
UDP_PREFIX_RELAY: bytes = b"RELAY\n"     # 6B

# ══════════════════════════════════════════════════════════════════════════════
# 日志配置
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger: logging.Logger = logging.getLogger("Benchmark")


# ══════════════════════════════════════════════════════════════════════════════
# TCP 消息编码/解码
# ══════════════════════════════════════════════════════════════════════════════

def encode_message(msg_type: str, payload: dict) -> bytes:
    """编码 TCP 消息: {"type": "<TYPE>", "payload": {…}}\n"""
    msg = json.dumps({"type": msg_type, "payload": payload},
                     ensure_ascii=False, separators=(',', ':'))
    return (msg + '\n').encode('utf-8')


def decode_message(line: bytes) -> Optional[Tuple[str, dict]]:
    """解码 TCP 消息，返回 (type, payload) 或 None"""
    try:
        text = line.decode('utf-8').strip()
        if not text:
            return None
        obj = json.loads(text)
        msg_type = obj.get("type")
        payload = obj.get("payload")
        if not isinstance(msg_type, str) or not isinstance(payload, dict):
            return None
        return msg_type, payload
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 测试结果数据结构
# ══════════════════════════════════════════════════════════════════════════════

# ── Relay 限流常量 (RELAY_RATE_LIMIT = 60 pkt/s/session, 来自 server.py) ──
# 用于在报告中标注测试类别:
#   <= 60 PPS: 协议内合规 Relay 测试
#   > 60 PPS:  限流压力测试 (丢包可能是预期行为)
_RELAY_COMPLIANT_PPS: int = 60


def _test_category_label(target_pps: int) -> str:
    """根据目标 PPS 返回测试类别标签"""
    if target_pps <= _RELAY_COMPLIANT_PPS:
        return "[合规] 协议内 Relay 测试"
    else:
        return f"[压力] 超限流测试 (>{_RELAY_COMPLIANT_PPS} PPS, 丢包可能是预期行为)"


@dataclass
class BenchmarkResult:
    """单次测试结果"""
    test_name:        str = ""
    mode:             str = ""           # localhost / lan / vps
    target_pps:       int = 0
    packet_size:      int = 0
    duration_sec:     float = 0.0

    # ── 统计 ──
    packets_sent:     int = 0
    packets_received: int = 0
    packets_lost:     int = 0
    loss_rate:        float = 0.0        # %

    rtt_avg_ms:       float = 0.0
    rtt_max_ms:       float = 0.0
    rtt_min_ms:       float = 0.0
    rtt_p50_ms:       float = 0.0
    rtt_p95_ms:       float = 0.0
    rtt_p99_ms:       float = 0.0

    jitter_ms:        float = 0.0        # 平均抖动
    jitter_max_ms:    float = 0.0        # 最大抖动

    actual_pps:       float = 0.0        # 实际达到的 PPS

    errors:           List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# UDP 发送方协议 (Player A — 发送端)
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkSenderProtocol(asyncio.DatagramProtocol):
    """
    基准测试发送方 UDP 协议

    职责:
    1. UDP REG 注册
    2. 发送 RELAY 测试包 (带时间戳 + 序列号)
    3. 接收回程 RELAY 包并计算 RTT
    """

    def __init__(
        self,
        server_addr: Tuple[str, int],
        relay_token: str,
        player_id: str,
        room_id: str,
    ) -> None:
        self._server_addr = server_addr
        self._relay_token = relay_token
        self._player_id = player_id
        self._room_id = room_id
        self._transport: Optional[asyncio.DatagramTransport] = None

        # ── 测试状态 ──
        self._send_timestamps: Dict[int, float] = {}   # seq -> send_time (perf_counter)
        self._rtt_samples: List[float] = []
        self._packets_sent: int = 0
        self._packets_received: int = 0
        self._registered: bool = False
        self._ready_event = asyncio.Event()

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self._transport = None

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """接收回程 RELAY 包"""
        recv_time = time.perf_counter()

        if len(data) < 6:
            return

        # 只处理 RELAY 前缀的包
        if data[:6] != b'RELAY\n':
            return

        payload = data[6:]
        newline_pos = payload.find(b'\n')
        if newline_pos < 0:
            return

        game_data = payload[newline_pos + 1:]
        if len(game_data) < 12:
            return

        # 解析序列号 (前 4 字节) + 发送时间戳 (8 字节 double)
        seq = struct.unpack('!I', game_data[:4])[0]
        send_time = struct.unpack('!d', game_data[4:12])[0]

        rtt = (recv_time - send_time) * 1000.0  # ms
        if rtt >= 0:
            self._rtt_samples.append(rtt)
        self._packets_received += 1

        # 从发送时间戳表中移除
        self._send_timestamps.pop(seq, None)

    def error_received(self, exc: Exception) -> None:
        logger.warning("Sender UDP 错误: %s", exc)

    async def register(self) -> None:
        """发送 UDP REG 注册包"""
        if self._transport is None:
            return

        reg_data = UDP_PREFIX_REG + json.dumps(
            {"player_id": self._player_id, "room_id": self._room_id},
            separators=(',', ':'),
        ).encode('utf-8')

        # 发送多次确保注册成功
        for _ in range(3):
            self._transport.sendto(reg_data, self._server_addr)
            await asyncio.sleep(0.1)

        self._registered = True
        logger.info("Sender 已注册: %s", self._player_id)

    def send_relay_packet(self, seq: int, payload_size: int) -> None:
        """发送一个 RELAY 测试包"""
        if self._transport is None:
            return

        now = time.perf_counter()

        # 构建 RELAY header JSON
        header = json.dumps(
            {"relay_token": self._relay_token, "player_id": self._player_id},
            separators=(',', ':'),
        ).encode('utf-8')

        # 构建 game_data: [4B seq] [8B timestamp] [padding]
        game_data = struct.pack('!I', seq) + struct.pack('!d', now)

        # 填充到目标大小
        current_size = len(game_data)
        if payload_size > current_size:
            game_data += b'\x00' * (payload_size - current_size)

        # 完整包: RELAY\n<header>\n<game_data>
        packet = UDP_PREFIX_RELAY + header + b'\n' + game_data

        # 检查包大小限制
        if len(packet) > MAX_UDP_PACKET:
            return

        self._send_timestamps[seq] = now
        self._transport.sendto(packet, self._server_addr)
        self._packets_sent += 1

    @property
    def rtt_samples(self) -> List[float]:
        return self._rtt_samples

    @property
    def packets_sent(self) -> int:
        return self._packets_sent

    @property
    def packets_received(self) -> int:
        return self._packets_received


# ══════════════════════════════════════════════════════════════════════════════
# UDP 接收方协议 (Player B — 回弹端)
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkReceiverProtocol(asyncio.DatagramProtocol):
    """
    基准测试接收方 UDP 协议

    职责:
    1. UDP REG 注册
    2. 接收 RELAY 包并立即回弹 (echo)
    """

    def __init__(
        self,
        server_addr: Tuple[str, int],
        relay_token: str,
        player_id: str,
        room_id: str,
    ) -> None:
        self._server_addr = server_addr
        self._relay_token = relay_token
        self._player_id = player_id
        self._room_id = room_id
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._packets_echoed: int = 0
        self._registered: bool = False

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self._transport = None

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """接收 RELAY 包并回弹"""
        if len(data) < 6:
            return

        if data[:6] != b'RELAY\n':
            return

        payload = data[6:]
        newline_pos = payload.find(b'\n')
        if newline_pos < 0:
            return

        game_data = payload[newline_pos + 1:]

        # 立即回弹: 用自己的 player_id 构建 RELAY 包
        if self._transport is None:
            return

        header = json.dumps(
            {"relay_token": self._relay_token, "player_id": self._player_id},
            separators=(',', ':'),
        ).encode('utf-8')

        echo_packet = UDP_PREFIX_RELAY + header + b'\n' + game_data

        if len(echo_packet) <= MAX_UDP_PACKET:
            self._transport.sendto(echo_packet, self._server_addr)
            self._packets_echoed += 1

    def error_received(self, exc: Exception) -> None:
        logger.warning("Receiver UDP 错误: %s", exc)

    async def register(self) -> None:
        """发送 UDP REG 注册包"""
        if self._transport is None:
            return

        reg_data = UDP_PREFIX_REG + json.dumps(
            {"player_id": self._player_id, "room_id": self._room_id},
            separators=(',', ':'),
        ).encode('utf-8')

        for _ in range(3):
            self._transport.sendto(reg_data, self._server_addr)
            await asyncio.sleep(0.1)

        self._registered = True
        logger.info("Receiver 已注册: %s", self._player_id)

    @property
    def packets_echoed(self) -> int:
        return self._packets_echoed


# ══════════════════════════════════════════════════════════════════════════════
# TCP 客户端 — 房间创建/加入/心跳
# ══════════════════════════════════════════════════════════════════════════════

class TCPClient:
    """TCP 信令客户端"""

    def __init__(self, server_host: str, server_port: int = TCP_PORT) -> None:
        self._server_host = server_host
        self._server_port = server_port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """建立 TCP 连接"""
        self._reader, self._writer = await asyncio.open_connection(
            self._server_host, self._server_port,
        )
        logger.info("TCP 已连接: %s:%d", self._server_host, self._server_port)

    async def close(self) -> None:
        """关闭 TCP 连接"""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._writer is not None:
            if not self._writer.is_closing():
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except (OSError, ConnectionResetError, ConnectionAbortedError):
                    pass
            self._writer = None
            self._reader = None

    async def send(self, msg_type: str, payload: dict) -> None:
        """发送 TCP 消息"""
        if self._writer is None or self._writer.is_closing():
            return
        data = encode_message(msg_type, payload)
        self._writer.write(data)
        await self._writer.drain()

    async def recv(self, timeout: float = 10.0) -> Optional[Tuple[str, dict]]:
        """接收 TCP 消息"""
        if self._reader is None:
            return None
        try:
            line = await asyncio.wait_for(
                self._reader.readline(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None
        if not line:
            return None
        return decode_message(line)

    async def create_room(self, player_name: str) -> Optional[Tuple[str, str]]:
        """创建房间，返回 (room_id, player_id)"""
        await self.send(MSG_CREATE_ROOM, {"player_name": player_name})
        result = await self.recv()
        if result is None:
            return None
        msg_type, payload = result
        if msg_type == MSG_ROOM_CREATED:
            return payload.get("room_id"), payload.get("player_id")
        elif msg_type == MSG_ERROR:
            logger.error("创建房间失败: code=%s msg=%s",
                         payload.get("code"), payload.get("message"))
        return None

    async def join_room(self, room_id: str, player_name: str) -> Optional[Tuple[str, str]]:
        """加入房间，返回 (room_id, player_id)"""
        await self.send(MSG_JOIN_ROOM, {
            "room_id": room_id, "player_name": player_name,
        })
        result = await self.recv()
        if result is None:
            return None
        msg_type, payload = result
        if msg_type == MSG_ROOM_JOINED:
            return payload.get("room_id"), payload.get("player_id")
        elif msg_type == MSG_ERROR:
            logger.error("加入房间失败: code=%s msg=%s",
                         payload.get("code"), payload.get("message"))
        return None

    async def wait_for_message(self, expected_type: str, timeout: float = 30.0) -> Optional[dict]:
        """等待特定类型的消息"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            result = await self.recv(timeout=remaining)
            if result is None:
                continue
            msg_type, payload = result
            if msg_type == expected_type:
                return payload
            elif msg_type == MSG_ERROR:
                logger.error("收到错误: code=%s msg=%s",
                             payload.get("code"), payload.get("message"))
        return None

    def start_heartbeat(self) -> None:
        """启动心跳任务"""
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """心跳循环"""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self.send(MSG_HEARTBEAT, {
                    "timestamp": int(time.time()),
                })
                # 读取心跳回复 (非阻塞，丢弃即可)
                try:
                    result = await self.recv(timeout=1.0)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# 基准测试核心引擎
# ══════════════════════════════════════════════════════════════════════════════

class BenchmarkEngine:
    """
    基准测试引擎

    测试流程:
    1. 创建两个 TCP 客户端 (Player A + Player B)
    2. Player A 创建房间
    3. Player B 加入房间
    4. 等待双方 UDP 注册 → PEER_INFO → PUNCHING
    5. Player A 发送 P2P_FAILED → 切换到 RELAY
    6. 开始 RELAY 性能测试
    """

    def __init__(
        self,
        server_host: str,
        server_tcp_port: int = TCP_PORT,
        server_udp_port: int = UDP_PORT,
        mode: str = "localhost",
    ) -> None:
        self._server_host = server_host
        self._server_tcp_port = server_tcp_port
        self._server_udp_port = server_udp_port
        self._mode = mode
        self._results: List[BenchmarkResult] = []

    async def setup_relay_session(
        self,
    ) -> Optional[Tuple[
        TCPClient, TCPClient,
        BenchmarkSenderProtocol, BenchmarkReceiverProtocol,
        str, asyncio.DatagramTransport, asyncio.DatagramTransport,
    ]]:
        """
        建立 Relay 会话

        返回: (tcp_a, tcp_b, sender_proto, receiver_proto, relay_token,
               sender_transport, receiver_transport)
        """

        tcp_a = TCPClient(self._server_host, self._server_tcp_port)
        tcp_b = TCPClient(self._server_host, self._server_tcp_port)

        try:
            # ── 1. TCP 连接 ──
            await tcp_a.connect()
            await tcp_b.connect()

            # ── 2. Player A 创建房间 ──
            result_a = await tcp_a.create_room("BenchPlayerA")
            if result_a is None:
                logger.error("创建房间失败")
                await tcp_a.close()
                await tcp_b.close()
                return None

            room_id, player_a_id = result_a
            logger.info("房间创建成功: room=%s player_a=%s", room_id, player_a_id)

            # ── 3. Player B 加入房间 ──
            result_b = await tcp_b.join_room(room_id, "BenchPlayerB")
            if result_b is None:
                logger.error("加入房间失败")
                await tcp_a.close()
                await tcp_b.close()
                return None

            _, player_b_id = result_b
            logger.info("加入房间成功: player_b=%s", player_b_id)

            # ── 4. 创建 UDP 端点 ──
            loop = asyncio.get_running_loop()
            server_addr = (self._server_host, self._server_udp_port)

            sender_proto = BenchmarkSenderProtocol(
                server_addr, "", player_a_id, room_id,
            )
            receiver_proto = BenchmarkReceiverProtocol(
                server_addr, "", player_b_id, room_id,
            )

            sender_transport, _ = await loop.create_datagram_endpoint(
                lambda: sender_proto,
                local_addr=('0.0.0.0', 0),
            )
            receiver_transport, _ = await loop.create_datagram_endpoint(
                lambda: receiver_proto,
                local_addr=('0.0.0.0', 0),
            )

            # ── 5. UDP 注册 ──
            await sender_proto.register()
            await receiver_proto.register()

            # 等待服务端处理注册 + 发送 PEER_INFO
            await asyncio.sleep(0.5)

            # ── 6. 消费 PEER_INFO (双方都会收到) ──
            # Player A 读取 PEER_INFO
            peer_info_a = await tcp_a.wait_for_message(MSG_PEER_INFO, timeout=10.0)
            if peer_info_a is None:
                logger.error("Player A 未收到 PEER_INFO")
                sender_transport.close()
                receiver_transport.close()
                await tcp_a.close()
                await tcp_b.close()
                return None

            # Player B 读取 PEER_INFO
            peer_info_b = await tcp_b.wait_for_message(MSG_PEER_INFO, timeout=10.0)
            if peer_info_b is None:
                logger.error("Player B 未收到 PEER_INFO")
                sender_transport.close()
                receiver_transport.close()
                await tcp_a.close()
                await tcp_b.close()
                return None

            logger.info("双方 PEER_INFO 已收到，进入 PUNCHING 状态")

            # ── 7. Player A 发送 P2P_FAILED → 触发 RELAY ──
            await tcp_a.send(MSG_P2P_FAILED, {
                "room_id": room_id,
                "reason": "TIMEOUT",
            })

            # ── 8. 等待 RELAY_ENABLED ──
            relay_payload_a = await tcp_a.wait_for_message(MSG_RELAY_ENABLED, timeout=15.0)
            if relay_payload_a is None:
                logger.error("Player A 未收到 RELAY_ENABLED")
                sender_transport.close()
                receiver_transport.close()
                await tcp_a.close()
                await tcp_b.close()
                return None

            relay_payload_b = await tcp_b.wait_for_message(MSG_RELAY_ENABLED, timeout=15.0)
            if relay_payload_b is None:
                logger.error("Player B 未收到 RELAY_ENABLED")
                sender_transport.close()
                receiver_transport.close()
                await tcp_a.close()
                await tcp_b.close()
                return None

            relay_token = relay_payload_a.get("relay_token", "")
            logger.info("RELAY 已启用: token=%s", relay_token)

            # 更新协议中的 relay_token
            sender_proto._relay_token = relay_token
            receiver_proto._relay_token = relay_token

            # 启动心跳
            tcp_a.start_heartbeat()
            tcp_b.start_heartbeat()

            return (
                tcp_a, tcp_b,
                sender_proto, receiver_proto,
                relay_token,
                sender_transport, receiver_transport,
            )

        except Exception as e:
            logger.exception("建立 Relay 会话异常: %s", e)
            await tcp_a.close()
            await tcp_b.close()
            return None

    async def run_single_test(
        self,
        test_name: str,
        target_pps: int,
        packet_size: int,
        duration_sec: float,
        sender_proto: BenchmarkSenderProtocol,
    ) -> BenchmarkResult:
        """
        执行单次基准测试

        使用高精度定时器以恒定速率发送包
        """

        result = BenchmarkResult(
            test_name=test_name,
            mode=self._mode,
            target_pps=target_pps,
            packet_size=packet_size,
            duration_sec=duration_sec,
        )

        logger.info("═══ 开始测试: %s ═══", test_name)
        logger.info("  目标 PPS: %d, 包大小: %dB, 时长: %.1fs",
                     target_pps, packet_size, duration_sec)

        # 重置发送方统计
        sender_proto._rtt_samples.clear()
        sender_proto._send_timestamps.clear()
        sender_proto._packets_sent = 0
        sender_proto._packets_received = 0

        # ── 高精度发包循环 ──
        interval = 1.0 / target_pps if target_pps > 0 else 1.0
        start_time = time.perf_counter()
        seq = 0

        try:
            while True:
                elapsed = time.perf_counter() - start_time
                if elapsed >= duration_sec:
                    break

                # 发送一个包
                sender_proto.send_relay_packet(seq, packet_size)
                seq += 1

                # 高精度等待 — 计算下一个包的精确发送时刻
                next_send_time = start_time + seq * interval
                now = time.perf_counter()
                sleep_time = next_send_time - now

                if sleep_time > 0.001:
                    # 使用 asyncio.sleep 让出事件循环，以便接收数据
                    await asyncio.sleep(sleep_time)
                elif sleep_time > 0:
                    # 极短等待 — busy wait 但让出事件循环
                    await asyncio.sleep(0)
                else:
                    # 已经落后 — 不等待但仍然让出事件循环
                    if seq % 10 == 0:
                        await asyncio.sleep(0)

        except Exception as e:
            result.errors.append(str(e))

        # ── 等待尾部包的回程 ──
        send_duration = time.perf_counter() - start_time
        await asyncio.sleep(min(2.0, duration_sec * 0.5))

        actual_duration = time.perf_counter() - start_time

        # ══════════════════════════════════════════════════════════════════
        # 统计计算
        # ══════════════════════════════════════════════════════════════════

        result.packets_sent = sender_proto.packets_sent
        result.packets_received = sender_proto.packets_received
        result.packets_lost = result.packets_sent - result.packets_received
        result.loss_rate = (
            (result.packets_lost / result.packets_sent * 100.0)
            if result.packets_sent > 0 else 0.0
        )

        samples = sender_proto.rtt_samples
        if samples:
            sorted_samples = sorted(samples)
            n = len(sorted_samples)

            result.rtt_avg_ms = statistics.mean(sorted_samples)
            result.rtt_max_ms = sorted_samples[-1]
            result.rtt_min_ms = sorted_samples[0]
            result.rtt_p50_ms = sorted_samples[int(n * 0.50)]
            result.rtt_p95_ms = sorted_samples[min(int(n * 0.95), n - 1)]
            result.rtt_p99_ms = sorted_samples[min(int(n * 0.99), n - 1)]

            # Jitter: 连续 RTT 样本间差值的均值
            if len(sorted_samples) >= 2:
                # 使用原始顺序的 samples 来计算 jitter
                jitters = [
                    abs(samples[i] - samples[i - 1])
                    for i in range(1, len(samples))
                ]
                result.jitter_ms = statistics.mean(jitters)
                result.jitter_max_ms = max(jitters)

        # actual_pps 基于发包阶段计算 (不含尾部等待回包时间)
        result.actual_pps = result.packets_sent / send_duration if send_duration > 0 else 0.0

        return result

    async def run_benchmark_suite(
        self,
        pps_list: List[int],
        size_list: List[int],
        duration_sec: float = 10.0,
    ) -> List[BenchmarkResult]:
        """运行完整基准测试套件"""

        all_results: List[BenchmarkResult] = []

        for target_pps in pps_list:
            for packet_size in size_list:
                # 为每组测试建立独立的 Relay 会话
                session = await self.setup_relay_session()
                if session is None:
                    logger.error("无法建立 Relay 会话，跳过 PPS=%d SIZE=%d",
                                 target_pps, packet_size)
                    err_result = BenchmarkResult(
                        test_name=f"PPS={target_pps}_SIZE={packet_size}",
                        mode=self._mode,
                        target_pps=target_pps,
                        packet_size=packet_size,
                        errors=["Failed to establish relay session"],
                    )
                    all_results.append(err_result)

                    # 等待一段时间让限流重置
                    logger.info("等待 12 秒让限流重置...")
                    await asyncio.sleep(12)
                    continue

                (tcp_a, tcp_b,
                 sender_proto, receiver_proto,
                 relay_token,
                 sender_transport, receiver_transport) = session

                try:
                    test_name = f"RELAY_PPS{target_pps}_SIZE{packet_size}B"
                    result = await self.run_single_test(
                        test_name=test_name,
                        target_pps=target_pps,
                        packet_size=packet_size,
                        duration_sec=duration_sec,
                        sender_proto=sender_proto,
                    )
                    all_results.append(result)
                    self._print_result(result)

                finally:
                    # ── 资源清理 ──
                    sender_transport.close()
                    receiver_transport.close()
                    await tcp_a.close()
                    await tcp_b.close()

                # 测试间隔 — 等待限流窗口重置
                logger.info("测试间隔，等待 3 秒...")
                await asyncio.sleep(3)

        self._results = all_results
        return all_results

    async def run_stress_test(
        self,
        concurrent_sessions: int = 5,
        pps_per_session: int = 60,
        packet_size: int = 64,
        duration_sec: float = 15.0,
    ) -> BenchmarkResult:
        """
        CPU 压力测试 — 多个并发 Relay 会话

        注意: 服务端有 IP 限流限制 (MAX_RELAY_PER_IP=2, CREATE_ROOM_RATE=1/10s)
        localhost 模式下并发会话受限。
        实际应从多个不同 IP 测试。
        此处以单 IP 最大能力测试。
        """

        logger.info("═══════════════════════════════════════════════════════")
        logger.info("  CPU 压力测试")
        logger.info("  目标并发会话: %d (受 IP 限流限制)", concurrent_sessions)
        logger.info("  每会话 PPS:   %d", pps_per_session)
        logger.info("  包大小:       %dB", packet_size)
        logger.info("  时长:         %.1fs", duration_sec)
        logger.info("═══════════════════════════════════════════════════════")

        # 由于单 IP 限流 (MAX_RELAY_PER_IP=2, CREATE_ROOM_RATE=1/10s),
        # 实际只能建立有限的并发会话
        actual_sessions = min(concurrent_sessions, 2)  # 受 MAX_RELAY_PER_IP 限制
        logger.info("  实际并发会话 (受限于 MAX_RELAY_PER_IP): %d", actual_sessions)

        sessions = []
        for i in range(actual_sessions):
            if i > 0:
                # CREATE_ROOM_RATE = 1次/10秒/IP
                logger.info("  等待 CREATE_ROOM 限流窗口重置 (11s)...")
                await asyncio.sleep(11)

            session = await self.setup_relay_session()
            if session is None:
                logger.warning("  会话 %d 建立失败", i)
                continue
            sessions.append(session)
            logger.info("  会话 %d 已建立", i)

        if not sessions:
            return BenchmarkResult(
                test_name="STRESS_TEST",
                mode=self._mode,
                errors=["No sessions could be established"],
            )

        # ── 并发发包 ──
        async def _run_session(idx: int, sess_tuple):
            (_, _, sender_proto, _, _, _, _) = sess_tuple
            return await self.run_single_test(
                test_name=f"STRESS_SESSION_{idx}",
                target_pps=pps_per_session,
                packet_size=packet_size,
                duration_sec=duration_sec,
                sender_proto=sender_proto,
            )

        tasks = [
            asyncio.ensure_future(_run_session(i, s))
            for i, s in enumerate(sessions)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── 汇总结果 ──
        aggregate = BenchmarkResult(
            test_name=f"STRESS_TEST_{actual_sessions}sessions",
            mode=self._mode,
            target_pps=pps_per_session * actual_sessions,
            packet_size=packet_size,
            duration_sec=duration_sec,
        )

        all_rtt: List[float] = []
        for r in results:
            if isinstance(r, Exception):
                aggregate.errors.append(str(r))
                continue
            aggregate.packets_sent += r.packets_sent
            aggregate.packets_received += r.packets_received
            if r.rtt_avg_ms > 0:
                all_rtt.extend([r.rtt_avg_ms])  # 聚合各会话的平均 RTT

        aggregate.packets_lost = aggregate.packets_sent - aggregate.packets_received
        aggregate.loss_rate = (
            (aggregate.packets_lost / aggregate.packets_sent * 100.0)
            if aggregate.packets_sent > 0 else 0.0
        )

        if all_rtt:
            aggregate.rtt_avg_ms = statistics.mean(all_rtt)
            aggregate.rtt_max_ms = max(all_rtt)

        aggregate.actual_pps = (
            aggregate.packets_sent / duration_sec if duration_sec > 0 else 0.0
        )

        # ── 清理 ──
        for sess in sessions:
            (tcp_a, tcp_b, _, _, _, s_trans, r_trans) = sess
            s_trans.close()
            r_trans.close()
            await tcp_a.close()
            await tcp_b.close()

        self._print_result(aggregate)
        return aggregate

    async def run_high_freq_small_packet_test(
        self,
        duration_sec: float = 10.0,
    ) -> BenchmarkResult:
        """
        高频小包场景测试

        模拟游戏实时同步: 高频 (240+ PPS) + 小包 (64B)
        """

        logger.info("═══════════════════════════════════════════════════════")
        logger.info("  高频小包场景测试")
        logger.info("  PPS: 240, 包大小: 64B, 时长: %.1fs", duration_sec)
        logger.info("═══════════════════════════════════════════════════════")

        session = await self.setup_relay_session()
        if session is None:
            return BenchmarkResult(
                test_name="HIGH_FREQ_SMALL_PKT",
                mode=self._mode,
                errors=["Failed to establish relay session"],
            )

        (tcp_a, tcp_b,
         sender_proto, receiver_proto,
         relay_token,
         sender_transport, receiver_transport) = session

        try:
            result = await self.run_single_test(
                test_name="HIGH_FREQ_SMALL_PKT_240PPS_64B",
                target_pps=240,
                packet_size=64,
                duration_sec=duration_sec,
                sender_proto=sender_proto,
            )
            self._print_result(result)
            return result
        finally:
            sender_transport.close()
            receiver_transport.close()
            await tcp_a.close()
            await tcp_b.close()

    @staticmethod
    def _print_result(result: BenchmarkResult) -> None:
        """格式化打印测试结果"""
        category = _test_category_label(result.target_pps)
        print()
        print("╔══════════════════════════════════════════════════════════════╗")
        print(f"║  测试: {result.test_name:<53}║")
        print(f"║  模式: {result.mode:<53}║")
        print(f"║  类别: {category:<53}║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  目标 PPS:    {result.target_pps:<46}║")
        print(f"║  实际 PPS:    {result.actual_pps:<46.1f}║")
        print(f"║  包大小:      {result.packet_size:<46}║")
        print(f"║  测试时长:    {result.duration_sec:<46.1f}║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  发送包数:    {result.packets_sent:<46}║")
        print(f"║  接收包数:    {result.packets_received:<46}║")
        print(f"║  丢包数:      {result.packets_lost:<46}║")
        print(f"║  丢包率:      {result.loss_rate:<45.2f}%║")
        if result.target_pps > _RELAY_COMPLIANT_PPS:
            print(f"║  ! 超过 RELAY_RATE_LIMIT ({_RELAY_COMPLIANT_PPS} pkt/s), 丢包可能是服务端限流所致 ║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  RTT 平均:    {result.rtt_avg_ms:<45.3f}ms║")
        print(f"║  RTT 最小:    {result.rtt_min_ms:<45.3f}ms║")
        print(f"║  RTT 最大:    {result.rtt_max_ms:<45.3f}ms║")
        print(f"║  RTT P50:     {result.rtt_p50_ms:<45.3f}ms║")
        print(f"║  RTT P95:     {result.rtt_p95_ms:<45.3f}ms║")
        print(f"║  RTT P99:     {result.rtt_p99_ms:<45.3f}ms║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  Jitter 平均: {result.jitter_ms:<45.3f}ms║")
        print(f"║  Jitter 最大: {result.jitter_max_ms:<45.3f}ms║")
        print("╚══════════════════════════════════════════════════════════════╝")

        if result.errors:
            print("  [!] 错误:")
            for err in result.errors:
                print(f"      - {err}")
        print()

    @staticmethod
    def print_summary(results: List[BenchmarkResult]) -> None:
        """打印所有测试的汇总表"""
        if not results:
            print("无测试结果。")
            return

        print()
        print("═══════════════════════════════════════════════════════════════════════════════════════════════════════════")
        print("                                        基准测试汇总报告")
        print("═══════════════════════════════════════════════════════════════════════════════════════════════════════════")
        print()
        print("  注: RELAY_RATE_LIMIT = 60 pkt/s/session (协议常量)")
        print("  OK  60 PPS      = 协议内合规 Relay 测试")
        print("  [!] 120/240/1000 PPS = 限流压力测试，丢包可能是服务端限流预期行为")
        print("  提示: localhost 模式会绕过 IP/Relay 限流，仅用于本机功能与极限吞吐验证；LAN/VPS 模式才代表真实限流表现。")
        print()
        print(f"{'测试名称':<36} {'类别':<6} {'PPS':>6} {'实际PPS':>9} {'大小':>6} "
              f"{'发送':>7} {'接收':>7} {'丢包%':>7} "
              f"{'RTT均':>9} {'RTT最大':>9} {'Jitter':>9}")
        print("─" * 119)

        for r in results:
            if r.errors:
                print(f"{r.test_name:<36} {'':>6} {'ERROR':>6}  {' '.join(r.errors)}")
                continue
            tag = "OK" if r.target_pps <= _RELAY_COMPLIANT_PPS else "[!]"
            print(
                f"{r.test_name:<36} "
                f"{tag:>6} "
                f"{r.target_pps:>6} "
                f"{r.actual_pps:>9.1f} "
                f"{r.packet_size:>5}B "
                f"{r.packets_sent:>7} "
                f"{r.packets_received:>7} "
                f"{r.loss_rate:>6.2f}% "
                f"{r.rtt_avg_ms:>8.3f}ms"
                f"{r.rtt_max_ms:>8.3f}ms"
                f"{r.jitter_ms:>8.3f}ms"
            )

        print("═" * 119)
        print()


# ══════════════════════════════════════════════════════════════════════════════
# 命令行入口
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="P2P UDP Relay 性能基准测试工具 (遵循 PROTOCOL_LOCK v1.0.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 本地测试 (需要先启动 server.py)
  python benchmark.py --mode localhost

  # 局域网测试
  python benchmark.py --mode lan --host 192.168.1.100

  # 公网 VPS 测试
  python benchmark.py --mode vps --host your-vps-ip.com

  # 自定义 PPS 和包大小
  python benchmark.py --mode localhost --pps 60,120 --sizes 64,256

  # 仅运行压力测试
  python benchmark.py --mode localhost --stress-only

  # 仅运行高频小包测试
  python benchmark.py --mode localhost --highfreq-only

  # 指定测试时长
  python benchmark.py --mode localhost --duration 30
        """,
    )

    parser.add_argument(
        '--mode', '-m',
        choices=['localhost', 'lan', 'vps'],
        default='localhost',
        help='测试模式 (default: localhost)',
    )
    parser.add_argument(
        '--host', '-H',
        default='127.0.0.1',
        help='服务器地址 (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--tcp-port',
        type=int,
        default=TCP_PORT,
        help=f'TCP 端口 (default: {TCP_PORT})',
    )
    parser.add_argument(
        '--udp-port',
        type=int,
        default=UDP_PORT,
        help=f'UDP 端口 (default: {UDP_PORT})',
    )
    parser.add_argument(
        '--pps',
        default='60,120,240,1000',
        help='PPS 列表 (逗号分隔, default: 60,120,240,1000)',
    )
    parser.add_argument(
        '--sizes',
        default='64,256,1024',
        help='包大小列表 (逗号分隔, default: 64,256,1024)',
    )
    parser.add_argument(
        '--duration', '-d',
        type=float,
        default=10.0,
        help='每次测试时长 (秒, default: 10)',
    )
    parser.add_argument(
        '--stress-only',
        action='store_true',
        help='仅运行 CPU 压力测试',
    )
    parser.add_argument(
        '--highfreq-only',
        action='store_true',
        help='仅运行高频小包测试',
    )
    parser.add_argument(
        '--no-stress',
        action='store_true',
        help='跳过 CPU 压力测试',
    )
    parser.add_argument(
        '--no-highfreq',
        action='store_true',
        help='跳过高频小包测试',
    )
    parser.add_argument(
        '--stress-sessions',
        type=int,
        default=5,
        help='压力测试目标并发会话数 (default: 5, 受限于 IP 限流)',
    )
    parser.add_argument(
        '--output', '-o',
        default=None,
        help='输出结果到 JSON 文件',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='详细日志输出',
    )

    return parser.parse_args()


async def async_main() -> None:
    """异步主函数"""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 根据模式设置默认主机
    if args.mode == 'localhost' and args.host == '127.0.0.1':
        pass  # 默认即可
    elif args.mode == 'lan' and args.host == '127.0.0.1':
        logger.warning("LAN 模式但未指定 --host，使用 127.0.0.1")
    elif args.mode == 'vps' and args.host == '127.0.0.1':
        logger.warning("VPS 模式但未指定 --host，使用 127.0.0.1")

    # 解析 PPS 和包大小列表
    pps_list = [int(x.strip()) for x in args.pps.split(',')]
    size_list = [int(x.strip()) for x in args.sizes.split(',')]

    engine = BenchmarkEngine(
        server_host=args.host,
        server_tcp_port=args.tcp_port,
        server_udp_port=args.udp_port,
        mode=args.mode,
    )

    all_results: List[BenchmarkResult] = []

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║       P2P UDP Relay 性能基准测试                            ║")
    print("║       遵循 PROTOCOL_LOCK v1.0.0                            ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  模式:     {args.mode:<49}║")
    tcp_udp_info = f"{args.host} (TCP:{args.tcp_port} UDP:{args.udp_port})"
    print(f"║  服务器:   {tcp_udp_info:<49}║")
    print(f"║  PPS:      {args.pps:<49}║")
    print(f"║  包大小:   {args.sizes:<49}║")
    print(f"║  时长:     {args.duration:<49.1f}║")
    print("╚══════════════════════════════════════════════════════════════╝")
    if args.mode == 'localhost':
        print("[提示] localhost 模式会绕过 IP/Relay 限流，仅用于本机功能与极限吞吐验证；LAN/VPS 模式才代表真实限流表现。")
    print()

    try:
        if args.stress_only:
            # ── 仅压力测试 ──
            stress_result = await engine.run_stress_test(
                concurrent_sessions=args.stress_sessions,
                pps_per_session=60,
                packet_size=64,
                duration_sec=args.duration,
            )
            all_results.append(stress_result)

        elif args.highfreq_only:
            # ── 仅高频小包测试 ──
            hf_result = await engine.run_high_freq_small_packet_test(
                duration_sec=args.duration,
            )
            all_results.append(hf_result)

        else:
            # ── 完整测试套件 ──
            # 1. 标准基准测试
            suite_results = await engine.run_benchmark_suite(
                pps_list=pps_list,
                size_list=size_list,
                duration_sec=args.duration,
            )
            all_results.extend(suite_results)

            # 2. 高频小包测试
            if not args.no_highfreq:
                logger.info("等待 12 秒让限流重置后运行高频小包测试...")
                await asyncio.sleep(12)
                hf_result = await engine.run_high_freq_small_packet_test(
                    duration_sec=args.duration,
                )
                all_results.append(hf_result)

            # 3. CPU 压力测试
            if not args.no_stress:
                logger.info("等待 12 秒让限流重置后运行压力测试...")
                await asyncio.sleep(12)
                stress_result = await engine.run_stress_test(
                    concurrent_sessions=args.stress_sessions,
                    pps_per_session=60,
                    packet_size=64,
                    duration_sec=args.duration,
                )
                all_results.append(stress_result)

        # ── 汇总报告 ──
        BenchmarkEngine.print_summary(all_results)

        # ── 输出 JSON (可选) ──
        if args.output:
            json_data = []
            for r in all_results:
                json_data.append({
                    "test_name":        r.test_name,
                    "mode":             r.mode,
                    "target_pps":       r.target_pps,
                    "actual_pps":       round(r.actual_pps, 1),
                    "packet_size":      r.packet_size,
                    "duration_sec":     r.duration_sec,
                    "packets_sent":     r.packets_sent,
                    "packets_received": r.packets_received,
                    "packets_lost":     r.packets_lost,
                    "loss_rate":        round(r.loss_rate, 2),
                    "rtt_avg_ms":       round(r.rtt_avg_ms, 3),
                    "rtt_max_ms":       round(r.rtt_max_ms, 3),
                    "rtt_min_ms":       round(r.rtt_min_ms, 3),
                    "rtt_p50_ms":       round(r.rtt_p50_ms, 3),
                    "rtt_p95_ms":       round(r.rtt_p95_ms, 3),
                    "rtt_p99_ms":       round(r.rtt_p99_ms, 3),
                    "jitter_ms":        round(r.jitter_ms, 3),
                    "jitter_max_ms":    round(r.jitter_max_ms, 3),
                    "errors":           r.errors,
                })

            output_content = json.dumps(json_data, indent=2, ensure_ascii=False)
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output_content)
            logger.info("结果已保存到: %s", args.output)

    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在停止...")
    except Exception:
        logger.exception("基准测试异常")


def main() -> None:
    """程序入口"""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
