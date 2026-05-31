#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  nat_test.py — P2P UDP Hole Punching / DIRECT 连通性测试工具
  遵循 PROTOCOL_LOCK v1.0.0
═══════════════════════════════════════════════════════════════════════════════

用法:
  # 一方创建房间
  python nat_test.py create --host <SERVER_IP> --name Alice

  # 另一方加入房间
  python nat_test.py join --host <SERVER_IP> --room <ROOM_ID> --name Bob

测试流程:
  1. TCP 连接 server.py，创建/加入房间
  2. UDP 绑定本地端口，发送 REG 注册
  3. 等待 PEER_INFO → 获取 peer 公网 endpoint
  4. 向 peer endpoint 发送 PUNCH 包，同时监听
  5. 收到对方 PUNCH → 回复 PONG
  6. 收到 PONG → 判定 DIRECT 可达
  7. 双方发送 P2P_SUCCESS → DIRECT 状态
  8. 若超时 → 发送 P2P_FAILED → 输出 RELAY fallback

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
import struct
import time
from typing import Dict, List, Optional, Set, Tuple

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
logger: logging.Logger = logging.getLogger("NATTest")


# ══════════════════════════════════════════════════════════════════════════════
# TCP 消息编解码
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
# P2P UDP 打洞协议 — DatagramProtocol
# ══════════════════════════════════════════════════════════════════════════════

