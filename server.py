#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════════════
  P2P UDP Hole Punching + UDP Relay 中转服务器
  遵循 PROTOCOL_LOCK v1.0.0
═══════════════════════════════════════════════════════════════════════════════

架构概览:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                          Server 主进程                              │
  │                                                                     │
  │  ┌──────────────────┐    ┌──────────────────┐                      │
  │  │  TCP 信令服务器    │    │  UDP Relay 服务器  │                      │
  │  │  Port 9000        │    │  Port 9001         │                      │
  │  │                    │    │                     │                      │
  │  │  - CREATE_ROOM    │    │  - REG 注册         │                      │
  │  │  - JOIN_ROOM      │    │  - RELAY 中转       │                      │
  │  │  - HEARTBEAT      │    │                     │                      │
  │  │  - LEAVE_ROOM     │    └──────────────────┘                      │
  │  │  - P2P_SUCCESS    │                                               │
  │  │  - P2P_FAILED     │    ┌──────────────────┐                      │
  │  └──────────────────┘    │  RoomManager      │                      │
  │                           │  - 房间生命周期     │                      │
  │                           │  - 状态机管理       │                      │
  │                           │  - 超时检测         │                      │
  │                           └──────────────────┘                      │
  └─────────────────────────────────────────────────────────────────────┘

状态机:
  WAITING → READY → PUNCHING → DIRECT
                             → RELAY
  任意活跃状态 → CLOSED

依赖: 仅 Python 标准库 (asyncio, json, dataclasses, etc.)
兼容: Windows (WindowsSelectorEventLoopPolicy) + Linux
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
# Windows 事件循环策略 — 必须在所有 import 之前设置
# ══════════════════════════════════════════════════════════════════════════════
import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import json
import logging
import os
import re
import secrets
import signal
import time
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Dict, Optional, Set, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# 协议常量 — 冻结，禁止修改任何值
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
# 消息类型 — 冻结，禁止新增/重命名/别名
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

# 合法消息类型集合（用于快速校验）
_VALID_MSG_TYPES: frozenset = frozenset({
    MSG_CREATE_ROOM, MSG_ROOM_CREATED, MSG_JOIN_ROOM, MSG_ROOM_JOINED,
    MSG_PEER_INFO, MSG_HEARTBEAT, MSG_LEAVE_ROOM,
    MSG_P2P_SUCCESS, MSG_P2P_FAILED, MSG_RELAY_ENABLED, MSG_ERROR,
})

# ══════════════════════════════════════════════════════════════════════════════
# 错误码 — 冻结，禁止修改数值/含义
# ══════════════════════════════════════════════════════════════════════════════

@unique
class ErrorCode(IntEnum):
    ROOM_NOT_FOUND      = 1001
    ROOM_FULL           = 1002
    HEARTBEAT_TIMEOUT   = 1003
    RATE_LIMIT          = 1004
    INVALID_MESSAGE     = 1005
    SERVER_FULL         = 1006
    RELAY_UNAVAILABLE   = 1007
    INVALID_TOKEN       = 1008
    PLAYER_NAME_INVALID = 1009
    DUPLICATE_ROOM      = 1010

# 错误码到人类可读消息的映射
_ERROR_MESSAGES: Dict[int, str] = {
    1001: "Room not found",
    1002: "Room full",
    1003: "Heartbeat timeout",
    1004: "Rate limit exceeded",
    1005: "Invalid message",
    1006: "Server full",
    1007: "Relay unavailable",
    1008: "Invalid token",
    1009: "Player name invalid",
    1010: "Duplicate room",
}

# ══════════════════════════════════════════════════════════════════════════════
# 房间状态 — 冻结，禁止新增/重命名
# ══════════════════════════════════════════════════════════════════════════════

STATE_WAITING:   str = "WAITING"
STATE_READY:     str = "READY"
STATE_PUNCHING:  str = "PUNCHING"
STATE_DIRECT:    str = "DIRECT"
STATE_RELAY:     str = "RELAY"
STATE_CLOSED:    str = "CLOSED"

# ══════════════════════════════════════════════════════════════════════════════
# UDP 前缀 — 冻结，禁止修改/新增
# ══════════════════════════════════════════════════════════════════════════════

UDP_PREFIX_REG:   bytes = b"REG\n"       # 4B
UDP_PREFIX_PUNCH: bytes = b"PUNCH\n"     # 6B
UDP_PREFIX_PING:  bytes = b"PING\n"      # 5B
UDP_PREFIX_PONG:  bytes = b"PONG\n"      # 5B
UDP_PREFIX_DATA:  bytes = b"DATA\n"      # 5B
UDP_PREFIX_RELAY: bytes = b"RELAY\n"     # 6B

# ══════════════════════════════════════════════════════════════════════════════
# ID 格式验证
# ══════════════════════════════════════════════════════════════════════════════

_RE_PLAYER_ID:   re.Pattern = re.compile(r'^p_[0-9a-f]{12}$')
_RE_ROOM_ID:     re.Pattern = re.compile(r'^[A-Z0-9]{6}$')
_RE_RELAY_TOKEN: re.Pattern = re.compile(r'^rtk_[0-9a-f]{16}$')
_RE_PLAYER_NAME: re.Pattern = re.compile(r'^.{1,32}$', re.DOTALL)

# ══════════════════════════════════════════════════════════════════════════════
# 服务端限制常量
# ══════════════════════════════════════════════════════════════════════════════

MAX_ROOMS:              int = 500           # 服务器最大房间数
MAX_CONNECTIONS_PER_IP: int = 100           # 单 IP 最大并发 TCP 连接
MAX_ROOMS_PER_IP:       int = 3             # 单 IP 最大活跃房间数
TCP_RATE_LIMIT:         int = 30            # 条/秒/连接
CREATE_ROOM_RATE:       int = 3             # 次/10秒/IP
JOIN_ROOM_RATE:         int = 6             # 次/10秒/IP
UDP_RATE_LIMIT:         int = 2000          # 包/秒/IP
RELAY_RATE_LIMIT:       int = 2000          # 包/秒/会话
INVALID_PKT_THRESHOLD:  int = 50            # 包/分钟/IP
IP_BAN_DURATION_UDP:    int = 300           # 秒
IP_BAN_DURATION_TOKEN:  int = 600           # 秒
IP_BAN_DURATION_CONN:   int = 900           # 秒
MAX_RELAY_PER_IP:       int = 2             # 单 IP Relay 会话数
INVALID_TOKEN_LIMIT:    int = 10            # 次/分钟/IP
WAITING_ROOM_TIMEOUT:   int = 300           # 5 分钟, WAITING 房间超时 (服务端策略)
# ── 服务端本地超时配置 (非协议常量，本地预览/验证阶段可调整) ──
_RELAY_IDLE_TIMEOUT_SECONDS: int = 1800  # Relay 空闲超时 (预览阶段, 协议规范值为 30)
# Server-local preview capacity. Frozen protocol constants above remain unchanged.
_RELAY_MAX_SESSIONS_PER_IP_DEFAULT: int = MAX_RELAY_PER_IP
_RELAY_MAX_SESSIONS_DEFAULT: int = MAX_ROOMS
_RELAY_BYTES_PER_SECOND_DEFAULT: int = (RELAY_BANDWIDTH_LIMIT * 1024) // 8
_RELAY_BURST_BYTES_DEFAULT: int = 512 * 1024

# ══════════════════════════════════════════════════════════════════════════════
# 日志配置 — 限流日志
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger: logging.Logger = logging.getLogger("P2PServer")


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive integer environment variable, falling back safely."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw_value, default)
        return default
    if value <= 0:
        logger.warning("Invalid %s=%r; using default %d", name, raw_value, default)
        return default
    return value


