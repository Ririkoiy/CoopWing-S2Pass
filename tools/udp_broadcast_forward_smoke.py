import argparse
import base64
import json
import os
import socket
import sys
import time

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure workspace root is in sys.path so this tool can run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.profile import GameProfile
from adapters.transport import Transport
from adapters.udp_broadcast_forward_adapter import GenericUdpBroadcastForwardAdapter


class PrintTransport(Transport):
    def __init__(self):
        self.callback = None
        self.sent_count = 0

    def send(self, payload: bytes) -> None:
        self.sent_count += 1
        print(f"\n[transport] envelope_bytes={len(payload)}")
        try:
            envelope = json.loads(payload.decode("utf-8"))
            payload_b64 = envelope.get("payload_b64", "")
            raw_len = len(base64.b64decode(payload_b64.encode("ascii"), validate=True)) if payload_b64 else 0
            print(
                "[transport] "
                f"adapter={envelope.get('adapter')} "
                f"kind={envelope.get('kind')} "
                f"origin_id={envelope.get('origin_id')} "
                f"packet_id={envelope.get('packet_id')} "
                f"hop_count={envelope.get('hop_count')} "
                f"target_port={envelope.get('target_port')} "
                f"payload_bytes={raw_len}"
            )
        except Exception as e:
            print(f"[transport] invalid envelope display: {e}")
            print(repr(payload))
        sys.stdout.flush()

    def set_receive_callback(self, callback):
        self.callback = callback


class LoopbackRemoteTransport(PrintTransport):
    def __init__(self, remote_origin_id):
        super().__init__()
        self.remote_origin_id = remote_origin_id

    def send(self, payload: bytes) -> None:
        super().send(payload)
        if not self.callback:
            return
        envelope = json.loads(payload.decode("utf-8"))
        envelope["origin_id"] = self.remote_origin_id
        envelope["packet_id"] = f"smoke_loop_{time.time_ns()}"
        self.callback(json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def build_profile(args):
    return GameProfile(
        profile_id="udp_broadcast_forward_smoke",
        display_name="UDP Broadcast Forward Smoke",
        exe_path="",
        local_bind_host=args.bind_host,
        local_bind_port=args.bind_port,
        remote_target_host=args.target_host,
        remote_target_port=args.target_port,
    )


def run_listen_or_bridge(args):
    print("Experimental game LAN broadcast forwarding smoke tool.")
    print("This only forwards UDP broadcast/discovery packets on the configured port.")
    print("It will not make every game automatically appear in LAN server lists.")
    print("Windows Firewall, AP isolation, and game protocol differences can still block discovery.")
    print(f"Mode={args.mode}, bind={args.bind_host}:{args.bind_port}, target={args.target_host}:{args.target_port}")
    sys.stdout.flush()

    transport = (
        LoopbackRemoteTransport(remote_origin_id=f"{args.origin_id}_remote")
        if args.mode == "bridge"
        else PrintTransport()
    )
    adapter = GenericUdpBroadcastForwardAdapter(
        build_profile(args),
        transport,
        origin_id=args.origin_id,
        max_payload_size=args.max_payload_size,
    )

    try:
        adapter.start()
    except Exception as e:
        print(f"Failed to start UDP broadcast forward adapter: {e}", file=sys.stderr)
        return 1

    actual_host, actual_port = adapter.get_local_addr()
    print(f"Adapter listening on {actual_host}:{actual_port}")
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    try:
        while True:
            stats = adapter.get_stats()
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"from_local={stats['packets_from_local']} "
                f"to_transport={stats['packets_to_transport']} "
                f"from_transport={stats['packets_from_transport']} "
                f"to_local={stats['packets_to_local']} "
                f"drop_oversize={stats['dropped_oversize_packets']} "
                f"drop_invalid={stats['dropped_invalid_envelopes']} "
                f"drop_loop={stats['dropped_local_loop']} "
                f"last_error={stats['last_error']}"
            )
            sys.stdout.flush()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping adapter...")
    finally:
        adapter.stop()
        print("Adapter stopped cleanly.")
    return 0


def run_send(args):
    payload = args.message.encode("utf-8")
    if len(payload) > args.max_payload_size:
        print(f"Payload too large: {len(payload)} > {args.max_payload_size}", file=sys.stderr)
        return 2

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        print(
            f"Sending {args.count} UDP discovery packets to "
            f"{args.target_host}:{args.target_port}; payload_bytes={len(payload)}"
        )
        for index in range(args.count):
            if index:
                time.sleep(args.interval)
            sent = sock.sendto(payload, (args.target_host, args.target_port))
            print(f"seq={index + 1}, sent_bytes={sent}")
        return 0
    finally:
        sock.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Experimental game LAN broadcast forwarding smoke tool. "
            "It forwards only the explicitly configured UDP discovery port and does not guarantee game LAN discovery."
        )
    )
    parser.add_argument("--mode", choices=("listen", "send", "bridge"), default="listen")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--bind-port", type=int, required=True)
    parser.add_argument("--target-host", default="255.255.255.255")
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--name", "--origin-id", dest="origin_id", default="udp_broadcast_smoke")
    parser.add_argument("--message", default="LAN_DISCOVERY_PROBE")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-payload-size", type=int, default=1500)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.mode == "send":
        return run_send(args)
    return run_listen_or_bridge(args)


if __name__ == "__main__":
    sys.exit(main())
