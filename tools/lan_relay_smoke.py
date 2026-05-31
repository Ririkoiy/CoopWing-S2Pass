#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LAN Relay Smoke Helper — replaces manual run_creator_p33a.py / run_joiner_p33a.py.

Uses S2PassClientCore (payload mode) + CoreTransportAdapter + LocalUdpBridgeAdapter.
Does NOT start server.py, udp_game_server.py, or udp_game_client.py.
Does NOT construct protocol JSON directly.

Usage:
  python tools/lan_relay_smoke.py --role create --server-host <IP> [--player-name CreatorA]
  python tools/lan_relay_smoke.py --role join --server-host <IP> --room-id <ID> [--player-name JoinerB]
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import argparse
import os

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from network_core import (
    S2PassClientCore,
    S2PassConfig,
    S2PassEvent,
    EVT_ROOM_CREATED,
    EVT_ROOM_JOINED,
    EVT_RELAY_ENABLED,
    EVT_ERROR,
    EVT_TIMEOUT,
    EVT_P2P_FAILED_SENT,
    EVT_TCP_CONNECTED,
    EVT_PEER_INFO,
    EVT_INFO,
    EVT_UDP_REG_SENT,
)
from adapters import GameProfile, CoreTransportAdapter, LocalUdpBridgeAdapter

# ---------------------------------------------------------------------------
# Bounded wait constants (seconds)
# ---------------------------------------------------------------------------
ROOM_EVENT_TIMEOUT = 15
RELAY_EVENT_TIMEOUT = 35


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="S2Pass LAN Relay Smoke Helper",
    )
    parser.add_argument(
        "--role",
        required=True,
        choices=["create", "join"],
        help="Room role: create or join",
    )
    parser.add_argument(
        "--server-host",
        required=True,
        help="S2Pass server IP or hostname",
    )
    parser.add_argument(
        "--server-port",
        type=int,
        default=9000,
        help="S2Pass server TCP port (default: 9000)",
    )
    parser.add_argument(
        "--server-udp-port",
        type=int,
        default=9001,
        help="S2Pass server UDP port (default: 9001)",
    )
    parser.add_argument(
        "--player-name",
        default="SmokePlayer",
        help="Player display name",
    )
    parser.add_argument(
        "--bind-host",
        default="127.0.0.1",
        help="Local UDP adapter bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--bind-port",
        type=int,
        default=0,
        help="Local UDP adapter bind port (default: 0 = OS-assigned)",
    )
    parser.add_argument(
        "--room-id",
        default=None,
        help="Room ID to join (required for --role join)",
    )
    parser.add_argument(
        "--game-server-host",
        default="127.0.0.1",
        help="Local game server host for join role (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--game-server-port",
        type=int,
        default=40100,
        help="Local game server port for join role (default: 40100)",
    )
    args = parser.parse_args()

    if args.role == "join" and not args.room_id:
        parser.error("--room-id is required for --role join")

    return args


def _format_counters(adapter: LocalUdpBridgeAdapter) -> str:
    return (
        f"packets_from_game={adapter.packets_from_game}, "
        f"packets_to_transport={adapter.packets_to_transport}, "
        f"packets_from_transport={adapter.packets_from_transport}, "
        f"packets_to_game={adapter.packets_to_game}"
    )


