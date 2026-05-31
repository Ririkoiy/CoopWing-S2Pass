#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S2Pass Network Core — reusable by CLI, DearPyGui, Flutter bridge.

All protocol JSON construction is internal.
All output goes through event callback or asyncio.Queue.
No print() in this module.
"""

import asyncio
import dataclasses
import json
import struct
import time
from typing import Optional, Callable, Dict, Any

# ---------------------------------------------------------------------------
# Constants from PROTOCOL_LOCK.md (frozen, do not modify)
# ---------------------------------------------------------------------------
TCP_PORT = 9000
UDP_PORT = 9001
HEARTBEAT_INTERVAL = 5

# Timeout constants matching old cli_client.py
ROOM_WAIT_TIMEOUT = 10       # seconds, for ROOM_CREATED / ROOM_JOINED
PEER_INFO_TIMEOUT = 300      # seconds, default lobby wait for PEER_INFO
RELAY_ENABLED_TIMEOUT = 30   # seconds, for RELAY_ENABLED

# relay_ip values considered unusable → fallback to config.host
_BAD_RELAY_IPS = {None, "", "0.0.0.0", "::", "127.0.0.1"}

# ---------------------------------------------------------------------------
# Event type constants (client-side UI events, NOT protocol message types)
# ---------------------------------------------------------------------------
EVT_TCP_CONNECTED    = "TCP_CONNECTED"
EVT_ROOM_CREATED     = "ROOM_CREATED"
EVT_ROOM_JOINED      = "ROOM_JOINED"
EVT_PEER_INFO        = "PEER_INFO"
EVT_RELAY_ENABLED    = "RELAY_ENABLED"
EVT_RELAY_FALLBACK   = "RELAY_FALLBACK"
EVT_UDP_REG_SENT     = "UDP_REG_SENT"
EVT_P2P_FAILED_SENT  = "P2P_FAILED_SENT"
EVT_ERROR            = "ERROR"
EVT_TIMEOUT          = "TIMEOUT"
EVT_CONNECTION_LOST  = "CONNECTION_LOST"
EVT_TEST_STARTED     = "TEST_STARTED"
EVT_TEST_STATS       = "TEST_STATS"
EVT_TEST_COMPLETED   = "TEST_COMPLETED"
EVT_CLEANUP          = "CLEANUP"
EVT_INFO             = "INFO"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class S2PassConfig:
    """Configuration for S2PassClientCore. No argparse dependency."""
    host: str
    port: int = TCP_PORT
    udp_port: int = UDP_PORT
    player_name: str = "Player"
    room_id: Optional[str] = None       # Required for join, auto-set for create
    role: str = "create"                 # "create" or "join"
    force_relay: bool = False
    lobby_timeout: int = 300             # 0 = infinite
    start_delay: float = 1.0
    keep_open_after_test: bool = False
    send_test: bool = False
    pps: int = 10
    duration: int = 10
    packet_size: int = 64
    # UDP REG retry — old code sends 3 times, 0.05s apart (lines 421-423)
    udp_reg_count: int = 3
    udp_reg_interval: float = 0.05
    is_payload_mode: bool = False


@dataclasses.dataclass
class S2PassEvent:
    """Typed event output for UI consumption."""
    type: str
    message: str
    data: Optional[Dict[str, Any]] = dataclasses.field(default_factory=dict)
    timestamp: float = dataclasses.field(default_factory=time.time)


# ---------------------------------------------------------------------------
# UDP protocol (moved from old cli_client.py L30-44)
# ---------------------------------------------------------------------------
class _CoreUDPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler — delegates to S2PassClientCore."""

    def __init__(self, core: "S2PassClientCore"):
        self._core = core
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        self._core._handle_udp_packet(data, addr)

    def error_received(self, exc: Exception):
        # Ignore socket/network level errors gracefully (same as old code)
        pass