def _positive_int_arg(value: str) -> int:
    """argparse type for positive integer capacity values."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


class RateLimitedLogger:
    """日志限流器 — 避免高频场景下日志洪泛"""

    __slots__ = ('_logger', '_min_interval', '_last_log')

    def __init__(self, base_logger: logging.Logger, min_interval: float = 1.0) -> None:
        self._logger = base_logger
        self._min_interval = min_interval
        self._last_log: Dict[str, float] = {}

    def _should_log(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_log.get(key, 0.0)
        if now - last >= self._min_interval:
            self._last_log[key] = now
            return True
        return False

    def info(self, key: str, msg: str, *args: object) -> None:
        if self._should_log(key):
            self._logger.info(msg, *args)

    def warning(self, key: str, msg: str, *args: object) -> None:
        if self._should_log(key):
            self._logger.warning(msg, *args)

    def debug(self, key: str, msg: str, *args: object) -> None:
        if self._should_log(key):
            self._logger.debug(msg, *args)


rl_logger = RateLimitedLogger(logger, min_interval=2.0)

# ══════════════════════════════════════════════════════════════════════════════
# ID 生成工具
# ══════════════════════════════════════════════════════════════════════════════


def generate_player_id() -> str:
    """生成 player_id: p_<hex12>"""
    return f"p_{secrets.token_hex(6)}"


def generate_room_id() -> str:
    """生成 room_id: [A-Z0-9]{6} (excluding O/0/I/1 for readability)"""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return ''.join(secrets.choice(chars) for _ in range(ROOM_ID_LENGTH))


def generate_relay_token() -> str:
    """生成 relay_token: rtk_<hex16>"""
    return f"rtk_{secrets.token_hex(8)}"


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Player:
    """玩家数据"""
    player_id:    str                                    # p_<hex12>
    player_name:  str                                    # 1-32 字符
    room_id:      Optional[str]            = None        # 所在房间
    tcp_writer:   Optional[asyncio.StreamWriter] = None  # TCP 写入端
    peer_addr:    Optional[str]            = None        # TCP 对端地址 (ip:port)
    tcp_ip:       Optional[str]            = None        # TCP 连接 IP
    udp_addr:     Optional[Tuple[str, int]] = None       # UDP 公网地址 (ip, port)
    last_heartbeat: float                  = 0.0         # 最后心跳时间 (monotonic)


@dataclass
class RelaySession:
    """Relay 中转会话"""
    relay_token:  str                                    # rtk_<hex16>
    room_id:      str                                    # 关联房间
    player_a_id:  str                                    # 玩家 A 的 ID
    player_b_id:  str                                    # 玩家 B 的 ID
    created_at:   float          = 0.0                   # 创建时间 (monotonic)
    last_activity: float         = 0.0                   # 最后活动时间 (monotonic)
    bytes_a:      int            = 0                     # 玩家 A 累计发送字节
    bytes_b:      int            = 0                     # 玩家 B 累计发送字节
    bandwidth_window_start: float = 0.0                  # 带宽窗口开始时间
    bandwidth_bytes:    int      = 0                     # 当前窗口字节数
    bandwidth_tokens:   float    = 0.0                   # Server-local token bucket bytes
    bandwidth_last_refill: float = 0.0                   # Token bucket refill time
    pkt_window_start:   float    = 0.0                   # 包速率窗口开始时间
    pkt_count:          int      = 0                     # 当前窗口包数
    dropped_packets:    int      = 0                     # Server-side relay drop count
    dropped_bytes:      int      = 0                     # Server-side relay dropped bytes
    reserved_ips:       Tuple[str, ...] = field(default_factory=tuple)

    # ── Server-local relay-path diagnostics (v0.2 live-debug) ──
    relay_packets_received:    int = 0
    relay_bytes_received:      int = 0
    relay_packets_forwarded:   int = 0
    relay_bytes_forwarded:     int = 0
    relay_drop_missing_fields: int = 0
    relay_drop_invalid_token:  int = 0
    relay_drop_player_mismatch:    int = 0
    relay_drop_no_target_udp_addr: int = 0
    relay_drop_no_udp_transport:   int = 0
    relay_drop_rate_limit_exceeded:    int = 0
    relay_drop_bandwidth_exceeded:     int = 0
    relay_forward_exceptions:          int = 0


@dataclass
class Room:
    """房间数据"""
    room_id:       str                                   # [A-Z0-9]{6}
    state:         str             = STATE_WAITING       # 状态机当前状态
    creator_id:    Optional[str]   = None                # 创建者 player_id
    joiner_id:     Optional[str]   = None                # 加入者 player_id
    created_at:    float           = 0.0                 # 创建时间 (monotonic)
    relay_session: Optional[RelaySession] = None         # Relay 会话
    punch_timeout_handle: Optional[asyncio.TimerHandle] = None  # 打洞超时句柄
    p2p_success_players: Set[str]  = field(default_factory=set) # 已报告 P2P_SUCCESS 的玩家
    tasks:         list            = field(default_factory=list) # 关联的异步任务


# ══════════════════════════════════════════════════════════════════════════════
# 限流器
# ══════════════════════════════════════════════════════════════════════════════

class TokenBucket:
    """令牌桶限流器"""

    __slots__ = ('_rate', '_max_tokens', '_tokens', '_last_refill')

    def __init__(self, rate: float, max_tokens: float) -> None:
        self._rate = rate
        self._max_tokens = max_tokens
        self._tokens = max_tokens
        self._last_refill = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
        self._last_refill = now
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class SlidingWindowCounter:
    """滑动窗口计数器"""

    __slots__ = ('_window', '_max_count', '_timestamps')

    def __init__(self, window: float, max_count: int) -> None:
        self._window = window
        self._max_count = max_count
        self._timestamps: list[float] = []

    def check_and_add(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        # 清除过期的时间戳
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max_count:
            return False
        self._timestamps.append(now)
        return True


# ══════════════════════════════════════════════════════════════════════════════
# IP 限流管理器
# ══════════════════════════════════════════════════════════════════════════════

class IPRateLimiter:
    """每 IP 限流管理"""

    def _is_whitelist(self, ip: str) -> bool:
        """检查 IP 是否在免限流白名单中 (localhost/127.x.x.x/::1/::ffff:127.x.x.x)"""
        # 快速路径: 最常见的 localhost 表示
        if ip == "127.0.0.1" or ip.startswith("127.") or ip == "::1":
            return True
        # IPv6-mapped IPv4 处理: ::ffff:127.0.0.1 → 提取 IPv4 部分
        if ip.startswith("::ffff:"):
            mapped = ip[7:]  # 去掉 "::ffff:" 前缀
            if mapped.startswith("127."):
                return True
        return False

    def __init__(
        self,
        relay_max_sessions_per_ip: int = _RELAY_MAX_SESSIONS_PER_IP_DEFAULT,
    ) -> None:
        # TCP 限流: 每连接消息速率
        self._tcp_buckets: Dict[str, TokenBucket] = {}
        # CREATE_ROOM 限流: 每 IP 1次/10秒
        self._create_room_windows: Dict[str, SlidingWindowCounter] = {}
        # JOIN_ROOM 限流: 每 IP 3次/10秒
        self._join_room_windows: Dict[str, SlidingWindowCounter] = {}
        # UDP 限流: 每 IP 100包/秒
        self._udp_buckets: Dict[str, TokenBucket] = {}
        # 无效包计数器: 每 IP 50包/分钟
        self._invalid_pkt_windows: Dict[str, SlidingWindowCounter] = {}
        # 无效 Token 计数器: 每 IP 10次/分钟
        self._invalid_token_windows: Dict[str, SlidingWindowCounter] = {}
        # IP 黑名单: ip -> 解禁时间 (monotonic)
        self._blacklist: Dict[str, float] = {}
        # 每 IP 活跃连接数
        self._connections_per_ip: Dict[str, int] = {}
        # 每 IP 活跃房间数
        self._rooms_per_ip: Dict[str, int] = {}
        # 每 IP Relay 会话数
        self._relay_per_ip: Dict[str, int] = {}
        self._relay_max_sessions_per_ip = relay_max_sessions_per_ip

    def is_banned(self, ip: str) -> bool:
        """检查 IP 是否被封禁"""
        if self._is_whitelist(ip):
            return False
        ban_until = self._blacklist.get(ip)
        if ban_until is None:
            return False
        if time.monotonic() >= ban_until:
            del self._blacklist[ip]
            return False
        return True

    def ban_ip(self, ip: str, duration: int) -> None:
        """封禁 IP"""
        if self._is_whitelist(ip):
            return
        self._blacklist[ip] = time.monotonic() + duration
        logger.warning("IP %s banned for %d seconds", ip, duration)

    # ── TCP 限流 ──

    def check_tcp_rate(self, conn_key: str) -> bool:
        """检查 TCP 消息速率 (每连接)"""
        try:
            ip = conn_key.split(":")[0]
            if self._is_whitelist(ip):
                return True
        except Exception:
            pass
        bucket = self._tcp_buckets.get(conn_key)
        if bucket is None:
            bucket = TokenBucket(TCP_RATE_LIMIT, TCP_RATE_LIMIT)
            self._tcp_buckets[conn_key] = bucket
        return bucket.consume()

    def remove_tcp_bucket(self, conn_key: str) -> None:
        self._tcp_buckets.pop(conn_key, None)

    def check_create_room_rate(self, ip: str) -> bool:
        if self._is_whitelist(ip):
            return True
        window = self._create_room_windows.get(ip)
        if window is None:
            window = SlidingWindowCounter(10.0, CREATE_ROOM_RATE)
            self._create_room_windows[ip] = window
        return window.check_and_add()

    def check_join_room_rate(self, ip: str) -> bool:
        if self._is_whitelist(ip):
            return True
        window = self._join_room_windows.get(ip)
        if window is None:
            window = SlidingWindowCounter(10.0, JOIN_ROOM_RATE)
            self._join_room_windows[ip] = window
        return window.check_and_add()

    # ── UDP 限流 ──

    def check_udp_rate(self, ip: str) -> bool:
        if self._is_whitelist(ip):
            return True
        bucket = self._udp_buckets.get(ip)
        if bucket is None:
            bucket = TokenBucket(UDP_RATE_LIMIT, UDP_RATE_LIMIT)
            self._udp_buckets[ip] = bucket
        return bucket.consume()

    def record_invalid_packet(self, ip: str) -> bool:
        """记录无效包，返回 True 表示超过阈值应封禁"""
        if self._is_whitelist(ip):
            return False
        window = self._invalid_pkt_windows.get(ip)
        if window is None:
            window = SlidingWindowCounter(60.0, INVALID_PKT_THRESHOLD)
            self._invalid_pkt_windows[ip] = window
        return not window.check_and_add()

    def record_invalid_token(self, ip: str) -> bool:
        """记录无效 Token 请求，返回 True 表示超过阈值应封禁"""
        if self._is_whitelist(ip):
            return False
        window = self._invalid_token_windows.get(ip)
        if window is None:
            window = SlidingWindowCounter(60.0, INVALID_TOKEN_LIMIT)
            self._invalid_token_windows[ip] = window
        return not window.check_and_add()

    # ── 连接计数 ──

    def add_connection(self, ip: str) -> bool:
        """添加连接，返回 False 表示已达上限"""
        if self._is_whitelist(ip):
            return True
        count = self._connections_per_ip.get(ip, 0)
        if count >= MAX_CONNECTIONS_PER_IP:
            return False
        self._connections_per_ip[ip] = count + 1
        return True

    def remove_connection(self, ip: str) -> None:
        if self._is_whitelist(ip):
            return
        count = self._connections_per_ip.get(ip, 0)
        if count <= 1:
            self._connections_per_ip.pop(ip, None)
        else:
            self._connections_per_ip[ip] = count - 1

    # ── 房间计数 ──

    def add_room(self, ip: str) -> bool:
        if self._is_whitelist(ip):
            return True
        count = self._rooms_per_ip.get(ip, 0)
        if count >= MAX_ROOMS_PER_IP:
            return False
        self._rooms_per_ip[ip] = count + 1
        return True

    def remove_room(self, ip: str) -> None:
        if self._is_whitelist(ip):
            return
        count = self._rooms_per_ip.get(ip, 0)
        if count <= 1:
            self._rooms_per_ip.pop(ip, None)
        else:
            self._rooms_per_ip[ip] = count - 1

    # ── Relay 计数 ──

    def add_relay(self, ip: str) -> bool:
        if self._is_whitelist(ip):
            return True
        count = self._relay_per_ip.get(ip, 0)
        if count >= self._relay_max_sessions_per_ip:
            return False
        self._relay_per_ip[ip] = count + 1
        return True

    def remove_relay(self, ip: str) -> None:
        if self._is_whitelist(ip):
            return
        count = self._relay_per_ip.get(ip, 0)
        if count <= 1:
            self._relay_per_ip.pop(ip, None)
        else:
            self._relay_per_ip[ip] = count - 1

    def cleanup_expired_bans(self) -> None:
        """清理已过期的封禁"""
        now = time.monotonic()
        expired = [ip for ip, until in self._blacklist.items() if now >= until]
        for ip in expired:
            del self._blacklist[ip]


# ══════════════════════════════════════════════════════════════════════════════
# 消息编码/解码工具
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
# P2P Server — 核心服务器类
# ══════════════════════════════════════════════════════════════════════════════

class P2PServer:
    """
    P2P UDP Hole Punching + Relay 中转服务器

    职责:
    1. TCP 信令服务: 房间管理、打洞协调、心跳
    2. UDP 服务: NAT 地址注册、Relay 数据中转
    3. 房间生命周期: 状态机维护、超时管理、资源清理
    """

    def __init__(
        self,
        advertise_host: Optional[str] = None,
        relay_max_sessions_per_ip: Optional[int] = None,
        relay_max_sessions: Optional[int] = None,
        relay_bytes_per_second: Optional[int] = None,
        relay_burst_bytes: Optional[int] = None,
    ) -> None:
        # ── 核心数据结构 ──
        self._rooms:   Dict[str, Room]   = {}    # room_id -> Room
        self._players: Dict[str, Player] = {}    # player_id -> Player
        # 反向索引: TCP 连接 key -> player_id
        self._conn_to_player: Dict[str, str] = {}
        # 反向索引: UDP 地址 -> player_id
        self._udp_to_player: Dict[Tuple[str, int], str] = {}
        # Relay Token -> RelaySession
        self._relay_sessions: Dict[str, RelaySession] = {}

        # ── Server-local Relay capacity (CLI > environment > preview defaults) ──
        self._relay_max_sessions_per_ip = (
            relay_max_sessions_per_ip
            if relay_max_sessions_per_ip is not None
            else _env_positive_int(
                'S2PASS_RELAY_MAX_SESSIONS_PER_IP',
                _RELAY_MAX_SESSIONS_PER_IP_DEFAULT,
            )
        )
        self._relay_max_sessions = (
            relay_max_sessions
            if relay_max_sessions is not None
            else _env_positive_int(
                'S2PASS_RELAY_MAX_SESSIONS',
                _RELAY_MAX_SESSIONS_DEFAULT,
            )
        )
        self._relay_bytes_per_second = (
            relay_bytes_per_second
            if relay_bytes_per_second is not None
            else _env_positive_int(
                'S2PASS_RELAY_BYTES_PER_SECOND',
                _RELAY_BYTES_PER_SECOND_DEFAULT,
            )
        )
        self._relay_burst_bytes = (
            relay_burst_bytes
            if relay_burst_bytes is not None
            else _env_positive_int(
                'S2PASS_RELAY_BURST_BYTES',
                _RELAY_BURST_BYTES_DEFAULT,
            )
        )
        for capacity_name, capacity_value in (
            ('relay_max_sessions_per_ip', self._relay_max_sessions_per_ip),
            ('relay_max_sessions', self._relay_max_sessions),
            ('relay_bytes_per_second', self._relay_bytes_per_second),
            ('relay_burst_bytes', self._relay_burst_bytes),
        ):
            if capacity_value <= 0:
                raise ValueError(f"{capacity_name} must be positive")

        # ── 限流 ──
        self._rate_limiter = IPRateLimiter(self._relay_max_sessions_per_ip)

        # ── 服务器基础设施 ──
        self._tcp_server: Optional[asyncio.AbstractServer] = None
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._udp_protocol: Optional['UDPRelayProtocol'] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # ── 后台任务 ──
        self._background_tasks: list[asyncio.Task] = []

        # ── 服务器地址配置 ──
        # 优先级: 构造函数参数 (--advertise-host) > 环境变量 > 默认值
        self._server_ip: str = os.environ.get('SERVER_IP', '0.0.0.0')
        if advertise_host:
            self._public_ip: str = advertise_host
        else:
            self._public_ip: str = os.environ.get(
                'S2PASS_ADVERTISE_HOST',
                os.environ.get('PUBLIC_IP', '127.0.0.1'),
            )

        # ── 关闭标志 ──
        self._shutting_down: bool = False

    # ══════════════════════════════════════════════════════════════════════
    # 服务器生命周期
    # ══════════════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """启动 TCP + UDP 服务"""
        self._loop = asyncio.get_running_loop()

        # ── 启动 TCP 信令服务 ──
        self._tcp_server = await asyncio.start_server(
            self._handle_tcp_connection,
            host=self._server_ip,
            port=TCP_PORT,
            reuse_address=True,
        )
        logger.info("TCP 信令服务启动: %s:%d", self._server_ip, TCP_PORT)

        # ── 启动 UDP Relay 服务 ──
        # 手动创建 socket 并设置 SO_REUSEADDR (兼容 Python 3.11+ 和 Windows)
        import socket as _socket
        udp_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        udp_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        udp_sock.bind((self._server_ip, UDP_PORT))
        udp_sock.setblocking(False)

        self._udp_protocol = UDPRelayProtocol(self)
        transport, _ = await self._loop.create_datagram_endpoint(
            lambda: self._udp_protocol,
            sock=udp_sock,
        )
        self._udp_transport = transport
        logger.info("UDP Relay 服务启动: %s:%d", self._server_ip, UDP_PORT)

        # ── 启动后台维护任务 ──
        self._background_tasks.append(
            asyncio.ensure_future(self._heartbeat_checker())
        )
        self._background_tasks.append(
            asyncio.ensure_future(self._relay_session_checker())
        )
        self._background_tasks.append(
            asyncio.ensure_future(self._ban_cleanup_task())
        )

        logger.info("═══ P2P Server 启动完成 ═══")
        logger.info("  TCP 信令端口:    %d", TCP_PORT)
        logger.info("  UDP Relay端口:   %d", UDP_PORT)
        logger.info("  advertise_host:  %s", self._public_ip)
        logger.info("  relay_port:      %d", UDP_PORT)
        logger.info("  relay_max_sessions_per_ip: %d", self._relay_max_sessions_per_ip)
        logger.info("  relay_max_sessions:        %d", self._relay_max_sessions)
        logger.info("  relay_bytes_per_second:    %d", self._relay_bytes_per_second)
        logger.info("  relay_burst_bytes:         %d", self._relay_burst_bytes)

    async def stop(self) -> None:
        """优雅关闭服务器 — 清理所有资源"""
        if self._shutting_down:
            return
        self._shutting_down = True
        logger.info("═══ 正在关闭 P2P Server ═══")

        # 1. 关闭所有房间
        room_ids = list(self._rooms.keys())
        for room_id in room_ids:
            await self._close_room(room_id, reason="Server shutting down")

        # 2. 关闭所有 TCP 连接
        for player in list(self._players.values()):
            await self._close_tcp_writer(player.tcp_writer)

        # 3. 取消后台任务
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # 4. 关闭 TCP 服务器
        if self._tcp_server is not None:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None

        # 5. 关闭 UDP transport
        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None

        logger.info("═══ P2P Server 已关闭 ═══")

    # ══════════════════════════════════════════════════════════════════════
    # TCP 连接处理
    # ══════════════════════════════════════════════════════════════════════

    async def _handle_tcp_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理一个 TCP 客户端连接"""
        peer_info = writer.get_extra_info('peername')
        if peer_info is None:
            await self._close_tcp_writer(writer)
            return

        client_ip: str = peer_info[0]
        client_port: int = peer_info[1]
        conn_key = f"{client_ip}:{client_port}"

        # ── IP 黑名单检查 ──
        if self._rate_limiter.is_banned(client_ip):
            logger.warning("黑名单 IP 连接被拒: %s", client_ip)
            await self._close_tcp_writer(writer)
            return

        # ── 连接数限制 ──
        if not self._rate_limiter.add_connection(client_ip):
            logger.warning("IP %s 连接数超限", client_ip)
            await self._close_tcp_writer(writer)
            return

        logger.info("TCP 连接建立: %s", conn_key)
        player_id: Optional[str] = None

        try:
            # 读取消息循环
            while not self._shutting_down:
                try:
                    line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=HEARTBEAT_TIMEOUT + 5,
                    )
                except asyncio.TimeoutError:
                    # 读取超时 — 可能是心跳超时
                    logger.info("TCP 读取超时: %s", conn_key)
                    break

                if not line:
                    # 连接关闭
                    break

                # ── 消息大小限制 ──
                if len(line) > MAX_TCP_MESSAGE:
                    logger.warning("消息过大 (%d bytes) from %s, 断开连接",
                                   len(line), conn_key)
                    break

                # ── TCP 速率限流 ──
                if not self._rate_limiter.check_tcp_rate(conn_key):
                    await self._send_error(writer, ErrorCode.RATE_LIMIT)
                    continue

                # ── 解码消息 ──
                result = decode_message(line)
                if result is None:
                    rl_logger.warning(
                        f"invalid_msg_{conn_key}",
                        "无效消息 from %s: %s", conn_key,
                        line[:100].decode('utf-8', errors='replace'),
                    )
                    await self._send_error(writer, ErrorCode.INVALID_MESSAGE)
                    continue

                msg_type, payload = result

                # ── 校验消息类型 ──
                if msg_type not in _VALID_MSG_TYPES:
                    await self._send_error(writer, ErrorCode.INVALID_MESSAGE)
                    continue

                # ── 分发消息 ──
                player_id = await self._dispatch_message(
                    msg_type, payload, writer, client_ip, conn_key, player_id,
                )

        except ConnectionResetError:
            logger.info("TCP 连接重置: %s", conn_key)
        except ConnectionAbortedError:
            logger.info("TCP 连接中止: %s", conn_key)
        except Exception:
            logger.exception("TCP 处理异常: %s", conn_key)
        finally:
            # ── TCP 断开 = 隐式 LEAVE_ROOM ──
            await self._handle_disconnect(player_id, conn_key, client_ip)
            self._rate_limiter.remove_tcp_bucket(conn_key)
            self._rate_limiter.remove_connection(client_ip)
            await self._close_tcp_writer(writer)

    async def _dispatch_message(
        self,
        msg_type: str,
        payload: dict,
        writer: asyncio.StreamWriter,
        client_ip: str,
        conn_key: str,
        current_player_id: Optional[str],
    ) -> Optional[str]:
        """消息分发路由，返回当前 player_id"""

        if msg_type == MSG_CREATE_ROOM:
            return await self._handle_create_room(
                payload, writer, client_ip, conn_key,
            )

        elif msg_type == MSG_JOIN_ROOM:
            return await self._handle_join_room(
                payload, writer, client_ip, conn_key,
            )

        elif msg_type == MSG_HEARTBEAT:
            await self._handle_heartbeat(
                payload, writer, current_player_id,
            )
            return current_player_id

        elif msg_type == MSG_LEAVE_ROOM:
            await self._handle_leave_room(
                payload, current_player_id,
            )
            return current_player_id

        elif msg_type == MSG_P2P_SUCCESS:
            await self._handle_p2p_success(
                payload, current_player_id,
            )
            return current_player_id

        elif msg_type == MSG_P2P_FAILED:
            await self._handle_p2p_failed(
                payload, current_player_id,
            )
            return current_player_id

        else:
            # 其他消息类型（如 ROOM_CREATED 等服务端消息）客户端不应发送
            await self._send_error(writer, ErrorCode.INVALID_MESSAGE)
            return current_player_id

    # ══════════════════════════════════════════════════════════════════════
    # TCP 消息处理器
    # ══════════════════════════════════════════════════════════════════════

    async def _handle_create_room(
        self,
        payload: dict,
        writer: asyncio.StreamWriter,
        client_ip: str,
        conn_key: str,
    ) -> Optional[str]:
        """处理 CREATE_ROOM 消息"""

        # ── 限流检查 ──
        if not self._rate_limiter.check_create_room_rate(client_ip):
            await self._send_error(writer, ErrorCode.RATE_LIMIT)
            return None

        # ── 服务器容量检查 ──
        if len(self._rooms) >= MAX_ROOMS:
            await self._send_error(writer, ErrorCode.SERVER_FULL)
            return None

        # ── IP 房间数检查 ──
        if not self._rate_limiter.add_room(client_ip):
            await self._send_error(writer, ErrorCode.SERVER_FULL)
            return None

        # ── 校验 player_name ──
        player_name = payload.get("player_name")
        if not self._validate_player_name(player_name):
            self._rate_limiter.remove_room(client_ip)
            await self._send_error(writer, ErrorCode.PLAYER_NAME_INVALID)
            return None

        # ── 检查该连接是否已经有玩家 ──
        existing_pid = self._conn_to_player.get(conn_key)
        if existing_pid is not None:
            existing_player = self._players.get(existing_pid)
            if existing_player is not None and existing_player.room_id is not None:
                self._rate_limiter.remove_room(client_ip)
                await self._send_error(writer, ErrorCode.DUPLICATE_ROOM)
                return existing_pid

        # ── 生成 ID ──
        player_id = generate_player_id()
        room_id = generate_room_id()

        # 确保 room_id 不重复
        attempts = 0
        while room_id in self._rooms and attempts < 100:
            room_id = generate_room_id()
            attempts += 1

        if room_id in self._rooms:
            self._rate_limiter.remove_room(client_ip)
            await self._send_error(writer, ErrorCode.SERVER_FULL)
            return None

        # ── 创建玩家 ──
        player = Player(
            player_id=player_id,
            player_name=player_name,
            room_id=room_id,
            tcp_writer=writer,
            peer_addr=conn_key,
            tcp_ip=client_ip,
            last_heartbeat=time.monotonic(),
        )
        self._players[player_id] = player
        self._conn_to_player[conn_key] = player_id

        # ── 创建房间 ──
        room = Room(
            room_id=room_id,
            state=STATE_WAITING,
            creator_id=player_id,
            created_at=time.monotonic(),
        )
        self._rooms[room_id] = room

        # ── 返回 ROOM_CREATED ──
        await self._send_message(writer, MSG_ROOM_CREATED, {
            "room_id":   room_id,
            "player_id": player_id,
        })

        logger.info("房间创建: %s (创建者: %s / %s)", room_id, player_name, player_id)
        return player_id

    async def _handle_join_room(
        self,
        payload: dict,
        writer: asyncio.StreamWriter,
        client_ip: str,
        conn_key: str,
    ) -> Optional[str]:
        """处理 JOIN_ROOM 消息"""

        # ── 限流检查 ──
        if not self._rate_limiter.check_join_room_rate(client_ip):
            await self._send_error(writer, ErrorCode.RATE_LIMIT)
            return None

        # ── 校验 room_id ──
        room_id = payload.get("room_id")
        if not isinstance(room_id, str) or not _RE_ROOM_ID.match(room_id):
            await self._send_error(writer, ErrorCode.ROOM_NOT_FOUND)
            return None

        # ── 校验 player_name ──
        player_name = payload.get("player_name")
        if not self._validate_player_name(player_name):
            await self._send_error(writer, ErrorCode.PLAYER_NAME_INVALID)
            return None

        # ── 检查房间是否存在 ──
        room = self._rooms.get(room_id)
        if room is None or room.state == STATE_CLOSED:
            await self._send_error(writer, ErrorCode.ROOM_NOT_FOUND)
            return None

        # ── 检查房间是否已满 ──
        if room.state != STATE_WAITING:
            await self._send_error(writer, ErrorCode.ROOM_FULL)
            return None

        # ── 检查连接是否已有玩家在房间中 ──
        existing_pid = self._conn_to_player.get(conn_key)
        if existing_pid is not None:
            existing_player = self._players.get(existing_pid)
            if existing_player is not None and existing_player.room_id is not None:
                await self._send_error(writer, ErrorCode.DUPLICATE_ROOM)
                return existing_pid

        # ── 生成玩家 ID ──
        player_id = generate_player_id()

        # ── 创建玩家 ──
        player = Player(
            player_id=player_id,
            player_name=player_name,
            room_id=room_id,
            tcp_writer=writer,
            peer_addr=conn_key,
            tcp_ip=client_ip,
            last_heartbeat=time.monotonic(),
        )
        self._players[player_id] = player
        self._conn_to_player[conn_key] = player_id

        # ── 更新房间 ──
        room.joiner_id = player_id
        room.state = STATE_READY

        # ── 返回 ROOM_JOINED ──
        await self._send_message(writer, MSG_ROOM_JOINED, {
            "room_id":   room_id,
            "player_id": player_id,
        })

        logger.info("玩家加入房间: %s -> %s (玩家: %s / %s)",
                     player_name, room_id, player_id, conn_key)

        # 状态进入 READY，等待双方 UDP 注册
        # PEER_INFO 将在双方 UDP 注册完成后发送
        return player_id

    async def _handle_heartbeat(
        self,
        payload: dict,
        writer: asyncio.StreamWriter,
        player_id: Optional[str],
    ) -> None:
        """处理 HEARTBEAT 消息 — 双向"""
        timestamp = payload.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            await self._send_error(writer, ErrorCode.INVALID_MESSAGE)
            return

        # 更新玩家最后心跳时间
        if player_id is not None:
            player = self._players.get(player_id)
            if player is not None:
                player.last_heartbeat = time.monotonic()

        # 回复 HEARTBEAT
        await self._send_message(writer, MSG_HEARTBEAT, {
            "timestamp": int(time.time()),
        })

    async def _handle_leave_room(
        self,
        payload: dict,
        player_id: Optional[str],
    ) -> None:
        """处理 LEAVE_ROOM 消息"""
        if player_id is None:
            return

        room_id = payload.get("room_id")
        if not isinstance(room_id, str):
            return

        player = self._players.get(player_id)
        if player is None or player.room_id != room_id:
            return

        logger.info("玩家离开房间: %s from %s", player_id, room_id)
        await self._close_room(room_id, reason="Player left")

    async def _handle_p2p_success(
        self,
        payload: dict,
        player_id: Optional[str],
    ) -> None:
        """
        处理 P2P_SUCCESS 消息

        双确认机制: 双方均发送 P2P_SUCCESS 后，房间状态才可变为 DIRECT
        """
        if player_id is None:
            return

        room_id = payload.get("room_id")
        if not isinstance(room_id, str):
            return

        room = self._rooms.get(room_id)
        if room is None:
            return

        # 只有 PUNCHING 状态才接受 P2P_SUCCESS
        if room.state != STATE_PUNCHING:
            return

        player = self._players.get(player_id)
        if player is None or player.room_id != room_id:
            return

        # 记录该玩家已报告 P2P_SUCCESS
        room.p2p_success_players.add(player_id)
        logger.info("P2P_SUCCESS from %s in room %s (%d/2)",
                     player_id, room_id, len(room.p2p_success_players))

        # ── 双确认检查: 双方均报告成功 ──
        both_players = {room.creator_id, room.joiner_id}
        if room.p2p_success_players >= both_players:
            # 取消打洞超时
            self._cancel_punch_timeout(room)
            room.state = STATE_DIRECT
            logger.info("房间 %s 进入 DIRECT 状态 (P2P 直连成功)", room_id)

    async def _handle_p2p_failed(
        self,
        payload: dict,
        player_id: Optional[str],
    ) -> None:
        """
        处理 P2P_FAILED 消息

        任一方发送 P2P_FAILED → 状态变为 RELAY → 向双方下发 RELAY_ENABLED
        """
        if player_id is None:
            return

        room_id = payload.get("room_id")
        if not isinstance(room_id, str):
            return

        room = self._rooms.get(room_id)
        if room is None:
            return

        # 只有 PUNCHING 状态才接受 P2P_FAILED
        if room.state != STATE_PUNCHING:
            return

        reason = payload.get("reason", "UNKNOWN")
        logger.info("P2P_FAILED from %s in room %s, reason: %s",
                     player_id, room_id, reason)

        # ── 任一方失败 → 立即切换 RELAY ──
        await self._switch_to_relay(room)

    # ══════════════════════════════════════════════════════════════════════
    # UDP 注册处理 (由 UDPRelayProtocol 调用)
    # ══════════════════════════════════════════════════════════════════════

    def handle_udp_register(
        self,
        data: bytes,
        addr: Tuple[str, int],
    ) -> None:
        """处理 UDP REG 注册包"""
        try:
            json_str = data.decode('utf-8')
            obj = json.loads(json_str)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        player_id = obj.get("player_id")
        room_id = obj.get("room_id")

        if not isinstance(player_id, str) or not isinstance(room_id, str):
            return
        if not _RE_PLAYER_ID.match(player_id):
            return

        player = self._players.get(player_id)
        if player is None or player.room_id != room_id:
            return

        # 记录 UDP 公网地址
        old_addr = player.udp_addr
        player.udp_addr = addr

        # 更新 UDP 反向索引
        if old_addr is not None and old_addr != addr:
            self._udp_to_player.pop(old_addr, None)
        self._udp_to_player[addr] = player_id

        logger.info("UDP 注册: %s -> %s:%d (房间 %s)",
                     player_id, addr[0], addr[1], room_id)

        # ── 检查是否双方都已注册 → 推送 PEER_INFO ──
        room = self._rooms.get(room_id)
        if room is None or room.state != STATE_READY:
            return

        creator = self._players.get(room.creator_id) if room.creator_id else None
        joiner = self._players.get(room.joiner_id) if room.joiner_id else None

        if (creator is not None and creator.udp_addr is not None and
                joiner is not None and joiner.udp_addr is not None):
            # 双方 UDP 注册完成 → 发送 PEER_INFO，进入 PUNCHING
            asyncio.ensure_future(self._send_peer_info(room, creator, joiner))

    async def _send_peer_info(
        self,
        room: Room,
        creator: Player,
        joiner: Player,
    ) -> None:
        """向双方发送 PEER_INFO，进入 PUNCHING 状态"""
        if room.state != STATE_READY:
            return

        room.state = STATE_PUNCHING
        logger.info("房间 %s 进入 PUNCHING 状态", room.room_id)

        # 向创建者发送加入者的信息
        if creator.tcp_writer is not None and joiner.udp_addr is not None:
            await self._send_message(creator.tcp_writer, MSG_PEER_INFO, {
                "peer_id":   joiner.player_id,
                "peer_name": joiner.player_name,
                "peer_ip":   joiner.udp_addr[0],
                "peer_port": joiner.udp_addr[1],
                "room_id":   room.room_id,
            })

        # 向加入者发送创建者的信息
        if joiner.tcp_writer is not None and creator.udp_addr is not None:
            await self._send_message(joiner.tcp_writer, MSG_PEER_INFO, {
                "peer_id":   creator.player_id,
                "peer_name": creator.player_name,
                "peer_ip":   creator.udp_addr[0],
                "peer_port": creator.udp_addr[1],
                "room_id":   room.room_id,
            })

        # ── 设置服务端打洞超时 (10 秒) ──
        self._set_punch_timeout(room)

    def _set_punch_timeout(self, room: Room) -> None:
        """设置打洞超时: PUNCHING 超时 10 秒 → 强制 RELAY"""
        self._cancel_punch_timeout(room)

        def _on_punch_timeout() -> None:
            if room.state == STATE_PUNCHING:
                logger.info("房间 %s 打洞超时 (%ds), 强制切换 RELAY",
                            room.room_id, SERVER_PUNCH_TIMEOUT)
                asyncio.ensure_future(self._switch_to_relay(room))

        loop = self._loop
        if loop is not None and loop.is_running():
            room.punch_timeout_handle = loop.call_later(
                SERVER_PUNCH_TIMEOUT, _on_punch_timeout,
            )

    def _cancel_punch_timeout(self, room: Room) -> None:
        """取消打洞超时"""
        if room.punch_timeout_handle is not None:
            room.punch_timeout_handle.cancel()
            room.punch_timeout_handle = None

    # ══════════════════════════════════════════════════════════════════════
    # Relay 切换与数据转发
    # ══════════════════════════════════════════════════════════════════════

    async def _switch_to_relay(self, room: Room) -> None:
        """切换到 RELAY 模式"""
        if room.state not in (STATE_PUNCHING,):
            return

        # 取消打洞超时
        self._cancel_punch_timeout(room)

        creator = self._players.get(room.creator_id) if room.creator_id else None
        joiner = self._players.get(room.joiner_id) if room.joiner_id else None

        if creator is None or joiner is None:
            logger.warning("房间 %s 切换 RELAY 失败: 玩家缺失", room.room_id)
            await self._close_room(room.room_id, reason="Player missing")
            return

        # ── Relay capacity checks ──
        if len(self._relay_sessions) >= self._relay_max_sessions:
            logger.warning(
                "房间 %s 切换 RELAY 失败: 全局 Relay 会话数超限 "
                "(active=%d limit=%d)",
                room.room_id,
                len(self._relay_sessions),
                self._relay_max_sessions,
            )
            if creator.tcp_writer:
                await self._send_error(creator.tcp_writer, ErrorCode.RELAY_UNAVAILABLE)
            if joiner.tcp_writer:
                await self._send_error(joiner.tcp_writer, ErrorCode.RELAY_UNAVAILABLE)
            return

        creator_ip = creator.tcp_ip or ""
        joiner_ip = joiner.tcp_ip or ""
        relay_ips = tuple(dict.fromkeys((creator_ip, joiner_ip)))
        reserved_ips: list[str] = []
        for relay_ip in relay_ips:
            if self._rate_limiter.add_relay(relay_ip):
                reserved_ips.append(relay_ip)
                continue
            for reserved_ip in reserved_ips:
                self._rate_limiter.remove_relay(reserved_ip)
            logger.warning(
                "房间 %s 切换 RELAY 失败: IP %s Relay 会话数超限 (limit=%d)",
                room.room_id,
                relay_ip,
                self._relay_max_sessions_per_ip,
            )
            if creator.tcp_writer:
                await self._send_error(creator.tcp_writer, ErrorCode.RELAY_UNAVAILABLE)
            if joiner.tcp_writer:
                await self._send_error(joiner.tcp_writer, ErrorCode.RELAY_UNAVAILABLE)
            return

        # ── 生成 Relay Token ──
        relay_token = generate_relay_token()
        now = time.monotonic()

        relay_session = RelaySession(
            relay_token=relay_token,
            room_id=room.room_id,
            player_a_id=creator.player_id,
            player_b_id=joiner.player_id,
            created_at=now,
            last_activity=now,
            bandwidth_window_start=now,
            bandwidth_tokens=float(self._relay_burst_bytes),
            bandwidth_last_refill=now,
            reserved_ips=tuple(reserved_ips),
        )

        room.relay_session = relay_session
        room.state = STATE_RELAY
        self._relay_sessions[relay_token] = relay_session

        logger.info("房间 %s 进入 RELAY 状态 (token: %s)", room.room_id, relay_token)

        # ── 向双方发送 RELAY_ENABLED ──
        relay_payload = {
            "room_id":     room.room_id,
            "relay_ip":    self._public_ip,
            "relay_port":  UDP_PORT,
            "relay_token": relay_token,
        }

        if creator.tcp_writer is not None:
            await self._send_message(creator.tcp_writer, MSG_RELAY_ENABLED, relay_payload)
        if joiner.tcp_writer is not None:
            await self._send_message(joiner.tcp_writer, MSG_RELAY_ENABLED, relay_payload)

    def handle_udp_relay(
        self,
        raw_data: memoryview,
        addr: Tuple[str, int],
    ) -> None:
        """
        处理 UDP RELAY 数据包 — 高频路径，性能优化

        格式: RELAY\n<json_header>\n<game_data>
        """
        ip = addr[0]

        # ── IP 黑名单检查 ──
        if self._rate_limiter.is_banned(ip):
            return

        # 找到第二个 \n (JSON header 的结尾)
        # raw_data 此时已去掉了 "RELAY\n" 前缀
        data_bytes = bytes(raw_data)
        newline_pos = data_bytes.find(b'\n')
        if newline_pos < 0:
            if self._rate_limiter.record_invalid_packet(ip):
                self._rate_limiter.ban_ip(ip, IP_BAN_DURATION_UDP)
            return

        # ── 解析 JSON header ──
        try:
            header = json.loads(data_bytes[:newline_pos])
        except json.JSONDecodeError:
            if self._rate_limiter.record_invalid_packet(ip):
                self._rate_limiter.ban_ip(ip, IP_BAN_DURATION_UDP)
            return

        relay_token = header.get("relay_token")
        player_id = header.get("player_id")

        if not isinstance(relay_token, str) or not isinstance(player_id, str):
            # Session is unknown at this point; log via rate limiter for visibility
            rl_logger.warning(
                "relay_missing_fields",
                "Relay packet missing relay_token or player_id from %s", ip,
            )
            return

        # ── 验证 Token ──
        session = self._relay_sessions.get(relay_token)
        if session is None:
            if self._rate_limiter.record_invalid_token(ip):
                self._rate_limiter.ban_ip(ip, IP_BAN_DURATION_TOKEN)
            return

        # ── Session-level diagnostics: received ──
        session.relay_packets_received += 1
        session.relay_bytes_received += len(data_bytes)

        # ── 确定转发目标 ──
        if player_id == session.player_a_id:
            target_id = session.player_b_id
        elif player_id == session.player_b_id:
            target_id = session.player_a_id
        else:
            session.relay_drop_player_mismatch += 1
            return

        target_player = self._players.get(target_id)
        if target_player is None or target_player.udp_addr is None:
            session.relay_drop_no_target_udp_addr += 1
            return

        # ── 包速率限制检查 (60 pkt/s/session) ──
        now = time.monotonic()

        is_whitelisted = self._rate_limiter._is_whitelist(ip)

        if not is_whitelisted:
            if now - session.pkt_window_start >= 1.0:
                session.pkt_window_start = now
                session.pkt_count = 0

            session.pkt_count += 1
            if session.pkt_count > RELAY_RATE_LIMIT:
                session.dropped_packets += 1
                session.relay_drop_rate_limit_exceeded += 1
                rl_logger.warning(
                    f"relay_pkt_{session.relay_token}",
                    "Relay 包速率超限, 丢弃 payload "
                    "(token=%s packet_limit=%d dropped_packets=%d)",
                    session.relay_token,
                    RELAY_RATE_LIMIT,
                    session.dropped_packets,
                )
                return

        # ── Server-local token bucket bandwidth limit ──
        game_data_len = len(data_bytes) - newline_pos - 1

        if not is_whitelisted:
            elapsed = max(0.0, now - session.bandwidth_last_refill)
            session.bandwidth_tokens = min(
                float(self._relay_burst_bytes),
                session.bandwidth_tokens + elapsed * self._relay_bytes_per_second,
            )
            session.bandwidth_last_refill = now
            if game_data_len > session.bandwidth_tokens:
                session.dropped_packets += 1
                session.dropped_bytes += game_data_len
                session.relay_drop_bandwidth_exceeded += 1
                rl_logger.warning(
                    f"relay_bw_{session.relay_token}",
                    "Relay 带宽超限, 丢弃 payload "
                    "(token=%s payload_bytes=%d available_bytes=%d "
                    "bytes_per_second=%d burst_bytes=%d "
                    "dropped_packets=%d dropped_bytes=%d)",
                    session.relay_token,
                    game_data_len,
                    int(session.bandwidth_tokens),
                    self._relay_bytes_per_second,
                    self._relay_burst_bytes,
                    session.dropped_packets,
                    session.dropped_bytes,
                )
                return
            session.bandwidth_tokens -= game_data_len

        if now - session.bandwidth_window_start >= 1.0:
            session.bandwidth_window_start = now
            session.bandwidth_bytes = 0
        session.bandwidth_bytes += game_data_len
        session.last_activity = now

        # ── 转发数据 — 保持原始格式 ──
        # 重建完整 RELAY 包: RELAY\n<json_header>\n<game_data>
        # 原始包已经是正确格式，直接使用原始数据（加回前缀）
        if self._udp_transport is not None:
            # 构建转发包：保持 player_id 为发送方 ID 以便接收方识别来源
            forward_header = json.dumps(
                {"relay_token": relay_token, "player_id": player_id},
                separators=(',', ':'),
            ).encode('utf-8')
            forward_data = b"RELAY\n" + forward_header + b"\n" + data_bytes[newline_pos + 1:]
            try:
                self._udp_transport.sendto(forward_data, target_player.udp_addr)
                session.relay_packets_forwarded += 1
                session.relay_bytes_forwarded += len(forward_data)
            except OSError:
                session.relay_forward_exceptions += 1
        else:
            session.relay_drop_no_udp_transport += 1

    # ══════════════════════════════════════════════════════════════════════
    # 房间关闭与资源清理
    # ══════════════════════════════════════════════════════════════════════

    async def _close_room(
        self,
        room_id: str,
        reason: str = "",
        error_code: ErrorCode = ErrorCode.ROOM_NOT_FOUND,
    ) -> None:
        """
        关闭房间 — 幂等清理所有关联资源

        使用 _rooms.pop() 原子操作保证只有一个协程执行清理，允许重复调用但不会重复清理。

        清理项目:
        1. 取消打洞超时定时器
        2. 清理 Relay 会话 + 归还限流计数
        3. 通知玩家 + 清理玩家数据 + 关闭 TCP
        4. 清理所有反向索引 (_conn_to_player, _udp_to_player)
        5. 取消关联异步任务
        """
        # ── 原子获取并移除房间，保证幂等性 ──
        room = self._rooms.pop(room_id, None)
        if room is None:
            return

        if room.state == STATE_CLOSED:
            # 已被标记关闭（防御性检查）
            return

        old_state = room.state
        room.state = STATE_CLOSED
        logger.info("房间关闭: %s (原状态: %s, 原因: %s)", room_id, old_state, reason)

        # 1. 取消打洞超时
        self._cancel_punch_timeout(room)

        # 2. 清理 Relay 会话
        if room.relay_session is not None:
            token = room.relay_session.relay_token
            self._relay_sessions.pop(token, None)

            # Return each per-IP session reservation exactly once.
            reserved_ips = room.relay_session.reserved_ips
            if not reserved_ips:
                fallback_ips: list[str] = []
                for pid in (room.creator_id, room.joiner_id):
                    if pid is None:
                        continue
                    p = self._players.get(pid)
                    if p is not None and p.tcp_ip:
                        fallback_ips.append(p.tcp_ip)
                reserved_ips = tuple(dict.fromkeys(fallback_ips))
            for relay_ip in reserved_ips:
                self._rate_limiter.remove_relay(relay_ip)

            room.relay_session = None

        # 3. 清理玩家 — 统一清理路径
        for pid in (room.creator_id, room.joiner_id):
            if pid is None:
                continue
            player = self._players.pop(pid, None)
            if player is None:
                continue

            # 通知玩家（在关闭 writer 之前）
            if player.tcp_writer is not None and not player.tcp_writer.is_closing():
                try:
                    await self._send_error(player.tcp_writer, error_code)
                except Exception:
                    pass

            # 清理 conn_to_player 反向索引
            if player.peer_addr:
                self._conn_to_player.pop(player.peer_addr, None)

            # 清理 UDP 反向索引
            if player.udp_addr is not None:
                self._udp_to_player.pop(player.udp_addr, None)

            # 归还 IP 房间计数
            if player.tcp_ip:
                self._rate_limiter.remove_room(player.tcp_ip)

            # 关闭 TCP writer
            await self._close_tcp_writer(player.tcp_writer)

        # 4. 取消关联任务
        for task in room.tasks:
            if not task.done():
                task.cancel()
        room.tasks.clear()

    async def _handle_disconnect(
        self,
        player_id: Optional[str],
        conn_key: str,
        client_ip: str,
    ) -> None:
        """
        处理 TCP 断开 = 隐式 LEAVE_ROOM

        统一通过 _close_room 清理，避免双重清理和资源泄漏。
        conn_to_player 在此处和 _close_room 中均使用 pop(..., None)，
        重复调用安全。
        """
        self._conn_to_player.pop(conn_key, None)

        if player_id is None:
            return

        player = self._players.get(player_id)
        if player is None:
            # 玩家已被其他路径清理（如 _close_room 由心跳超时触发）
            return

        room_id = player.room_id
        logger.info("TCP 断开 (隐式 LEAVE): %s from %s", player_id, conn_key)

        if room_id is not None:
            # 有房间 — 通过 _close_room 统一清理 (房间 + 双方玩家)
            await self._close_room(room_id, reason="TCP disconnected")
        else:
            # 无房间的玩家 — 直接清理
            self._players.pop(player_id, None)
            if player.udp_addr is not None:
                self._udp_to_player.pop(player.udp_addr, None)

    # ══════════════════════════════════════════════════════════════════════
    # 后台维护任务
    # ══════════════════════════════════════════════════════════════════════

    async def _heartbeat_checker(self) -> None:
        """心跳超时检测 + WAITING 房间超时 — 定期扫描"""
        while not self._shutting_down:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                now = time.monotonic()

                # ── 1. 心跳超时检测 ──
                timed_out_rooms: set = set()

                for pid, player in list(self._players.items()):
                    if player.last_heartbeat > 0:
                        if now - player.last_heartbeat > HEARTBEAT_TIMEOUT:
                            logger.info("心跳超时: %s (房间: %s)", pid, player.room_id)
                            if player.room_id is not None:
                                timed_out_rooms.add(player.room_id)
                            else:
                                # 无房间的玩家超时 — 直接清理
                                self._players.pop(pid, None)
                                if player.peer_addr:
                                    self._conn_to_player.pop(player.peer_addr, None)
                                if player.udp_addr is not None:
                                    self._udp_to_player.pop(player.udp_addr, None)
                                await self._close_tcp_writer(player.tcp_writer)

                for room_id in timed_out_rooms:
                    await self._close_room(
                        room_id,
                        reason="Heartbeat timeout",
                        error_code=ErrorCode.HEARTBEAT_TIMEOUT,
                    )

                # ── 2. WAITING 房间超时清理 (5 分钟) ──
                for room_id, room in list(self._rooms.items()):
                    if room.state == STATE_WAITING:
                        if now - room.created_at > WAITING_ROOM_TIMEOUT:
                            logger.info("WAITING 超时: 房间 %s (已等待 %.0f 秒)",
                                        room_id, now - room.created_at)
                            await self._close_room(
                                room_id, reason="WAITING timeout",
                            )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("心跳检测异常")

    async def _relay_session_checker(self) -> None:
        """Relay 会话超时检测"""
        while not self._shutting_down:
            try:
                await asyncio.sleep(10)  # 每 10 秒检查一次

                now = time.monotonic()
                expired_tokens: list[str] = []

                for token, session in list(self._relay_sessions.items()):
                    # 会话时长超时: 最大 2 小时
                    if now - session.created_at > RELAY_SESSION_TIMEOUT:
                        expired_tokens.append(token)
                        continue

                    # 空闲超时 (使用服务端本地配置)
                    if now - session.last_activity > _RELAY_IDLE_TIMEOUT_SECONDS:
                        expired_tokens.append(token)
                        continue

                for token in expired_tokens:
                    session = self._relay_sessions.get(token)
                    if session is None:
                        continue

                    logger.info("Relay 会话超时: %s (房间 %s)",
                                token, session.room_id)

                    # 关闭对应房间
                    await self._close_room(session.room_id, reason="Relay timeout")

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Relay 检测异常")

    async def _ban_cleanup_task(self) -> None:
        """定期清理过期的 IP 封禁"""
        while not self._shutting_down:
            try:
                await asyncio.sleep(60)
                self._rate_limiter.cleanup_expired_bans()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("封禁清理异常")

    # ══════════════════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _validate_player_name(name: object) -> bool:
        """校验玩家名: 1-32 字符"""
        if not isinstance(name, str):
            return False
        if len(name) < 1 or len(name) > 32:
            return False
        # 不允许纯空白
        if not name.strip():
            return False
        return True

    @staticmethod
    async def _send_message(
        writer: asyncio.StreamWriter,
        msg_type: str,
        payload: dict,
    ) -> None:
        """发送 TCP 消息"""
        if writer.is_closing():
            return
        try:
            data = encode_message(msg_type, payload)
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    @staticmethod
    async def _send_error(
        writer: asyncio.StreamWriter,
        error_code: ErrorCode,
    ) -> None:
        """发送 ERROR 消息"""
        if writer.is_closing():
            return
        message = _ERROR_MESSAGES.get(int(error_code), "Unknown error")
        try:
            data = encode_message(MSG_ERROR, {
                "code":    int(error_code),
                "message": message,
            })
            writer.write(data)
            await writer.drain()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
            pass

    @staticmethod
    async def _close_tcp_writer(writer: Optional[asyncio.StreamWriter]) -> None:
        """安全关闭 TCP writer"""
        if writer is None:
            return
        if writer.is_closing():
            return
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# UDP DatagramProtocol
# ══════════════════════════════════════════════════════════════════════════════