async def _run_create(args: argparse.Namespace) -> None:
    config = S2PassConfig(
        host=args.server_host,
        port=args.server_port,
        udp_port=args.server_udp_port,
        player_name=args.player_name,
        role="create",
        force_relay=True,
        is_payload_mode=True,
        send_test=False,
    )

    event_queue: asyncio.Queue[S2PassEvent] = asyncio.Queue(maxsize=256)
    core = S2PassClientCore(config, event_queue=event_queue)
    loop = asyncio.get_running_loop()

    core_task = asyncio.ensure_future(core.run())

    transport = None
    adapter = None

    try:
        # --- Wait for ROOM_CREATED ---
        try:
            while True:
                event = await asyncio.wait_for(
                    event_queue.get(), timeout=ROOM_EVENT_TIMEOUT
                )
                etype = event.type
                if etype == EVT_ROOM_CREATED:
                    print(f"[OK] ROOM_CREATED")
                    data = event.data or {}
                    room_id = data.get("room_id", "?")
                    player_id = data.get("player_id", "?")
                    print(f"     ROOM_ID: {room_id}")
                    print(f"     PLAYER_ID: {player_id}")
                    break
                elif etype == EVT_ERROR:
                    print(f"[ERROR] {event.message}")
                    return
                elif etype == EVT_TIMEOUT:
                    print(f"[TIMEOUT] {event.message}")
                    return
                elif etype in (EVT_TCP_CONNECTED, EVT_INFO):
                    print(f"[{etype}] {event.message}")
                else:
                    print(f"[{etype}] {event.message}")
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] ROOM_CREATED not received within {ROOM_EVENT_TIMEOUT}s")
            return

        # --- Wait for RELAY_ENABLED ---
        try:
            while True:
                event = await asyncio.wait_for(
                    event_queue.get(), timeout=RELAY_EVENT_TIMEOUT
                )
                etype = event.type
                if etype == EVT_RELAY_ENABLED:
                    print(f"[OK] RELAY_ENABLED")
                    data = event.data or {}
                    relay_target_host = data.get("relay_target_host", "?")
                    relay_target_port = data.get("relay_target_port", "?")
                    print(f"     relay_target: {relay_target_host}:{relay_target_port}")
                    break
                elif etype == EVT_ERROR:
                    print(f"[ERROR] {event.message}")
                    return
                elif etype == EVT_TIMEOUT:
                    print(f"[TIMEOUT] {event.message}")
                    return
                elif etype in (EVT_PEER_INFO, EVT_P2P_FAILED_SENT, EVT_UDP_REG_SENT,
                               EVT_INFO, EVT_TCP_CONNECTED):
                    print(f"[{etype}] {event.message}")
                else:
                    print(f"[{etype}] {event.message}")
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] RELAY_ENABLED not received within {RELAY_EVENT_TIMEOUT}s")
            return

        # --- Create adapters ---
        transport = CoreTransportAdapter(core, loop)
        profile = GameProfile(
            profile_id="lan_smoke_create",
            display_name="LAN Smoke Creator",
            exe_path="",
            adapter_type="local_udp_bridge",
            local_bind_host=args.bind_host,
            local_bind_port=args.bind_port,
        )
        adapter = LocalUdpBridgeAdapter(profile, transport)
        adapter.start()

        local_host, local_port = adapter.get_local_addr()
        if local_port is None:
            print("[ERROR] Adapter failed to bind")
            return

        print(f"[OK] Adapter started: {local_host}:{local_port}")
        print()
        print("=" * 60)
        print("READY — run on the OTHER machine:")
        print(f"  python tools/lan_relay_smoke.py --role join "
              f"--server-host {args.server_host} --room-id {room_id} "
              f"--game-server-host 127.0.0.1 "
              f"--game-server-port {args.game_server_port}")
        print()
        print("After join is ready, run on THIS machine:")
        print(f"  python tools/udp_game_client.py --host 127.0.0.1 "
              f"--port {local_port} --client-id smoke_lan "
              f"--count 5 --interval 1.0 --timeout 3.0")
        print("=" * 60)
        print()
        print("Press Ctrl+C to stop...")

        # --- Idle until Ctrl+C ---
        while True:
            await asyncio.sleep(3600)

    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...")
    finally:
        if adapter is not None:
            print(f"[COUNTERS] {_format_counters(adapter)}")
        else:
            print("[COUNTERS] adapter was not started")
        if adapter is not None:
            try:
                adapter.stop()
            except Exception:
                pass
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        if not core_task.done():
            core_task.cancel()
            try:
                await core_task
            except asyncio.CancelledError:
                pass
        try:
            await core.close()
        except Exception:
            pass
        print("[CLEANUP] Done")


