#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S2Pass P2P Client (Command-Line Test Client)

Thin CLI wrapper over network_core.S2PassClientCore.
No protocol JSON construction here — all protocol logic lives in network_core.py.
"""

import asyncio
import sys

# Windows selector event loop policy - MUST be executed at the very top
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse

from network_core import (
    S2PassClientCore,
    S2PassConfig,
    S2PassEvent,
    EVT_TCP_CONNECTED,
    EVT_ROOM_CREATED,
    EVT_ROOM_JOINED,
    EVT_PEER_INFO,
    EVT_RELAY_ENABLED,
    EVT_RELAY_FALLBACK,
    EVT_UDP_REG_SENT,
    EVT_P2P_FAILED_SENT,
    EVT_ERROR,
    EVT_TIMEOUT,
    EVT_CONNECTION_LOST,
    EVT_TEST_STARTED,
    EVT_TEST_STATS,
    EVT_TEST_COMPLETED,
    EVT_CLEANUP,
    EVT_INFO,
)


def make_event_printer():
    """Returns a callback that prints S2PassEvent to stdout.

    Preserves output style compatible with old CLIClient.
    """
    def on_event(event: S2PassEvent):
        t = event.type
        msg = event.message
        data = event.data

        if t == EVT_TCP_CONNECTED:
            print("TCP connected")

        elif t == EVT_ROOM_CREATED:
            print(f"room_id: {data.get('room_id')}")
            print(f"player_id: {data.get('player_id')}")

        elif t == EVT_ROOM_JOINED:
            print(f"player_id: {data.get('player_id')}")

        elif t == EVT_PEER_INFO:
            print("PEER_INFO received, UDP registration confirmed")

        elif t == EVT_RELAY_FALLBACK:
            print(f"[Relay] {msg}")

        elif t == EVT_RELAY_ENABLED:
            print("RELAY_ENABLED received")
            print(f"relay_token: {data.get('relay_token')}")
            print(f"relay_ip from server: {data.get('relay_ip')}")
            print(f"relay_port from server: {data.get('relay_port')}")
            print(f"RELAY target: {data.get('relay_target_host')}:{data.get('relay_target_port')}")

        elif t == EVT_UDP_REG_SENT:
            print("UDP REG sent")

        elif t == EVT_P2P_FAILED_SENT:
            print("P2P_FAILED sent")

        elif t == EVT_ERROR:
            code = data.get("code")
            if code is not None:
                print(f"[ERROR] Received error code {code}: {data.get('message')}")
            else:
                print(f"[TCP] {msg}" if "Failed to send" in msg else msg)

        elif t == EVT_TIMEOUT:
            print(f"[TIMEOUT] {msg}")

        elif t == EVT_CONNECTION_LOST:
            print(f"[TCP] {msg}")

        elif t == EVT_TEST_STARTED:
            print(f"[Test] {msg}")

        elif t == EVT_TEST_STATS:
            if data.get("mode") == "sender":
                print(f"packets sent: {data['packets_sent']}")
                print(f"echo packets received: {data['echo_packets_received']}")
                print(f"loss rate: {data['loss_rate']:.2f}%")
                print(f"avg RTT: {data['avg_rtt_ms']:.2f} ms")
            else:
                print(f"packets received: {data['packets_received']}")
                print(f"packets echoed: {data['packets_echoed']}")

        elif t == EVT_TEST_COMPLETED:
            print("[Test] Test completed.")
            if data.get("mode") == "sender":
                print(f"packets sent: {data['packets_sent']}")
                print(f"echo packets received: {data['echo_packets_received']}")
                print(f"loss rate: {data['loss_rate']:.2f}%")
                print(f"avg RTT: {data['avg_rtt_ms']:.2f} ms")

        elif t == EVT_INFO:
            # Contextual prefix matching old output style
            if "Waiting" in msg and "before sending" in msg:
                print(f"[Test] {msg}")
            elif "echo responder" in msg or "Keeping room open" in msg:
                print(f"[Test] {msg}")
            else:
                print(msg)

        elif t == EVT_CLEANUP:
            pass  # silent cleanup, same as old code

    return on_event


def validate_args(args):
    """Validate argument constraints, raising SystemExit for invalid values."""
    errors = []
    if args.pps <= 0:
        errors.append("--pps must be > 0")
    if args.duration <= 0:
        errors.append("--duration must be > 0")
    if args.packet_size < 12 or args.packet_size > 1200:
        errors.append("--packet-size must be >= 12 and <= 1200")
    if args.lobby_timeout < 0:
        errors.append("--lobby-timeout must be >= 0")
    if args.start_delay < 0:
        errors.append("--start-delay must be >= 0")
    if errors:
        raise SystemExit("error: " + "; ".join(errors))


async def main():
    parser = argparse.ArgumentParser(description="S2Pass P2P Client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for cmd in ["create", "join"]:
        p = subparsers.add_parser(cmd)
        p.add_argument("--host", required=True, help="Server IP address")
        p.add_argument("--name", required=True, help="Player name")
        if cmd == "join":
            p.add_argument("--room", required=True, help="Room ID to join")
        p.add_argument("--force-relay", action="store_true",
                        help="Force Relay mode via P2P_FAILED")
        p.add_argument("--send-test", action="store_true",
                        help="Run active sending test")
        p.add_argument("--keep-open-after-test", action="store_true",
                        help="Keep room open after --send-test completes")
        p.add_argument("--lobby-timeout", type=int, default=300,
                        help="Lobby wait timeout for PEER_INFO in seconds "
                             "(0=infinite, default=300)")
        p.add_argument("--pps", type=int, default=10,
                        help="Packets per second (must be > 0)")
        p.add_argument("--duration", type=int, default=10,
                        help="Test duration in seconds (must be > 0)")
        p.add_argument("--packet-size", type=int, default=64,
                        help="Packet size in bytes (12-1200)")
        p.add_argument("--start-delay", type=float, default=1.0,
                        help="Delay before sending test packets after "
                             "RELAY_ENABLED (default=1.0)")

    args = parser.parse_args()
    validate_args(args)

    # Map argparse namespace → S2PassConfig (no protocol JSON here)
    config = S2PassConfig(
        host=args.host,
        player_name=args.name,
        room_id=getattr(args, 'room', None),
        role=args.command,
        force_relay=args.force_relay,
        lobby_timeout=args.lobby_timeout,
        start_delay=args.start_delay,
        keep_open_after_test=args.keep_open_after_test,
        send_test=args.send_test,
        pps=args.pps,
        duration=args.duration,
        packet_size=args.packet_size,
    )

    core = S2PassClientCore(config, event_callback=make_event_printer())
    try:
        await core.run()
    except KeyboardInterrupt:
        print("\nCtrl+C detected, exiting gracefully...")
    finally:
        await core.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