# ---------------------------------------------------------------------------
# S2PassClientCore
# ---------------------------------------------------------------------------
class S2PassClientCore:
    """
    Reusable network core for S2Pass.

    Usage:
        core = S2PassClientCore(config, event_callback=my_cb)
        await core.run()
        await core.close()

    event_callback signature: (event: S2PassEvent) -> None
    event_queue alternative: asyncio.Queue[S2PassEvent]
    """

    def __init__(
        self,
        config: S2PassConfig,
        event_callback: Optional[Callable[[S2PassEvent], None]] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ):
        if config.is_payload_mode and config.send_test:
            raise ValueError("is_payload_mode and send_test cannot both be True")
        self.config = config
        self._payload_callback: Optional[Callable[[bytes], None]] = None
        self._event_callback = event_callback
        self._event_queue = event_queue

        # --- State (mirrors old CLIClient.__init__ L47-92) ---
        self.room_id: Optional[str] = None
        self.player_id: Optional[str] = None

        # TCP connection
        self._tcp_reader: Optional[asyncio.StreamReader] = None
        self._tcp_writer: Optional[asyncio.StreamWriter] = None

        # UDP connection
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._udp_protocol: Optional[_CoreUDPProtocol] = None

        # Peer signaling information
        self.peer_id: Optional[str] = None
        self.peer_name: Optional[str] = None
        self.peer_ip: Optional[str] = None
        self.peer_port: Optional[int] = None

        # Relay parameters
        self.relay_token: Optional[str] = None
        self.relay_ip: Optional[str] = None
        self.relay_port: Optional[int] = None
        # Resolved relay target (actual address to sendto)
        self._relay_target_host: Optional[str] = None
        self._relay_target_port: Optional[int] = None

        # Synchronization Events
        self._room_created_event = asyncio.Event()
        self._room_joined_event = asyncio.Event()
        self._peer_info_event = asyncio.Event()
        self._relay_enabled_event = asyncio.Event()

        # Running tasks
        self._tcp_receiver_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._test_sender_task: Optional[asyncio.Task] = None
        self._stats_reporter_task: Optional[asyncio.Task] = None

        # Statistics
        self.packets_sent_count = 0
        self.packets_received_count = 0
        self.packets_echoed_count = 0
        self.sent_packets: Dict[int, float] = {}       # seq -> send_timestamp
        self.received_echoes: Dict[int, float] = {}    # seq -> rtt
        self.has_error = False

    def set_payload_callback(self, callback: Callable[[bytes], None]) -> None:
        """Set a callback to be invoked when a raw peer payload is received."""
        self._payload_callback = callback

    def send_payload(self, payload: bytes) -> None:
        """Send a raw payload byte stream to the peer via the relay path."""
        if not self.config.is_payload_mode:
            raise RuntimeError("Cannot send payload when not in payload mode")
        if not self.relay_token or not self._relay_target_host or not self._relay_target_port:
            raise RuntimeError("Relay path is not ready. Relay token or target is missing.")
        if not self._udp_transport:
            raise RuntimeError("UDP transport is not initialized.")

        packet = self._build_relay_packet(payload)
        self._send_udp_to_relay(packet)

    # ===================================================================
    # Event emission
    # ===================================================================
    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Dispatch event to callback or queue. Never print."""
        event = S2PassEvent(
            type=event_type,
            message=message,
            data=data if data is not None else {},
        )
        if self._event_callback is not None:
            self._event_callback(event)
        if self._event_queue is not None:
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop if queue full, avoid blocking

    # ===================================================================
    # Protocol message builders (all JSON construction lives here)
    # ===================================================================
    def _build_create_room(self) -> str:
        """Build CREATE_ROOM envelope. (old L371-373)"""
        payload = {"player_name": self.config.player_name}
        envelope = {"type": "CREATE_ROOM", "payload": payload}
        return json.dumps(envelope, separators=(',', ':')) + '\n'

    def _build_join_room(self) -> str:
        """Build JOIN_ROOM envelope. (old L393-395)"""
        payload = {"room_id": self.room_id, "player_name": self.config.player_name}
        envelope = {"type": "JOIN_ROOM", "payload": payload}
        return json.dumps(envelope, separators=(',', ':')) + '\n'

    def _build_heartbeat(self) -> str:
        """Build HEARTBEAT envelope. (old L280-282)"""
        payload = {"timestamp": int(time.time())}
        envelope = {"type": "HEARTBEAT", "payload": payload}
        return json.dumps(envelope, separators=(',', ':')) + '\n'

    def _build_p2p_failed(self, reason: str) -> str:
        """Build P2P_FAILED envelope. (old L176-181)"""
        payload = {"room_id": self.room_id, "reason": reason}
        envelope = {"type": "P2P_FAILED", "payload": payload}
        return json.dumps(envelope, separators=(',', ':')) + '\n'

    def _build_leave_room(self) -> str:
        """Build LEAVE_ROOM envelope. (old L488-490)"""
        payload = {"room_id": self.room_id}
        envelope = {"type": "LEAVE_ROOM", "payload": payload}
        return json.dumps(envelope, separators=(',', ':')) + '\n'

    def _build_udp_reg(self) -> bytes:
        """Build UDP REG packet. (old L161-165)"""
        payload = {
            "player_id": self.player_id,
            "room_id": self.room_id,
        }
        return b"REG\n" + json.dumps(payload, separators=(',', ':')).encode('utf-8')

    def _build_relay_packet(self, binary_payload: bytes) -> bytes:
        """Build RELAY UDP packet. (old L133-138)"""
        json_header = {
            "relay_token": self.relay_token,
            "player_id": self.player_id,
        }
        header_bytes = json.dumps(json_header, separators=(',', ':')).encode('utf-8')
        return b"RELAY\n" + header_bytes + b"\n" + binary_payload

    # ===================================================================
    # TCP helpers
    # ===================================================================
    async def _send_tcp(self, message: str):
        """Send a pre-built TCP message string. Raises on failure."""
        self._tcp_writer.write(message.encode('utf-8'))
        await self._tcp_writer.drain()

    # ===================================================================
    # UDP helpers
    # ===================================================================
    def _send_udp_to_server(self, packet: bytes):
        """Send UDP packet to server UDP port."""
        if self._udp_transport:
            self._udp_transport.sendto(packet, (self.config.host, self.config.udp_port))

    def _send_udp_to_relay(self, packet: bytes):
        """Send UDP packet to resolved relay target."""
        if self._udp_transport and self._relay_target_host and self._relay_target_port:
            self._udp_transport.sendto(
                packet, (self._relay_target_host, self._relay_target_port)
            )

    # ===================================================================
    # relay_ip fallback (old L146-156)
    # ===================================================================
    @staticmethod
    def _resolve_relay_host(relay_ip: Optional[str], fallback_host: str) -> tuple:
        """
        Resolve actual relay host.
        Returns (resolved_host, fallback_reason_or_None).
        """
        if relay_ip in _BAD_RELAY_IPS:
            return fallback_host, f"relay_ip '{relay_ip}' is unusable"
        if relay_ip and relay_ip.startswith("127."):
            return fallback_host, f"relay_ip '{relay_ip}' is loopback"
        return relay_ip, None

    # ===================================================================
    # UDP packet handler (old L94-115)
    # ===================================================================
    def _handle_udp_packet(self, data: bytes, addr: tuple):
        """Handle incoming UDP packet. Strictly RELAY\\n prefix only."""
        if not data.startswith(b"RELAY\n"):
            return
        relay_data = data[6:]
        newline_pos = relay_data.find(b'\n')
        if newline_pos < 0:
            return
        try:
            header = json.loads(relay_data[:newline_pos].decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        relay_token = header.get("relay_token")
        sender_id = header.get("player_id")

        # Verify relay token consistency
        if self.relay_token and relay_token != self.relay_token:
            return

        binary_payload = relay_data[newline_pos + 1:]
        self._on_relay_packet(sender_id, binary_payload)

    def _on_relay_packet(self, sender_id: str, binary_payload: bytes):
        """Handle relay payload. (old L117-144)"""
        if self.config.is_payload_mode:
            if self._payload_callback:
                self._payload_callback(binary_payload)
            return

        if self.config.send_test:
            # Active sender parses response packets to calculate stats
            if len(binary_payload) >= 12:
                try:
                    seq, timestamp = struct.unpack(">Id", binary_payload[:12])
                    if seq in self.sent_packets and seq not in self.received_echoes:
                        rtt = time.perf_counter() - self.sent_packets[seq]
                        self.received_echoes[seq] = rtt
                        self.packets_received_count += 1
                except struct.error:
                    pass
        else:
            # Echo responder: increment count and return packet back
            self.packets_received_count += 1
            if self.relay_token and self._relay_target_host and self._relay_target_port:
                packet = self._build_relay_packet(binary_payload)
                if self._udp_transport:
                    try:
                        self._send_udp_to_relay(packet)
                        self.packets_echoed_count += 1
                    except OSError:
                        pass

    # ===================================================================
    # TCP receive loop (old L189-216)
    # ===================================================================
    async def _receive_loop(self):
        try:
            while True:
                line = await self._tcp_reader.readline()
                if not line:
                    self._emit(EVT_CONNECTION_LOST, "TCP connection closed by server")
                    break

                try:
                    text = line.decode('utf-8').strip()
                    if not text:
                        continue
                    obj = json.loads(text)
                    msg_type = obj.get("type")
                    payload = obj.get("payload")
                    if not isinstance(msg_type, str) or not isinstance(payload, dict):
                        self._emit(EVT_INFO, "Invalid message format, skipping")
                        continue

                    await self._handle_tcp_message(msg_type, payload)
                except json.JSONDecodeError as e:
                    self._emit(EVT_INFO, f"JSON parse error: {e}")
                except UnicodeDecodeError as e:
                    self._emit(EVT_INFO, f"Decode error: {e}")
        except asyncio.CancelledError:
            pass
        except (ConnectionError, OSError) as e:
            self._emit(EVT_CONNECTION_LOST, f"TCP receive loop connection error: {e}")

    # ===================================================================
    # TCP message handler (old L218-272)
    # ===================================================================
    async def _handle_tcp_message(self, msg_type: str, payload: dict):
        if msg_type == "ROOM_CREATED":
            self.room_id = payload.get("room_id")
            self.player_id = payload.get("player_id")
            self._emit(EVT_ROOM_CREATED, f"room_id: {self.room_id}", {
                "room_id": self.room_id,
                "player_id": self.player_id,
            })
            self._room_created_event.set()

        elif msg_type == "ROOM_JOINED":
            self.player_id = payload.get("player_id")
            self._emit(EVT_ROOM_JOINED, f"player_id: {self.player_id}", {
                "player_id": self.player_id,
            })
            self._room_joined_event.set()

        elif msg_type == "PEER_INFO":
            self.peer_id = payload.get("peer_id")
            self.peer_name = payload.get("peer_name")
            self.peer_ip = payload.get("peer_ip")
            self.peer_port = payload.get("peer_port")
            self._emit(EVT_PEER_INFO, "PEER_INFO received, UDP registration confirmed", {
                "peer_id": self.peer_id,
                "peer_name": self.peer_name,
                "peer_ip": self.peer_ip,
                "peer_port": self.peer_port,
            })
            self._peer_info_event.set()

            # If force-relay: creator immediately triggers P2P_FAILED,
            # joiner waits for RELAY_ENABLED. (old L241-245)
            if self.config.force_relay:
                if self.config.role == "create":
                    await self._send_p2p_failed("TIMEOUT")
                elif self.config.role == "join":
                    self._emit(EVT_INFO,
                               "join mode waits for RELAY_ENABLED; "
                               "creator normally triggers P2P_FAILED")

        elif msg_type == "RELAY_ENABLED":
            self.relay_token = payload.get("relay_token")
            self.relay_ip = payload.get("relay_ip")
            self.relay_port = payload.get("relay_port")

            # Resolve actual relay target (old L256-258)
            resolved_host, fallback_reason = self._resolve_relay_host(
                self.relay_ip, self.config.host
            )
            self._relay_target_host = resolved_host
            self._relay_target_port = (
                self.relay_port
                if isinstance(self.relay_port, int) and self.relay_port > 0
                else self.config.udp_port
            )

            if fallback_reason:
                self._emit(EVT_RELAY_FALLBACK,
                           f"{fallback_reason}, falling back to {self.config.host}")

            self._emit(EVT_RELAY_ENABLED, "RELAY_ENABLED received", {
                "relay_token": self.relay_token,
                "relay_ip": self.relay_ip,
                "relay_port": self.relay_port,
                "relay_target_host": self._relay_target_host,
                "relay_target_port": self._relay_target_port,
            })
            self._relay_enabled_event.set()

        elif msg_type == "HEARTBEAT":
            pass

        elif msg_type == "ERROR":
            code = payload.get("code")
            msg = payload.get("message")
            self._emit(EVT_ERROR, f"Received error code {code}: {msg}", {
                "code": code,
                "message": msg,
            })
            self.has_error = True
            # Unblock all waiters so run() can exit (old L269-272)
            self._room_created_event.set()
            self._room_joined_event.set()
            self._peer_info_event.set()
            self._relay_enabled_event.set()

    # ===================================================================
    # Heartbeat loop (old L274-292)
    # ===================================================================
    async def _heartbeat_loop(self):
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if self._tcp_writer and not self._tcp_writer.is_closing():
                    try:
                        await self._send_tcp(self._build_heartbeat())
                    except (ConnectionError, OSError):
                        break
                else:
                    break
        except asyncio.CancelledError:
            pass

    # ===================================================================
    # Send P2P_FAILED (old L173-187)
    # ===================================================================
    async def _send_p2p_failed(self, reason: str):
        if not self.room_id or not self._tcp_writer:
            return
        try:
            await self._send_tcp(self._build_p2p_failed(reason))
            self._emit(EVT_P2P_FAILED_SENT, "P2P_FAILED sent", {"reason": reason})
        except (ConnectionError, OSError) as e:
            self._emit(EVT_ERROR, f"Failed to send P2P_FAILED: {e}")

    # ===================================================================
    # Send UDP REG (old L158-171)
    # ===================================================================
    def _send_udp_register(self):
        if not self.player_id or not self.room_id:
            return
        try:
            self._send_udp_to_server(self._build_udp_reg())
            self._emit(EVT_UDP_REG_SENT, "UDP REG sent")
        except OSError as e:
            self._emit(EVT_ERROR, f"UDP REG send failed: {e}")

    # ===================================================================
    # Test sender (old L294-325)
    # ===================================================================
    async def _run_test_sender(self):
        seq = 0
        start_time = time.perf_counter()
        interval = 1.0 / self.config.pps

        try:
            while time.perf_counter() - start_time < self.config.duration:
                now = time.perf_counter()
                # 4B seq + 8B timestamp (perf_counter as double)
                header_payload = struct.pack(">Id", seq, now)
                padding_len = max(0, self.config.packet_size - len(header_payload))
                binary_payload = header_payload + b'\x00' * padding_len

                packet = self._build_relay_packet(binary_payload)
                if self._udp_transport and self._relay_target_host and self._relay_target_port:
                    try:
                        self._send_udp_to_relay(packet)
                        self.sent_packets[seq] = now
                        self.packets_sent_count += 1
                    except OSError as e:
                        self._emit(EVT_ERROR, f"Test send failed: {e}")

                seq += 1
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # ===================================================================
    # Stats (old L327-354)
    # ===================================================================
    def _get_stats(self) -> dict:
        """Return current statistics as a dict."""
        if self.config.send_test:
            sent = self.packets_sent_count
            received = self.packets_received_count
            loss_rate = 0.0
            if sent > 0:
                loss_rate = ((sent - received) / sent) * 100.0
            rtts = list(self.received_echoes.values())
            avg_rtt_ms = 0.0
            if rtts:
                avg_rtt_ms = (sum(rtts) / len(rtts)) * 1000.0
            return {
                "mode": "sender",
                "packets_sent": sent,
                "echo_packets_received": received,
                "loss_rate": loss_rate,
                "avg_rtt_ms": avg_rtt_ms,
            }
        else:
            return {
                "mode": "responder",
                "packets_received": self.packets_received_count,
                "packets_echoed": self.packets_echoed_count,
            }

    async def _stats_reporter(self):
        """Periodically emit stats events. (old L327-333)"""
        try:
            while True:
                await asyncio.sleep(1.0)
                stats = self._get_stats()
                self._emit(EVT_TEST_STATS, self._format_stats(stats), stats)
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _format_stats(stats: dict) -> str:
        """Format stats dict into human-readable string."""
        if stats.get("mode") == "sender":
            return (
                f"packets sent: {stats['packets_sent']}, "
                f"echo received: {stats['echo_packets_received']}, "
                f"loss: {stats['loss_rate']:.2f}%, "
                f"avg RTT: {stats['avg_rtt_ms']:.2f} ms"
            )
        else:
            return (
                f"packets received: {stats['packets_received']}, "
                f"packets echoed: {stats['packets_echoed']}"
            )

    # ===================================================================
    # Main lifecycle: run() — mirrors old CLIClient.start() exactly
    # ===================================================================
    async def run(self):
        """
        Full lifecycle. Mirrors old CLIClient.start() flow:
        1. TCP connect
        2. CREATE_ROOM or JOIN_ROOM
        3. Bind UDP + send REG ×N
        4. Wait PEER_INFO
        5. Wait RELAY_ENABLED
        6. Relay test or echo responder
        """
        # --- Step 1: TCP connect (old L358-364) ---
        try:
            self._tcp_reader, self._tcp_writer = await asyncio.open_connection(
                self.config.host, self.config.port
            )
            self._emit(EVT_TCP_CONNECTED, "TCP connected")
        except (ConnectionError, OSError) as e:
            self._emit(EVT_ERROR, f"Failed to connect to server "
                       f"{self.config.host}:{self.config.port}: {e}")
            self.has_error = True
            return

        # --- Step 2: Start background tasks (old L366-367) ---
        self._tcp_receiver_task = asyncio.create_task(self._receive_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # --- Step 3: CREATE or JOIN (old L370-411) ---
        if self.config.role == "create":
            try:
                await self._send_tcp(self._build_create_room())
            except (ConnectionError, OSError) as e:
                self._emit(EVT_ERROR, f"Failed to send CREATE_ROOM: {e}")
                return

            try:
                await asyncio.wait_for(
                    self._room_created_event.wait(), timeout=ROOM_WAIT_TIMEOUT
                )
            except asyncio.TimeoutError:
                self._emit(EVT_TIMEOUT,
                           f"ROOM_CREATED not received within {ROOM_WAIT_TIMEOUT}s")
                return
            if self.has_error or not self.room_id:
                self._emit(EVT_ERROR, "Failed to create room.")
                return

        elif self.config.role == "join":
            self.room_id = self.config.room_id
            try:
                await self._send_tcp(self._build_join_room())
            except (ConnectionError, OSError) as e:
                self._emit(EVT_ERROR, f"Failed to send JOIN_ROOM: {e}")
                return

            try:
                await asyncio.wait_for(
                    self._room_joined_event.wait(), timeout=ROOM_WAIT_TIMEOUT
                )
            except asyncio.TimeoutError:
                self._emit(EVT_TIMEOUT,
                           f"ROOM_JOINED not received within {ROOM_WAIT_TIMEOUT}s")
                return
            if self.has_error or not self.player_id:
                self._emit(EVT_ERROR, "Failed to join room.")
                return

        # --- Step 4: Bind UDP + send REG (old L414-423) ---
        loop = asyncio.get_running_loop()
        self._udp_transport, self._udp_protocol = await loop.create_datagram_endpoint(
            lambda: _CoreUDPProtocol(self),
            local_addr=('0.0.0.0', 0),
        )

        for _ in range(self.config.udp_reg_count):
            self._send_udp_register()
            await asyncio.sleep(self.config.udp_reg_interval)

        # --- Step 5: Wait PEER_INFO (old L426-438) ---
        lobby_timeout = self.config.lobby_timeout
        if lobby_timeout == 0:
            await self._peer_info_event.wait()
        else:
            try:
                await asyncio.wait_for(
                    self._peer_info_event.wait(), timeout=lobby_timeout
                )
            except asyncio.TimeoutError:
                self._emit(EVT_TIMEOUT,
                           f"PEER_INFO not received within {lobby_timeout}s")
                return
        if self.has_error:
            self._emit(EVT_ERROR, "Error during PEER_INFO wait.")
            return

        # --- Step 6: Wait RELAY_ENABLED (old L441-448) ---
        try:
            await asyncio.wait_for(
                self._relay_enabled_event.wait(), timeout=RELAY_ENABLED_TIMEOUT
            )
        except asyncio.TimeoutError:
            self._emit(EVT_TIMEOUT,
                       f"RELAY_ENABLED not received within {RELAY_ENABLED_TIMEOUT}s")
            return
        if self.has_error or not self.relay_token:
            self._emit(EVT_ERROR, "Failed to switch to Relay mode.")
            return

        # --- Step 7: Relay test / echo (old L451-472) ---
        if self.config.is_payload_mode:
            self._emit(EVT_INFO, "Payload mode active. Core is ready for raw data transmission.")
            while True:
                await asyncio.sleep(3600)
        elif self.config.send_test:
            if self.config.start_delay > 0:
                self._emit(EVT_INFO,
                           f"Waiting {self.config.start_delay:.2f}s before "
                           f"sending test packets...")
                await asyncio.sleep(self.config.start_delay)
            self._emit(EVT_TEST_STARTED, "Starting active test sender...")
            self._stats_reporter_task = asyncio.create_task(self._stats_reporter())
            self._test_sender_task = asyncio.create_task(self._run_test_sender())
            # Wait for sending task to complete
            await self._test_sender_task
            # Wait 1s for any last echoes (same as old code L461)
            await asyncio.sleep(1.0)
            final_stats = self._get_stats()
            self._emit(EVT_TEST_COMPLETED, "Test completed.",
                       final_stats)
            if self.config.keep_open_after_test:
                self._emit(EVT_INFO,
                           "Test completed. Keeping room open. Press Ctrl+C to leave.")
                while True:
                    await asyncio.sleep(3600)
        else:
            self._emit(EVT_INFO,
                       "Active echo responder mode. Press Ctrl+C to exit.")
            self._stats_reporter_task = asyncio.create_task(self._stats_reporter())
            while True:
                await asyncio.sleep(3600)

    # ===================================================================
    # Cleanup — mirrors old CLIClient.cleanup() exactly (old L474-510)
    # ===================================================================
    async def close(self):
        """Clean up all resources. Safe to call multiple times."""
        # C1: Cancel running tasks (old L476-478)
        all_tasks = [
            self._tcp_receiver_task,
            self._heartbeat_task,
            self._test_sender_task,
            self._stats_reporter_task,
        ]
        for task in all_tasks:
            if task and not task.done():
                task.cancel()

        # C2: Gather cancelled tasks to suppress CancelledError (old L481-483)
        tasks_to_await = [t for t in all_tasks if t and not t.done()]
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)

        # C3: Send LEAVE_ROOM if possible (old L486-494)
        if self.room_id and self._tcp_writer and not self._tcp_writer.is_closing():
            try:
                self._tcp_writer.write(
                    self._build_leave_room().encode('utf-8')
                )
                await asyncio.wait_for(self._tcp_writer.drain(), timeout=1.0)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass

        # C4: Close UDP transport (old L497-501)
        if self._udp_transport:
            try:
                self._udp_transport.close()
            except OSError:
                pass

        # C5: Close TCP connection (old L504-510)
        if self._tcp_writer:
            try:
                if not self._tcp_writer.is_closing():
                    self._tcp_writer.close()
                await asyncio.wait_for(self._tcp_writer.wait_closed(), timeout=2.0)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                pass

        self._emit(EVT_CLEANUP, "Resources cleaned up")