class PunchProtocol(asyncio.DatagramProtocol):
    """
    P2P UDP 打洞协议

    职责:
    1. 向服务器发送 REG 注册
    2. 向 peer endpoint 发送 PUNCH 包
    3. 接收 PUNCH → 回复 PONG
    4. 接收 PONG → 标记 DIRECT 成功 + 记录 RTT
    5. 发送 PING → 等待 PONG 测量 RTT
    """

    def __init__(
        self,
        server_addr: Tuple[str, int],
        player_id: str,
        room_id: str,
    ) -> None:
        self._server_addr = server_addr
        self._player_id = player_id
        self._room_id = room_id
        self._transport: Optional[asyncio.DatagramTransport] = None

        # 对端信息 (从 PEER_INFO 获取后设置)
        self._peer_addr: Optional[Tuple[str, int]] = None

        # ── 统计 ──
        self._punch_sent: int = 0
        self._punch_received: int = 0
        self._pong_sent: int = 0
        self._pong_received: int = 0
        self._ping_sent: int = 0
        self._ping_received: int = 0

        # ── 状态 ──
        self._direct_success: bool = False
        self._direct_event: asyncio.Event = asyncio.Event()
        self._rtt_samples: List[float] = []

        # ping 时间戳追踪
        self._ping_timestamps: Dict[int, float] = {}  # seq -> send_time
        self._ping_seq: int = 0

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport
        local = transport.get_extra_info('sockname')
        logger.info("UDP 绑定本地端口: %s:%d", local[0], local[1])

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self._transport = None

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """接收 UDP 包"""
        if len(data) < 5:
            return

        # ── PUNCH 包: 对方在打洞 ──
        if data[:6] == UDP_PREFIX_PUNCH:
            self._punch_received += 1
            # 收到 PUNCH → 立即回复 PONG (证明可达)
            self._send_pong(addr)
            if not self._direct_success:
                logger.info("  ← 收到 PUNCH from %s:%d (第 %d 个)",
                            addr[0], addr[1], self._punch_received)

        # ── PONG 包: 对方回复了我的 PUNCH/PING ──
        elif data[:5] == UDP_PREFIX_PONG:
            self._pong_received += 1
            recv_time = time.perf_counter()

            # 尝试从 PONG 载荷解析 RTT
            payload = data[5:]
            if len(payload) >= 8:
                try:
                    send_time = struct.unpack('!d', payload[:8])[0]
                    rtt_ms = (recv_time - send_time) * 1000.0
                    if 0.0 <= rtt_ms < 30000.0:  # 合理范围
                        self._rtt_samples.append(rtt_ms)
                except struct.error:
                    pass

            if not self._direct_success:
                self._direct_success = True
                self._direct_event.set()
                logger.info("  ★ 收到 PONG from %s:%d — DIRECT 可达!",
                            addr[0], addr[1])

        # ── PING 包: 对方在测延迟 ──
        elif data[:5] == UDP_PREFIX_PING:
            self._ping_received += 1
            # 收到 PING → 回复 PONG (带回原始时间戳)
            payload = data[5:]
            self._send_pong(addr, echo_payload=payload)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP 错误: %s", exc)

    # ── 发送方法 ──

    def send_reg(self) -> None:
        """发送 REG 注册包到服务器"""
        if self._transport is None:
            return
        reg_data = UDP_PREFIX_REG + json.dumps(
            {"player_id": self._player_id, "room_id": self._room_id},
            separators=(',', ':'),
        ).encode('utf-8')
        self._transport.sendto(reg_data, self._server_addr)

    def send_punch(self) -> None:
        """发送 PUNCH 包到对端"""
        if self._transport is None or self._peer_addr is None:
            return
        # PUNCH\n + JSON payload with timestamp
        payload = json.dumps(
            {"player_id": self._player_id, "seq": self._punch_sent},
            separators=(',', ':'),
        ).encode('utf-8')
        packet = UDP_PREFIX_PUNCH + payload
        if len(packet) <= MAX_UDP_PACKET:
            self._transport.sendto(packet, self._peer_addr)
            self._punch_sent += 1

    def _send_pong(
        self,
        addr: Tuple[str, int],
        echo_payload: bytes = b"",
    ) -> None:
        """发送 PONG 回复"""
        if self._transport is None:
            return
        # PONG\n + 回传时间戳 (如果有)
        # 若无 echo_payload, 附加当前时间戳供对方计算 RTT
        if not echo_payload:
            echo_payload = struct.pack('!d', time.perf_counter())
        packet = UDP_PREFIX_PONG + echo_payload
        if len(packet) <= MAX_UDP_PACKET:
            self._transport.sendto(packet, addr)
            self._pong_sent += 1

    def send_ping(self) -> None:
        """发送 PING 延迟测试包到对端"""
        if self._transport is None or self._peer_addr is None:
            return
        ts = struct.pack('!d', time.perf_counter())
        payload = ts + struct.pack('!I', self._ping_seq)
        self._ping_timestamps[self._ping_seq] = time.perf_counter()
        self._ping_seq += 1
        packet = UDP_PREFIX_PING + payload
        if len(packet) <= MAX_UDP_PACKET:
            self._transport.sendto(packet, self._peer_addr)
            self._ping_sent += 1

    def set_peer_addr(self, ip: str, port: int) -> None:
        """设置对端地址"""
        self._peer_addr = (ip, port)

    @property
    def direct_success(self) -> bool:
        return self._direct_success

    @property
    def local_addr(self) -> Optional[Tuple[str, int]]:
        if self._transport is not None:
            return self._transport.get_extra_info('sockname')
        return None


# ══════════════════════════════════════════════════════════════════════════════
# NAT 测试主逻辑
# ══════════════════════════════════════════════════════════════════════════════

