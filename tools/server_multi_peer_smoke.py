#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v0.3-E7 Server v2 Multi-Peer Relay-Only Local Smoke Tool

Verifies the v2 relay-only multi-peer main path using a real P2PServer and
real TCP/UDP sockets bound to localhost.

Flow:
  1. Start P2PServer on localhost
  2. Alice creates v2 room (max_players=4)
  3. Bob & Carol join v2
  4. Verify ROOM_CREATED, ROOM_JOINED, ROOM_UPDATED, participants snapshots
  5. All three send UDP REG
  6. Verify RELAY_ENABLED (same relay_token, locked payload keys)
  7. Alice sends RELAY game payload
  8. Verify fanout to Bob & Carol, Alice excluded, bytes identical
  9. Bob leaves → verify participant_left broadcast
  10. Host (Alice) leaves → verify room_closed broadcast
  11. Cleanup, exit 0 on success

Dependencies: Python standard library only (asyncio, json, socket, argparse).
"""

from __future__ import annotations

import asyncio
import sys
import os

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import json
import socket
import time as _time

# ── Import server module from project root ──────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PROJECT_ROOT)
import server as srv


# ══════════════════════════════════════════════════════════════════════════════
# Result tracker
# ══════════════════════════════════════════════════════════════════════════════

class Result:
    __slots__ = ("passed", "failed", "_verbose")

    def __init__(self, verbose: bool = False) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self._verbose = verbose

    def ok(self, msg: str) -> None:
        self.passed += 1
        print(f"  PASS  {msg}")

    def fail(self, msg: str, detail: str = "") -> None:
        self.failed += 1
        print(f"  FAIL  {msg}")
        if detail:
            print(f"        {detail}")

    def check(self, condition: bool, msg: str, detail: str = "") -> None:
        if condition:
            self.ok(msg)
        else:
            self.fail(msg, detail)


# ══════════════════════════════════════════════════════════════════════════════
# Lightweight smoke client (real TCP + UDP sockets, asyncio)
# ══════════════════════════════════════════════════════════════════════════════

class SmokeClient:
    """Minimal v2 client holding one TCP connection and one UDP socket."""

    __slots__ = (
        "name", "host", "tcp_port", "udp_port", "timeout",
        "reader", "writer", "udp_sock", "udp_addr",
        "player_id", "room_id", "relay_token",
        "_tcp_messages", "_udp_packets", "_loop",
    )

    def __init__(
        self,
        name: str,
        host: str,
        tcp_port: int,
        udp_port: int,
        timeout: float,
    ) -> None:
        self.name = name
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.timeout = timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.udp_sock: socket.socket | None = None
        self.udp_addr: tuple[str, int] | None = None
        self.player_id: str | None = None
        self.room_id: str | None = None
        self.relay_token: str | None = None
        self._tcp_messages: list[tuple[str, dict]] = []
        self._udp_packets: list[tuple[bytes, tuple[str, int]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── TCP ─────────────────────────────────────────────────────────────

    async def connect_tcp(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.tcp_port),
            timeout=self.timeout,
        )

    async def send_tcp(self, msg_type: str, payload: dict) -> None:
        assert self.writer is not None
        data = srv.encode_message(msg_type, payload)
        self.writer.write(data)
        await self.writer.drain()

    async def recv_tcp(self, timeout: float | None = None) -> tuple[str, dict] | None:
        assert self.reader is not None
        t = timeout if timeout is not None else self.timeout
        line = await asyncio.wait_for(self.reader.readline(), timeout=t)
        if not line:
            raise ConnectionError(f"{self.name}: TCP connection closed")
        result = srv.decode_message(line)
        if result is not None:
            self._tcp_messages.append(result)
        return result

    async def recv_tcp_until(
        self,
        msg_type: str,
        timeout: float | None = None,
    ) -> tuple[str, dict]:
        t = timeout if timeout is not None else self.timeout
        deadline = _time.monotonic() + t
        while _time.monotonic() < deadline:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            msg = await self.recv_tcp(timeout=remaining)
            if msg is not None and msg[0] == msg_type:
                return msg
        raise asyncio.TimeoutError(
            f"{self.name}: did not receive {msg_type} within {t:.1f}s"
        )

    def tcp_messages_of_type(self, msg_type: str) -> list[tuple[str, dict]]:
        return [(t, p) for t, p in self._tcp_messages if t == msg_type]

    def has_tcp_message(self, msg_type: str) -> bool:
        return any(t == msg_type for t, _ in self._tcp_messages)

    # ── UDP ─────────────────────────────────────────────────────────────

    def create_udp(self) -> None:
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setblocking(False)
        self.udp_sock.bind(("127.0.0.1", 0))
        self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp_addr = self.udp_sock.getsockname()

    def send_udp_reg(self) -> None:
        assert self.udp_sock is not None
        payload = json.dumps(
            {"player_id": self.player_id, "room_id": self.room_id},
            separators=(",", ":"),
        )
        data = b"REG\n" + payload.encode("utf-8")
        self.udp_sock.sendto(data, (self.host, self.udp_port))

    def send_udp_relay(self, game_payload: bytes) -> None:
        assert self.udp_sock is not None
        header = json.dumps(
            {"relay_token": self.relay_token, "player_id": self.player_id},
            separators=(",", ":"),
        )
        data = b"RELAY\n" + header.encode("utf-8") + b"\n" + game_payload
        self.udp_sock.sendto(data, (self.host, self.udp_port))

    async def recv_udp(self, timeout: float | None = None) -> tuple[dict, bytes, tuple[str, int]] | None:
        """Receive one RELAY UDP packet. Returns (header, game_data, addr) or None."""
        assert self.udp_sock is not None and self._loop is not None
        t = timeout if timeout is not None else (self.timeout / 2.0)
        try:
            data, addr = await asyncio.wait_for(
                self._loop.sock_recvfrom(self.udp_sock, 2048),
                timeout=t,
            )
        except asyncio.TimeoutError:
            return None
        if not data.startswith(b"RELAY\n"):
            return None
        self._udp_packets.append((data, addr))
        rest = data[6:]  # strip "RELAY\n"
        parts = rest.split(b"\n", 1)
        header = json.loads(parts[0].decode("utf-8"))
        game_data = parts[1] if len(parts) > 1 else b""
        return header, game_data, addr

    async def drain_udp(self) -> list[tuple[dict, bytes, tuple[str, int]]]:
        """Read all currently available UDP packets (non-blocking burst)."""
        packets: list[tuple[dict, bytes, tuple[str, int]]] = []
        while True:
            pkt = await self.recv_udp(timeout=0.05)
            if pkt is None:
                break
            packets.append(pkt)
        return packets

    # ── Cleanup ─────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self.writer is not None and not self.writer.is_closing():
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None
        if self.udp_sock is not None:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None

    def last_messages_summary(self, count: int = 5) -> str:
        lines: list[str] = []
        recent = self._tcp_messages[-count:]
        for t, p in recent:
            lines.append(f"  {t}: {json.dumps(p, ensure_ascii=False)[:120]}")
        if self._udp_packets:
            lines.append(f"  UDP packets received: {len(self._udp_packets)}")
        return "\n".join(lines) if lines else "  (no messages)"


# ══════════════════════════════════════════════════════════════════════════════
# Smoke scenario
# ══════════════════════════════════════════════════════════════════════════════

async def _sleep_ticks(seconds: float = 0.05) -> None:
    """Let pending asyncio tasks execute."""
    await asyncio.sleep(seconds)


async def _run_smoke_scenario(
    server: srv.P2PServer,
    args: argparse.Namespace,
    r: Result,
) -> None:
    host = args.host
    timeout = args.timeout
    payload_bytes = args.payload.encode("utf-8")

    # ── Step 1: Alice creates v2 room ───────────────────────────────────
    print("\n── Step 1: Alice creates v2 room ──")
    alice = SmokeClient("Alice", host, srv.TCP_PORT, srv.UDP_PORT, timeout)
    try:
        await alice.connect_tcp()
        alice.create_udp()

        await alice.send_tcp(srv.MSG_CREATE_ROOM, {
            "player_name": "Alice",
            "protocol_version": 2,
            "max_players": 4,
        })

        msg_type, payload = await alice.recv_tcp_until(srv.MSG_ROOM_CREATED)
        r.check(msg_type == srv.MSG_ROOM_CREATED, "Alice received ROOM_CREATED")
        r.check(
            payload.get("protocol_version") == 2,
            "ROOM_CREATED protocol_version=2",
            f"got {payload.get('protocol_version')}",
        )
        r.check(
            payload.get("max_players") == 4,
            "ROOM_CREATED max_players=4",
            f"got {payload.get('max_players')}",
        )
        participants = payload.get("participants", [])
        r.check(
            len(participants) == 1
            and participants[0].get("player_name") == "Alice"
            and participants[0].get("is_host") is True,
            "Participants snapshot: [Alice (host)]",
            f"got {participants}",
        )
        r.check(
            payload.get("participant_count") == 1,
            "participant_count=1",
            f"got {payload.get('participant_count')}",
        )

        alice.player_id = payload["player_id"]
        alice.room_id = payload["room_id"]
        print(f"        room_id={alice.room_id}  player_id={alice.player_id}")
    except Exception as exc:
        r.fail("Step 1 failed", str(exc))
        return

    # ── Step 2: Bob joins v2 ────────────────────────────────────────────
    print("\n── Step 2: Bob joins v2 room ──")
    bob = SmokeClient("Bob", host, srv.TCP_PORT, srv.UDP_PORT, timeout)
    try:
        await bob.connect_tcp()
        bob.create_udp()

        await bob.send_tcp(srv.MSG_JOIN_ROOM, {
            "room_id": alice.room_id,
            "player_name": "Bob",
            "protocol_version": 2,
        })

        msg_type, payload = await bob.recv_tcp_until(srv.MSG_ROOM_JOINED)
        r.check(msg_type == srv.MSG_ROOM_JOINED, "Bob received ROOM_JOINED")
        r.check(
            payload.get("protocol_version") == 2,
            "ROOM_JOINED protocol_version=2",
            f"got {payload.get('protocol_version')}",
        )
        r.check(
            payload.get("participant_count") == 2,
            "ROOM_JOINED participant_count=2",
            f"got {payload.get('participant_count')}",
        )
        participants = payload.get("participants", [])
        names = [p.get("player_name") for p in participants]
        r.check(
            names == ["Alice", "Bob"],
            f"Participants: {names}",
        )

        bob.player_id = payload["player_id"]
        bob.room_id = payload["room_id"]

        # Alice & Bob should receive TWO ROOM_UPDATED events:
        #  1) participant_joined (Bob joined, count=2)
        #  2) room_ready (count=2, room reached minimum)
        await alice.recv_tcp_until(srv.MSG_ROOM_UPDATED)  # participant_joined
        await alice.recv_tcp_until(srv.MSG_ROOM_UPDATED)  # room_ready
        await bob.recv_tcp_until(srv.MSG_ROOM_UPDATED)    # participant_joined
        await bob.recv_tcp_until(srv.MSG_ROOM_UPDATED)    # room_ready

        alice_joined = [m for m in alice.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
                        if m[1].get("event") == "participant_joined"]
        alice_ready = [m for m in alice.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
                       if m[1].get("event") == "room_ready"]
        bob_joined = [m for m in bob.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
                      if m[1].get("event") == "participant_joined"]
        bob_ready = [m for m in bob.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
                     if m[1].get("event") == "room_ready"]

        r.check(len(alice_joined) >= 1, "Alice received ROOM_UPDATED participant_joined")
        r.check(len(alice_ready) >= 1, "Alice received ROOM_UPDATED room_ready")
        r.check(len(bob_joined) >= 1, "Bob received ROOM_UPDATED participant_joined")
        r.check(len(bob_ready) >= 1, "Bob received ROOM_UPDATED room_ready")
    except Exception as exc:
        r.fail("Step 2 failed", str(exc))
        return

    # ── Step 3: Carol joins v2 ──────────────────────────────────────────
    print("\n── Step 3: Carol joins v2 room ──")
    carol = SmokeClient("Carol", host, srv.TCP_PORT, srv.UDP_PORT, timeout)
    try:
        await carol.connect_tcp()
        carol.create_udp()

        await carol.send_tcp(srv.MSG_JOIN_ROOM, {
            "room_id": alice.room_id,
            "player_name": "Carol",
            "protocol_version": 2,
        })

        msg_type, payload = await carol.recv_tcp_until(srv.MSG_ROOM_JOINED)
        r.check(msg_type == srv.MSG_ROOM_JOINED, "Carol received ROOM_JOINED")
        participants = payload.get("participants", [])
        names = [p.get("player_name") for p in participants]
        r.check(
            names == ["Alice", "Bob", "Carol"],
            f"Participants: {names}",
        )
        r.check(
            payload.get("participant_count") == 3,
            "participant_count=3",
            f"got {payload.get('participant_count')}",
        )
        carol.player_id = payload["player_id"]
        carol.room_id = payload["room_id"]

        # Alice & Bob should each receive one more ROOM_UPDATED participant_joined (for Carol, count=3)
        await alice.recv_tcp_until(srv.MSG_ROOM_UPDATED)
        await bob.recv_tcp_until(srv.MSG_ROOM_UPDATED)

        for client, label in [(alice, "Alice"), (bob, "Bob")]:
            all_joined = [
                m for m in client.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
                if m[1].get("event") == "participant_joined"
            ]
            r.check(
                any(m[1].get("participant_count") == 3 for m in all_joined),
                f"{label} received ROOM_UPDATED participant_joined (count=3)",
                f"got participant_joined events: {[(m[1].get('participant_count'), m[1].get('participants', [{}])[0].get('player_name') if m[1].get('participants') else '?') for m in all_joined]}",
            )
    except Exception as exc:
        r.fail("Step 3 failed", str(exc))
        return

    # ── Step 4: UDP REG → RELAY_ENABLED ─────────────────────────────────
    print("\n── Step 4: UDP REG and RELAY_ENABLED ──")
    try:
        # Send REG for Alice and Bob first (2+ registered triggers relay)
        alice.send_udp_reg()
        bob.send_udp_reg()
        await _sleep_ticks(0.3)

        # Alice & Bob should get RELAY_ENABLED
        await alice.recv_tcp_until(srv.MSG_RELAY_ENABLED)
        await bob.recv_tcp_until(srv.MSG_RELAY_ENABLED)

        alice_relay = alice.tcp_messages_of_type(srv.MSG_RELAY_ENABLED)
        bob_relay = bob.tcp_messages_of_type(srv.MSG_RELAY_ENABLED)
        r.check(len(alice_relay) == 1, "Alice received RELAY_ENABLED")
        r.check(len(bob_relay) == 1, "Bob received RELAY_ENABLED")

        # Verify payload shape
        expected_keys = {"room_id", "relay_token", "relay_ip", "relay_port"}
        alice_payload = alice_relay[0][1]
        bob_payload = bob_relay[0][1]
        r.check(
            set(alice_payload.keys()) == expected_keys,
            f"RELAY_ENABLED keys: {set(alice_payload.keys())}",
        )
        r.check(
            alice_payload["relay_token"] == bob_payload["relay_token"],
            "Alice and Bob share the same relay_token",
        )
        r.check(
            alice_payload["room_id"] == alice.room_id,
            "RELAY_ENABLED room_id matches",
        )
        r.check(
            alice_payload["relay_port"] == srv.UDP_PORT,
            f"RELAY_ENABLED relay_port={srv.UDP_PORT}",
        )

        alice.relay_token = alice_payload["relay_token"]
        bob.relay_token = alice_payload["relay_token"]

        # Carol sends REG (late join in RELAY)
        carol.send_udp_reg()
        await _sleep_ticks(0.3)

        await carol.recv_tcp_until(srv.MSG_RELAY_ENABLED)
        carol_relay = carol.tcp_messages_of_type(srv.MSG_RELAY_ENABLED)
        r.check(len(carol_relay) == 1, "Carol received RELAY_ENABLED (late join)")
        carol.relay_token = carol_relay[0][1]["relay_token"]

        r.check(
            carol.relay_token == alice.relay_token,
            "Carol shares the same relay_token",
        )
        r.check(
            set(carol_relay[0][1].keys()) == expected_keys,
            f"Carol RELAY_ENABLED keys: {set(carol_relay[0][1].keys())}",
        )
    except Exception as exc:
        r.fail("Step 4 failed", str(exc))
        # Print diagnostics
        print(f"        Alice messages: {alice.last_messages_summary()}")
        print(f"        Bob messages: {bob.last_messages_summary()}")
        print(f"        Carol messages: {carol.last_messages_summary()}")
        return

    # ── Step 5: RELAY fanout ────────────────────────────────────────────
    print("\n── Step 5: RELAY fanout ──")
    try:
        # Drain any stray UDP packets first
        await alice.drain_udp()
        await bob.drain_udp()
        await carol.drain_udp()

        alice.send_udp_relay(payload_bytes)
        await _sleep_ticks(0.3)

        # Bob and Carol should receive the RELAY packet
        bob_pkt = await bob.recv_udp(timeout=2.0)
        carol_pkt = await carol.recv_udp(timeout=2.0)

        r.check(bob_pkt is not None, "Bob received RELAY packet via UDP")
        r.check(carol_pkt is not None, "Carol received RELAY packet via UDP")

        if bob_pkt is not None:
            bob_header, bob_data, _ = bob_pkt
            r.check(
                bob_header.get("player_id") == alice.player_id,
                f"Bob RELAY header player_id={bob_header.get('player_id')} (expected Alice)",
            )
            r.check(
                bob_data == payload_bytes,
                f"Bob RELAY payload matches ({len(bob_data)} bytes)",
                f"expected {payload_bytes!r}, got {bob_data!r}",
            )

        if carol_pkt is not None:
            carol_header, carol_data, _ = carol_pkt
            r.check(
                carol_header.get("player_id") == alice.player_id,
                f"Carol RELAY header player_id={carol_header.get('player_id')} (expected Alice)",
            )
            r.check(
                carol_data == payload_bytes,
                f"Carol RELAY payload matches ({len(carol_data)} bytes)",
            )

        # Alice should NOT receive her own packet
        alice_self = await alice.recv_udp(timeout=0.5)
        r.check(
            alice_self is None,
            "Alice did NOT receive her own RELAY packet",
            f"unexpectedly received: {alice_self}",
        )
    except Exception as exc:
        r.fail("Step 5 failed", str(exc))
        return

    # ── Step 6: Bob leaves ──────────────────────────────────────────────
    print("\n── Step 6: Bob leaves ──")
    try:
        await bob.send_tcp(srv.MSG_LEAVE_ROOM, {"room_id": alice.room_id})

        # Alice & Carol should get ROOM_UPDATED participant_left
        await alice.recv_tcp_until(srv.MSG_ROOM_UPDATED)
        await carol.recv_tcp_until(srv.MSG_ROOM_UPDATED)

        alice_left = [
            m for m in alice.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
            if m[1].get("event") == "participant_left"
        ]
        carol_left = [
            m for m in carol.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
            if m[1].get("event") == "participant_left"
        ]
        r.check(len(alice_left) >= 1, "Alice received ROOM_UPDATED participant_left")
        r.check(len(carol_left) >= 1, "Carol received ROOM_UPDATED participant_left")

        # Bob should NOT receive participant_left for himself
        await _sleep_ticks(0.2)
        bob_left = [
            m for m in bob.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
            if m[1].get("event") == "participant_left"
        ]
        r.check(
            len(bob_left) == 0,
            "Bob did NOT receive participant_left for himself",
            f"got {len(bob_left)} participant_left events",
        )

        if alice_left:
            participants_after = alice_left[-1][1].get("participants", [])
            names_after = [p.get("player_name") for p in participants_after]
            r.check(
                "Bob" not in names_after,
                f"Participants after leave exclude Bob: {names_after}",
            )
            r.check(
                alice_left[-1][1].get("participant_count") == 2,
                "participant_count=2 after Bob left",
            )
    except Exception as exc:
        r.fail("Step 6 failed", str(exc))
        return

    # ── Step 7: Host (Alice) leaves → room closed ───────────────────────
    print("\n── Step 7: Host (Alice) leaves → room_closed ──")
    try:
        await alice.send_tcp(srv.MSG_LEAVE_ROOM, {"room_id": alice.room_id})

        # Carol should get ROOM_UPDATED room_closed
        await carol.recv_tcp_until(srv.MSG_ROOM_UPDATED)
        carol_closed = [
            m for m in carol.tcp_messages_of_type(srv.MSG_ROOM_UPDATED)
            if m[1].get("event") == "room_closed"
        ]
        r.check(
            len(carol_closed) >= 1,
            "Carol received ROOM_UPDATED room_closed",
        )
        if carol_closed:
            r.check(
                carol_closed[-1][1].get("room_id") == alice.room_id,
                "room_closed room_id matches",
            )
    except Exception as exc:
        r.fail("Step 7 failed", str(exc))
        return

    # ── Cleanup clients ─────────────────────────────────────────────────
    print("\n── Cleanup ──")
    for client in (alice, bob, carol):
        try:
            await client.close()
        except Exception:
            pass

    r.ok("All clients closed")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="v0.3-E7 Server v2 Multi-Peer Relay-Only Local Smoke Tool",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Server bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--advertise-host",
        default="127.0.0.1",
        help="Server advertise host for relay (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-step timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--payload",
        default="hello-from-alice",
        help="RELAY test payload (default: hello-from-alice)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--skip-leave-check",
        action="store_true",
        help="Skip leave/close checks",
    )
    parser.add_argument(
        "--keep-server-on-fail",
        action="store_true",
        help="Do not stop server on failure (for debugging)",
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    r = Result(verbose=args.verbose)

    print("=" * 60)
    print("v0.3-E7 Server v2 Multi-Peer Relay-Only Local Smoke")
    print(f"host={args.host}  advertise_host={args.advertise_host}")
    print(f"timeout={args.timeout}s  payload={args.payload!r}")
    print("=" * 60)

    # ── Start P2PServer ─────────────────────────────────────────────────
    print("\n── Starting P2PServer ──")
    server = srv.P2PServer(advertise_host=args.advertise_host)
    try:
        await server.start()
    except OSError as exc:
        print(f"\nFAIL: Could not start server on {args.host}:{srv.TCP_PORT}/{srv.UDP_PORT}")
        print(f"      {exc}")
        print("      Ensure ports 9000 (TCP) and 9001 (UDP) are free.")
        return 1

    print(f"Server listening on TCP:{srv.TCP_PORT} UDP:{srv.UDP_PORT}")
    server_task: asyncio.Task | None = None

    try:
        await _run_smoke_scenario(server, args, r)
    except Exception as exc:
        r.fail("Unhandled exception in smoke scenario", str(exc))
        if args.verbose:
            import traceback
            traceback.print_exc()
    finally:
        if not (args.keep_server_on_fail and r.failed > 0):
            print("\n── Stopping server ──")
            await server.stop()
            print("Server stopped")
        else:
            print("\n(keeping server alive for debugging — press Ctrl+C to stop)")
            while True:
                await asyncio.sleep(1)

    # ── Final summary ───────────────────────────────────────────────────
    total = r.passed + r.failed
    print(f"\n{'=' * 60}")
    print(f"RESULTS: {r.passed}/{total} passed", end="")
    if r.failed > 0:
        print(f"  ({r.failed} FAILED)")
        print("FAIL: Some tests did not pass.")
        return 1
    print()
    print("PASS: All smoke tests passed.")
    return 0


def main() -> int:
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 1


if __name__ == "__main__":
    sys.exit(main())
