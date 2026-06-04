# -*- coding: utf-8 -*-
"""Co-opWinG LAN Discovery smoke tool — manual dual-machine test helper.

Usage::

  python tools/lan_discovery_smoke.py [--name MyPC] [--timeout 60]

Prints discovered peers to stdout. Exit with Ctrl+C.
"""
from __future__ import annotations

import signal
import sys
import time

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.lan_discovery import LanDiscovery, LanDiscoveryConfig


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="LAN Discovery Smoke Tool")
    parser.add_argument("--name", default="SmokeTest",
                        help="Instance name for this node (default: SmokeTest)")
    parser.add_argument("--service-port", type=int, default=21520,
                        help="Service port to announce (default: 21520)")
    parser.add_argument("--broadcast-port", type=int, default=21521,
                        help="UDP broadcast port (default: 21521)")
    parser.add_argument("--announce-interval", type=float, default=5.0,
                        help="Announce interval in seconds (default: 5)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Total run time in seconds (default: 120)")
    parser.add_argument("--peer-timeout", type=float, default=30.0,
                        help="Peer timeout in seconds (default: 30)")
    args = parser.parse_args()

    config = LanDiscoveryConfig(
        service_port=args.service_port,
        broadcast_port=args.broadcast_port,
        announce_interval_seconds=args.announce_interval,
        peer_timeout_seconds=args.peer_timeout,
        instance_name=args.name,
        version="0.3-A1",
    )

    disco = LanDiscovery(config)
    disco.start()

    print(f"LAN Discovery started.")
    print(f"  peer_id:      {disco.peer_id}")
    print(f"  name:         {config.instance_name}")
    print(f"  broadcast:    {config.broadcast_port}")
    print(f"  announce:     {config.announce_interval_seconds}s")
    print(f"  peer timeout: {config.peer_timeout_seconds}s")
    print(f"  run time:     {args.timeout}s")
    print()
    print("Listening for peers... (Ctrl+C to stop)")
    print("-" * 60)

    def on_signal(signum, frame):
        print("\nStopping...")
        disco.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            peers = disco.get_peers()
            now_str = time.strftime("%H:%M:%S")
            print(f"[{now_str}] Peers: {len(peers)}")
            for p in peers:
                age = time.monotonic() - p.last_seen
                print(f"  - {p.name}  id={p.peer_id}  "
                      f"host={p.host}:{p.port}  ver={p.version}  "
                      f"seen={age:.1f}s ago")
            if not peers:
                print("  (none)")
            print()
            time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    finally:
        disco.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()