class UDPRelayProtocol(asyncio.DatagramProtocol):
    """
    UDP 数据报协议

    处理:
    - REG\n    — NAT 地址注册
    - RELAY\n  — Relay 数据中转 (高频路径)

    服务端不直接处理 PUNCH/PING/PONG/DATA (这些是客户端之间的)
    """

    def __init__(self, server: P2PServer) -> None:
        self._server = server
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self._transport = transport
        logger.info("UDP transport 就绪")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc is not None:
            logger.warning("UDP transport 丢失: %s", exc)
        self._transport = None

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        """
        接收 UDP 数据报 — 高频路径

        性能优化:
        - 使用 memoryview 避免拷贝
        - 前缀匹配使用 bytes.startswith (C 层比较)
        - Relay 路径避免不必要的 JSON 解码
        """
        # ── 包大小限制 ──
        if len(data) > MAX_UDP_PACKET:
            return

        if len(data) < 4:  # 最短前缀 REG\n = 4B
            return

        ip = addr[0]

        # ── IP 黑名单检查 ──
        if self._server._rate_limiter.is_banned(ip):
            return

        # ── UDP 速率限流 ──
        if not self._server._rate_limiter.check_udp_rate(ip):
            rl_logger.warning(
                f"udp_rate_{ip}",
                "UDP 速率超限: %s", ip,
            )
            return

        # ── 前缀分发 ──
        if data[:4] == b'REG\n':
            # REG 注册包
            self._server.handle_udp_register(data[4:], addr)

        elif data[:6] == b'RELAY\n':
            # RELAY 中转包 — 高频路径，使用 memoryview
            mv = memoryview(data)[6:]
            self._server.handle_udp_relay(mv, addr)

        else:
            # 服务端不处理 PUNCH/PING/PONG/DATA — 它们是客户端之间直接通信的
            # 记录无效包
            if self._server._rate_limiter.record_invalid_packet(ip):
                self._server._rate_limiter.ban_ip(ip, IP_BAN_DURATION_UDP)

    def error_received(self, exc: Exception) -> None:
        """UDP 错误处理"""
        rl_logger.warning("udp_error", "UDP 错误: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='S2Pass P2P UDP Hole Punching + Relay Server',
    )
    parser.add_argument(
        '--advertise-host',
        default=None,
        help='服务器对客户端公布的 Relay 公网地址 (VPS 公网 IP 或域名)。'
             '优先级: --advertise-host > S2PASS_ADVERTISE_HOST 环境变量 > 默认值 127.0.0.1。'
             '示例: --advertise-host 120.27.210.184',
    )
    parser.add_argument(
        '--relay-max-sessions-per-ip',
        type=_positive_int_arg,
        default=None,
        help='Per-IP active Relay session limit '
             '(CLI > S2PASS_RELAY_MAX_SESSIONS_PER_IP > default 2).',
    )
    parser.add_argument(
        '--relay-max-sessions',
        type=_positive_int_arg,
        default=None,
        help='Global active Relay session limit '
             '(CLI > S2PASS_RELAY_MAX_SESSIONS > default 500).',
    )
    parser.add_argument(
        '--relay-bytes-per-second',
        type=_positive_int_arg,
        default=None,
        help='Server-local per-session Relay sustained byte rate '
             '(CLI > S2PASS_RELAY_BYTES_PER_SECOND > default 32768).',
    )
    parser.add_argument(
        '--relay-burst-bytes',
        type=_positive_int_arg,
        default=None,
        help='Server-local per-session Relay token bucket burst '
             '(CLI > S2PASS_RELAY_BURST_BYTES > default 524288).',
    )
    return parser.parse_args()


async def _main() -> None:
    """异步主入口"""
    args = _parse_args()
    server = P2PServer(
        advertise_host=args.advertise_host,
        relay_max_sessions_per_ip=args.relay_max_sessions_per_ip,
        relay_max_sessions=args.relay_max_sessions,
        relay_bytes_per_second=args.relay_bytes_per_second,
        relay_burst_bytes=args.relay_burst_bytes,
    )

    # ── 注册信号处理 (优雅关闭) ──
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("收到关闭信号")
        shutdown_event.set()

    # Windows 只支持 SIGINT，Linux 支持 SIGTERM
    if sys.platform != 'win32':
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    else:
        # Windows: 使用 signal 模块处理 Ctrl+C
        # 注意: Windows 的 asyncio 不支持 add_signal_handler
        # 但 KeyboardInterrupt 会自然传播
        pass

    try:
        await server.start()

        if sys.platform != 'win32':
            await shutdown_event.wait()
        else:
            # Windows: 使用无限等待，KeyboardInterrupt 会中断
            while True:
                await asyncio.sleep(3600)

    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C")
    finally:
        await server.stop()


def main() -> None:
    """程序入口"""
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