class NATTestRunner:
    """NAT 穿透测试运行器"""

    def __init__(
        self,
        server_host: str,
        server_tcp_port: int = TCP_PORT,
        server_udp_port: int = UDP_PORT,
        punch_duration: float = 10.0,
        punch_interval: float = PUNCH_INTERVAL,
        punch_timeout: float = 10.0,
    ) -> None:
        self._server_host = server_host
        self._server_tcp_port = server_tcp_port
        self._server_udp_port = server_udp_port
        self._punch_duration = punch_duration
        self._punch_interval = punch_interval
        self._punch_timeout = punch_timeout

        # ── 运行时状态 ──
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._punch_proto: Optional[PunchProtocol] = None
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._player_id: Optional[str] = None
        self._room_id: Optional[str] = None
        self._shutting_down: bool = False

    # ── TCP 通信 ──

    async def _tcp_connect(self) -> None:
        """建立 TCP 连接"""
        self._reader, self._writer = await asyncio.open_connection(
            self._server_host, self._server_tcp_port,
        )
        logger.info("TCP 已连接: %s:%d", self._server_host, self._server_tcp_port)

    async def _tcp_send(self, msg_type: str, payload: dict) -> None:
        """发送 TCP 消息"""
        if self._writer is None or self._writer.is_closing():
            return
        data = encode_message(msg_type, payload)
        self._writer.write(data)
        await self._writer.drain()

    async def _tcp_recv(self, timeout: float = 30.0) -> Optional[Tuple[str, dict]]:
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

    async def _wait_for_message(
        self,
        expected_type: str,
        timeout: float = 30.0,
    ) -> Optional[dict]:
        """等待指定类型的 TCP 消息"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            result = await self._tcp_recv(timeout=remaining)
            if result is None:
                continue
            msg_type, payload = result
            if msg_type == expected_type:
                return payload
            elif msg_type == MSG_ERROR:
                code = payload.get("code", "?")
                message = payload.get("message", "Unknown")
                logger.error("收到错误: code=%s message=%s", code, message)
                return None
            elif msg_type == MSG_HEARTBEAT:
                # 心跳回复，忽略继续等待
                continue
            else:
                logger.debug("忽略消息: type=%s", msg_type)
        return None

    # ── 心跳 ──

    async def _heartbeat_loop(self) -> None:
        """心跳循环"""
        try:
            while not self._shutting_down:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self._shutting_down:
                    break
                await self._tcp_send(MSG_HEARTBEAT, {
                    "timestamp": int(time.time()),
                })
        except asyncio.CancelledError:
            pass
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    def _start_heartbeat(self) -> None:
        """启动心跳任务"""
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    # ── UDP 初始化 ──

    async def _setup_udp(self) -> None:
        """创建 UDP endpoint 并注册"""
        loop = asyncio.get_running_loop()
        server_addr = (self._server_host, self._server_udp_port)

        self._punch_proto = PunchProtocol(
            server_addr, self._player_id, self._room_id,
        )

        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: self._punch_proto,
            local_addr=('0.0.0.0', 0),
        )

        # 发送 REG 注册 (多次确保到达)
        for _ in range(3):
            self._punch_proto.send_reg()
            await asyncio.sleep(0.1)

        local = self._punch_proto.local_addr
        logger.info("UDP 注册完成: local=%s:%d → server=%s:%d",
                     local[0], local[1], self._server_host, self._server_udp_port)

    # ── 打洞流程 ──

    async def _punch_loop(self) -> bool:
        """
        执行打洞循环

        返回: True = DIRECT 成功, False = 超时失败
        """
        proto = self._punch_proto
        if proto is None or proto._peer_addr is None:
            return False

        peer_ip, peer_port = proto._peer_addr
        logger.info("═══════════════════════════════════════════════════════")
        logger.info("  开始 P2P 打洞")
        logger.info("  对端: %s:%d", peer_ip, peer_port)
        logger.info("  间隔: %.2fs", self._punch_interval)
        logger.info("  超时: %.1fs", self._punch_timeout)
        logger.info("═══════════════════════════════════════════════════════")

        start_time = time.monotonic()
        punch_count = 0

        while not self._shutting_down:
            elapsed = time.monotonic() - start_time
            if elapsed >= self._punch_timeout:
                break

            # 检查是否已成功
            if proto.direct_success:
                break

            # 发送 PUNCH
            proto.send_punch()
            punch_count += 1

            if punch_count <= 5 or punch_count % 10 == 0:
                logger.info("  → PUNCH #%d → %s:%d", punch_count, peer_ip, peer_port)

            # 等待间隔，但支持提前中断
            try:
                await asyncio.wait_for(
                    proto._direct_event.wait(),
                    timeout=self._punch_interval,
                )
                # 成功
                break
            except asyncio.TimeoutError:
                pass

        return proto.direct_success

    async def _post_punch_ping(self, count: int = 5) -> None:
        """打洞成功后发送 PING 测量直连 RTT"""
        proto = self._punch_proto
        if proto is None or not proto.direct_success:
            return

        logger.info("  发送 %d 个 PING 测量直连 RTT...", count)

        for i in range(count):
            if self._shutting_down:
                break
            proto.send_ping()
            await asyncio.sleep(0.3)

        # 等一会让 PONG 返回
        await asyncio.sleep(1.0)

    # ── 清理 ──

    async def _cleanup(self) -> None:
        """资源清理"""
        self._shutting_down = True

        # 1. 取消心跳
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # 2. 发送 LEAVE_ROOM (最佳努力)
        if self._writer is not None and not self._writer.is_closing():
            if self._room_id is not None:
                try:
                    await self._tcp_send(MSG_LEAVE_ROOM, {
                        "room_id": self._room_id,
                    })
                except Exception:
                    pass

        # 3. 关闭 UDP transport
        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None

        # 4. 关闭 TCP
        if self._writer is not None:
            if not self._writer.is_closing():
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except (OSError, ConnectionResetError, ConnectionAbortedError):
                    pass
            self._writer = None
            self._reader = None

        logger.info("资源清理完成")

    # ── 结果报告 ──

    def _print_report(
        self,
        peer_ip: str,
        peer_port: int,
        peer_name: str,
        peer_id: str,
    ) -> None:
        """打印测试报告"""
        proto = self._punch_proto
        if proto is None:
            return

        local = proto.local_addr
        local_str = f"{local[0]}:{local[1]}" if local else "unknown"

        # RTT 统计
        rtt_avg = 0.0
        rtt_min = 0.0
        rtt_max = 0.0
        if proto._rtt_samples:
            rtt_avg = sum(proto._rtt_samples) / len(proto._rtt_samples)
            rtt_min = min(proto._rtt_samples)
            rtt_max = max(proto._rtt_samples)

        print()
        print("╔══════════════════════════════════════════════════════════════╗")
        print("║                    NAT 穿透测试报告                         ║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  本端 player_id:  {self._player_id or 'N/A':<42}║")
        print(f"║  本端 UDP:        {local_str:<42}║")
        print(f"║  房间 ID:         {self._room_id or 'N/A':<42}║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  对端 player_id:  {peer_id:<42}║")
        print(f"║  对端 player_name:{peer_name:<42}║")
        print(f"║  对端公网 EP:     {peer_ip}:{peer_port:<35}║")
        print("╠══════════════════════════════════════════════════════════════╣")
        print(f"║  PUNCH 发送:      {proto._punch_sent:<42}║")
        print(f"║  PUNCH 接收:      {proto._punch_received:<42}║")
        print(f"║  PONG  发送:      {proto._pong_sent:<42}║")
        print(f"║  PONG  接收:      {proto._pong_received:<42}║")
        print(f"║  PING  发送:      {proto._ping_sent:<42}║")
        print(f"║  PING  接收:      {proto._ping_received:<42}║")
        print("╠══════════════════════════════════════════════════════════════╣")

        if proto.direct_success:
            print("║  结果:            ★ DIRECT SUCCESS ★                      ║")
            if rtt_avg > 0:
                print(f"║  直连 RTT 均值:   {rtt_avg:<41.3f}ms║")
                print(f"║  直连 RTT 最小:   {rtt_min:<41.3f}ms║")
                print(f"║  直连 RTT 最大:   {rtt_max:<41.3f}ms║")
                print(f"║  RTT 样本数:      {len(proto._rtt_samples):<42}║")
        else:
            print("║  结果:            ✗ DIRECT FAILED                         ║")
            print(f"║  失败原因:        {'TIMEOUT (punch 超时)':<42}║")

        print("╚══════════════════════════════════════════════════════════════╝")
        print()

    # ══════════════════════════════════════════════════════════════════════
    # 主入口: create 模式
    # ══════════════════════════════════════════════════════════════════════

    async def run_create(self, player_name: str) -> None:
        """create 模式: 创建房间 → 等待对方加入 → 打洞"""
        try:
            # ── 1. TCP 连接 ──
            await self._tcp_connect()

            # ── 2. CREATE_ROOM ──
            await self._tcp_send(MSG_CREATE_ROOM, {
                "player_name": player_name,
            })

            result = await self._tcp_recv(timeout=10.0)
            if result is None:
                logger.error("创建房间超时")
                return

            msg_type, payload = result
            if msg_type == MSG_ERROR:
                logger.error("创建房间失败: code=%s message=%s",
                             payload.get("code"), payload.get("message"))
                return
            if msg_type != MSG_ROOM_CREATED:
                logger.error("意外消息: type=%s", msg_type)
                return

            self._room_id = payload.get("room_id")
            self._player_id = payload.get("player_id")

            print()
            print("╔══════════════════════════════════════════════════════════════╗")
            print("║                      房间已创建                              ║")
            print("╠══════════════════════════════════════════════════════════════╣")
            print(f"║  room_id:    {self._room_id:<48}║")
            print(f"║  player_id:  {self._player_id:<48}║")
            print("╠══════════════════════════════════════════════════════════════╣")
            print("║  等待另一方加入...                                           ║")
            print("║  请在另一端运行:                                             ║")
            print(f"║  python nat_test.py join --host {self._server_host}"
                  f" --room {self._room_id} --name <NAME>")
            print("╚══════════════════════════════════════════════════════════════╝")
            print()

            # ── 3. 启动心跳 ──
            self._start_heartbeat()

            # ── 4. UDP 注册 ──
            await self._setup_udp()

            # ── 5. 等待 PEER_INFO (对方加入 + 双方 UDP 注册) ──
            await self._run_punch_phase()

        except KeyboardInterrupt:
            logger.info("用户中断 (Ctrl+C)")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.error("TCP 连接断开")
        except Exception as e:
            logger.exception("运行异常: %s", e)
        finally:
            await self._cleanup()

    # ══════════════════════════════════════════════════════════════════════
    # 主入口: join 模式
    # ══════════════════════════════════════════════════════════════════════

    async def run_join(self, room_id: str, player_name: str) -> None:
        """join 模式: 加入房间 → 打洞"""
        try:
            # ── 1. TCP 连接 ──
            await self._tcp_connect()

            # ── 2. JOIN_ROOM ──
            await self._tcp_send(MSG_JOIN_ROOM, {
                "room_id": room_id,
                "player_name": player_name,
            })

            result = await self._tcp_recv(timeout=10.0)
            if result is None:
                logger.error("加入房间超时")
                return

            msg_type, payload = result
            if msg_type == MSG_ERROR:
                logger.error("加入房间失败: code=%s message=%s",
                             payload.get("code"), payload.get("message"))
                return
            if msg_type != MSG_ROOM_JOINED:
                logger.error("意外消息: type=%s", msg_type)
                return

            self._room_id = payload.get("room_id", room_id)
            self._player_id = payload.get("player_id")

            print()
            print("╔══════════════════════════════════════════════════════════════╗")
            print("║                      已加入房间                              ║")
            print("╠══════════════════════════════════════════════════════════════╣")
            print(f"║  room_id:    {self._room_id:<48}║")
            print(f"║  player_id:  {self._player_id:<48}║")
            print("╚══════════════════════════════════════════════════════════════╝")
            print()

            # ── 3. 启动心跳 ──
            self._start_heartbeat()

            # ── 4. UDP 注册 ──
            await self._setup_udp()

            # ── 5. 打洞阶段 ──
            await self._run_punch_phase()

        except KeyboardInterrupt:
            logger.info("用户中断 (Ctrl+C)")
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            logger.error("TCP 连接断开")
        except Exception as e:
            logger.exception("运行异常: %s", e)
        finally:
            await self._cleanup()

    # ══════════════════════════════════════════════════════════════════════
    # 打洞核心阶段 (create/join 共享)
    # ══════════════════════════════════════════════════════════════════════

    async def _run_punch_phase(self) -> None:
        """等待 PEER_INFO → 打洞 → 报告结果"""

        logger.info("等待 PEER_INFO ... (等待对方 UDP 注册完成)")

        peer_info = await self._wait_for_message(
            MSG_PEER_INFO, timeout=120.0,
        )
        if peer_info is None:
            logger.error("等待 PEER_INFO 超时 (120s)")
            return

        peer_id = peer_info.get("peer_id", "")
        peer_name = peer_info.get("peer_name", "")
        peer_ip = peer_info.get("peer_ip", "")
        peer_port = peer_info.get("peer_port", 0)

        if not peer_ip or not peer_port:
            logger.error("PEER_INFO 信息不完整: ip=%s port=%s", peer_ip, peer_port)
            return

        logger.info("收到 PEER_INFO:")
        logger.info("  peer_id:   %s", peer_id)
        logger.info("  peer_name: %s", peer_name)
        logger.info("  peer_ip:   %s", peer_ip)
        logger.info("  peer_port: %d", peer_port)

        # 设置对端地址
        self._punch_proto.set_peer_addr(peer_ip, peer_port)

        # ── 执行打洞 ──
        success = await self._punch_loop()

        if success:
            # ── DIRECT 成功 ──
            logger.info("P2P DIRECT 成功!")

            # 发送 P2P_SUCCESS
            await self._tcp_send(MSG_P2P_SUCCESS, {
                "room_id": self._room_id,
            })
            logger.info("已发送 P2P_SUCCESS")

            # 发送 PING 测量 RTT
            await self._post_punch_ping(count=5)

            # 打印报告
            self._print_report(peer_ip, peer_port, peer_name, peer_id)

            # 保持连接等待退出
            logger.info("DIRECT 连接建立成功。保持连接 10 秒后退出 (Ctrl+C 提前退出)...")
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                pass

        else:
            # ── DIRECT 失败 → RELAY fallback ──
            logger.info("P2P DIRECT 失败 (超时)")

            # 发送 P2P_FAILED
            await self._tcp_send(MSG_P2P_FAILED, {
                "room_id": self._room_id,
                "reason": "TIMEOUT",
            })
            logger.info("已发送 P2P_FAILED, reason=TIMEOUT")

            # 等待 RELAY_ENABLED
            relay_payload = await self._wait_for_message(
                MSG_RELAY_ENABLED, timeout=15.0,
            )
            if relay_payload is not None:
                relay_token = relay_payload.get("relay_token", "N/A")
                relay_ip = relay_payload.get("relay_ip", "N/A")
                relay_port = relay_payload.get("relay_port", "N/A")
                logger.info("RELAY 回退可用:")
                logger.info("  relay_token: %s", relay_token)
                logger.info("  relay_ip:    %s", relay_ip)
                logger.info("  relay_port:  %s", relay_port)
            else:
                logger.warning("未收到 RELAY_ENABLED (可能对方已先发送 P2P_FAILED)")
                # 尝试等待更短时间，也许已经切换过了
                relay_payload = await self._wait_for_message(
                    MSG_RELAY_ENABLED, timeout=5.0,
                )
                if relay_payload is not None:
                    relay_token = relay_payload.get("relay_token", "N/A")
                    logger.info("  (延迟收到) relay_token: %s", relay_token)

            # 打印报告
            self._print_report(peer_ip, peer_port, peer_name, peer_id)


# ══════════════════════════════════════════════════════════════════════════════
# 命令行入口
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="P2P UDP Hole Punching / NAT 穿透测试工具 (遵循 PROTOCOL_LOCK v1.0.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 一方创建房间
  python nat_test.py create --host 1.2.3.4 --name Alice

  # 另一方加入房间
  python nat_test.py join --host 1.2.3.4 --room A1B2C3 --name Bob

  # 自定义打洞参数
  python nat_test.py create --host 1.2.3.4 --name Alice --duration 15 --interval 0.1 --timeout 15

  # localhost 本地测试
  python nat_test.py create --host 127.0.0.1 --name Alice
  python nat_test.py join --host 127.0.0.1 --room <ROOM_ID> --name Bob
        """,
    )

    subparsers = parser.add_subparsers(dest='command', help='操作模式')
    subparsers.required = True

    # ── create 子命令 ──
    create_parser = subparsers.add_parser('create', help='创建房间并等待对方加入')
    create_parser.add_argument(
        '--host', '-H', required=True,
        help='服务器地址',
    )
    create_parser.add_argument(
        '--name', '-n', required=True,
        help='玩家名称',
    )
    create_parser.add_argument(
        '--tcp-port', type=int, default=TCP_PORT,
        help=f'TCP 端口 (default: {TCP_PORT})',
    )
    create_parser.add_argument(
        '--udp-port', type=int, default=UDP_PORT,
        help=f'UDP 端口 (default: {UDP_PORT})',
    )
    create_parser.add_argument(
        '--duration', type=float, default=10.0,
        help='打洞总时长上限 (default: 10s)',
    )
    create_parser.add_argument(
        '--interval', type=float, default=PUNCH_INTERVAL,
        help=f'PUNCH 间隔 (default: {PUNCH_INTERVAL}s)',
    )
    create_parser.add_argument(
        '--timeout', type=float, default=10.0,
        help='打洞超时 (default: 10s)',
    )

    # ── join 子命令 ──
    join_parser = subparsers.add_parser('join', help='加入已有房间')
    join_parser.add_argument(
        '--host', '-H', required=True,
        help='服务器地址',
    )
    join_parser.add_argument(
        '--room', '-r', required=True,
        help='房间 ID (6 位大写字母+数字)',
    )
    join_parser.add_argument(
        '--name', '-n', required=True,
        help='玩家名称',
    )
    join_parser.add_argument(
        '--tcp-port', type=int, default=TCP_PORT,
        help=f'TCP 端口 (default: {TCP_PORT})',
    )
    join_parser.add_argument(
        '--udp-port', type=int, default=UDP_PORT,
        help=f'UDP 端口 (default: {UDP_PORT})',
    )
    join_parser.add_argument(
        '--duration', type=float, default=10.0,
        help='打洞总时长上限 (default: 10s)',
    )
    join_parser.add_argument(
        '--interval', type=float, default=PUNCH_INTERVAL,
        help=f'PUNCH 间隔 (default: {PUNCH_INTERVAL}s)',
    )
    join_parser.add_argument(
        '--timeout', type=float, default=10.0,
        help='打洞超时 (default: 10s)',
    )

    return parser.parse_args()


async def _async_main() -> None:
    """异步主入口"""
    args = parse_args()

    runner = NATTestRunner(
        server_host=args.host,
        server_tcp_port=args.tcp_port,
        server_udp_port=args.udp_port,
        punch_duration=args.duration,
        punch_interval=args.interval,
        punch_timeout=args.timeout,
    )

    if args.command == 'create':
        await runner.run_create(player_name=args.name)
    elif args.command == 'join':
        await runner.run_join(room_id=args.room, player_name=args.name)


def main() -> None:
    """程序入口"""
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)")
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