async def _run_join(args: argparse.Namespace) -> None:
    config = S2PassConfig(
        host=args.server_host,
        port=args.server_port,
        udp_port=args.server_udp_port,
        player_name=args.player_name,
        room_id=args.room_id,
        role="join",
        force_relay=True,
        is_payload_mode=True,
        send_test=False,
    )

    event_queue: asyncio.Queue[S2PassEvent] = asyncio.Queue(maxsize=256)
    core = S2PassClientCore(config, event_queue=event_queue)
    loop = asyncio.get_running_loop()

    core_task = asyncio.ensure_future(core.run())

    transport = None
    adapter = None

    try:
        # --- Wait for ROOM_JOINED ---
        try:
            while True:
                event = await asyncio.wait_for(
                    event_queue.get(), timeout=ROOM_EVENT_TIMEOUT
                )
                etype = event.type
                if etype == EVT_ROOM_JOINED:
                    print(f"[OK] ROOM_JOINED")
                    data = event.data or {}
                    player_id = data.get("player_id", "?")
                    print(f"     PLAYER_ID: {player_id}")
                    break
                elif etype == EVT_ERROR:
                    print(f"[ERROR] {event.message}")
                    return
                elif etype == EVT_TIMEOUT:
                    print(f"[TIMEOUT] {event.message}")
                    return
                elif etype in (EVT_TCP_CONNECTED, EVT_INFO):
                    print(f"[{etype}] {event.message}")
                else:
                    print(f"[{etype}] {event.message}")
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] ROOM_JOINED not received within {ROOM_EVENT_TIMEOUT}s")
            return

        # --- Wait for RELAY_ENABLED ---
        try:
            while True:
                event = await asyncio.wait_for(
                    event_queue.get(), timeout=RELAY_EVENT_TIMEOUT
                )
                etype = event.type
                if etype == EVT_RELAY_ENABLED:
                    print(f"[OK] RELAY_ENABLED")
                    data = event.data or {}
                    relay_target_host = data.get("relay_target_host", "?")
                    relay_target_port = data.get("relay_target_port", "?")
                    print(f"     relay_target: {relay_target_host}:{relay_target_port}")
                    break
                elif etype == EVT_ERROR:
                    print(f"[ERROR] {event.message}")
                    return
                elif etype == EVT_TIMEOUT:
                    print(f"[TIMEOUT] {event.message}")
                    return
                elif etype in (EVT_PEER_INFO, EVT_UDP_REG_SENT, EVT_INFO,
                               EVT_TCP_CONNECTED):
                    print(f"[{etype}] {event.message}")
                else:
                    print(f"[{etype}] {event.message}")
        except asyncio.TimeoutError:
            print(f"[TIMEOUT] RELAY_ENABLED not received within {RELAY_EVENT_TIMEOUT}s")
            return

        # --- Create adapters ---
        fixed_target = (args.game_server_host, args.game_server_port)
        transport = CoreTransportAdapter(core, loop)
        profile = GameProfile(
            profile_id="lan_smoke_join",
            display_name="LAN Smoke Joiner",
            exe_path="",
            adapter_type="local_udp_bridge",
            local_bind_host=args.bind_host,
            local_bind_port=args.bind_port,
        )
        adapter = LocalUdpBridgeAdapter(
            profile, transport, fixed_local_target_addr=fixed_target
        )
        adapter.start()

        local_host, local_port = adapter.get_local_addr()
        if local_port is None:
            print("[ERROR] Adapter failed to bind")
            return

        print(f"[OK] Adapter started: {local_host}:{local_port}")
        print(f"     fixed_local_target: {fixed_target[0]}:{fixed_target[1]}")
        print()
        print("=" * 60)
        print("READY — relay path established.")
        print("The udp_game_client on the creator machine can now send")
        print("PING packets through the relay to udp_game_server.")
        print("=" * 60)
        print()
        print("Press Ctrl+C to stop...")

        # --- Idle until Ctrl+C ---
        while True:
            await asyncio.sleep(3600)

    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C received, shutting down...")
    finally:
        if adapter is not None:
            print(f"[COUNTERS] {_format_counters(adapter)}")
        else:
            print("[COUNTERS] adapter was not started")
        if adapter is not None:
            try:
                adapter.stop()
            except Exception:
                pass
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        if not core_task.done():
            core_task.cancel()
            try:
                await core_task
            except asyncio.CancelledError:
                pass
        try:
            await core.close()
        except Exception:
            pass
        print("[CLEANUP] Done")


async def _main_async() -> None:
    args = _parse_args()

    if args.role == "create":
        await _run_create(args)
    else:
        await _run_join(args)


def main() -> None:
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
