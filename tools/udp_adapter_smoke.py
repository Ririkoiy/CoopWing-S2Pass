import sys
import os
import time
import socket
import argparse

# Ensure workspace root is in sys.path so we can import adapters
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.profile import GameProfile
from adapters.udp_adapter import GenericUdpForwardAdapter

def run_adapter_echo(args):
    profile = GameProfile(
        profile_id="smoke_udp_echo",
        display_name="UDP Adapter Smoke Echo",
        exe_path="",
        local_bind_host=args.bind_host,
        local_bind_port=args.bind_port
    )
    
    adapter = GenericUdpForwardAdapter(profile, mode="echo")
    try:
        adapter.start()
    except Exception as e:
        print(f"Failed to start adapter: {e}", file=sys.stderr)
        sys.exit(1)
        
    actual_host, actual_port = adapter.get_local_addr()
    print(f"UDP adapter-echo listening on {actual_host}:{actual_port}")
    
    try:
        while True:
            stats = adapter.get_stats()
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"running={stats.get('running')}, "
                  f"local_host={stats.get('local_host')}, "
                  f"local_port={stats.get('local_port')}, "
                  f"received_packets={stats.get('received_packets')}, "
                  f"sent_packets={stats.get('sent_packets')}, "
                  f"received_bytes={stats.get('received_bytes')}, "
                  f"sent_bytes={stats.get('sent_bytes')}, "
                  f"last_peer_addr={stats.get('last_peer_addr')}, "
                  f"last_error={stats.get('last_error')}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received. Stopping adapter...")
    finally:
        adapter.stop()
        print("Adapter stopped cleanly.")
    return 0

def run_send(args):
    payload = args.message.encode('utf-8')
    sent_count = 0
    received_count = 0
    rtts = []
    all_match = True
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)
    
    print(f"Sending {args.count} UDP packets to {args.target_host}:{args.target_port} with message '{args.message}'...")
    
    try:
        for seq in range(1, args.count + 1):
            if seq > 1:
                time.sleep(args.interval)
            
            start_time = time.perf_counter()
            try:
                sock.sendto(payload, (args.target_host, args.target_port))
                sent_count += 1
            except Exception as e:
                print(f"seq={seq}, sent_bytes=0, received_bytes=0, match=False, RTT=N/A, remote_addr=None, error=send_error({e})")
                all_match = False
                continue
            
            try:
                data, addr = sock.recvfrom(65535)
                end_time = time.perf_counter()
                rtt_ms = (end_time - start_time) * 1000.0
                rtts.append(rtt_ms)
                received_count += 1
                match = (data == payload)
                if not match:
                    all_match = False
                print(f"seq={seq}, sent_bytes={len(payload)}, received_bytes={len(data)}, match={match}, RTT={rtt_ms:.2f} ms, remote_addr={addr[0]}:{addr[1]}")
            except socket.timeout:
                all_match = False
                print(f"seq={seq}, sent_bytes={len(payload)}, received_bytes=0, match=False, RTT=N/A, remote_addr=None, error=timeout")
            except OSError as e:
                all_match = False
                print(f"seq={seq}, sent_bytes={len(payload)}, received_bytes=0, match=False, RTT=N/A, remote_addr=None, error={type(e).__name__}({e})")
    except KeyboardInterrupt:
        print("\nSending interrupted by user.")
    finally:
        sock.close()
        
    loss_pct = ((sent_count - received_count) / sent_count * 100.0) if sent_count > 0 else 0.0
    min_rtt = min(rtts) if rtts else 0.0
    avg_rtt = (sum(rtts) / len(rtts)) if rtts else 0.0
    max_rtt = max(rtts) if rtts else 0.0
    final_all_match = all_match and (sent_count > 0) and (received_count == sent_count)
    
    print("\n--- Summary ---")
    print(f"sent count: {sent_count}")
    print(f"received count: {received_count}")
    print(f"loss percentage: {loss_pct:.1f}%")
    print(f"min/avg/max RTT: {min_rtt:.2f}/{avg_rtt:.2f}/{max_rtt:.2f} ms")
    print(f"all_match: {final_all_match}")
    return 0 if final_all_match else 1

def main():
    parser = argparse.ArgumentParser(description="Double-ended UDP manual smoke test tool for S2Pass.")
    subparsers = parser.add_subparsers(dest="mode", required=True, help="Tool mode")
    
    # adapter-echo subcommand
    echo_parser = subparsers.add_parser("adapter-echo", help="Run GenericUdpForwardAdapter in echo mode")
    echo_parser.add_argument("--bind-host", default="0.0.0.0", help="Local host to bind (default: 0.0.0.0)")
    echo_parser.add_argument("--bind-port", type=int, default=40002, help="Local port to bind (default: 40002)")
    
    # send subcommand
    send_parser = subparsers.add_parser("send", help="Send UDP messages and wait for echo")
    send_parser.add_argument("--target-host", required=True, help="Target host IP or domain")
    send_parser.add_argument("--target-port", type=int, required=True, help="Target port")
    send_parser.add_argument("--message", default="hello-s2pass", help="Message payload to send (default: hello-s2pass)")
    send_parser.add_argument("--count", type=int, default=5, help="Number of packets to send (default: 5)")
    send_parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    send_parser.add_argument("--timeout", type=float, default=2.0, help="Timeout for waiting echo in seconds (default: 2.0)")
    
    args = parser.parse_args()
    
    if args.mode == "adapter-echo":
        return run_adapter_echo(args)
    elif args.mode == "send":
        return run_send(args)

    return 2

if __name__ == "__main__":
    sys.exit(main())
