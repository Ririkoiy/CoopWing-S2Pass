import socket
import sys
import argparse
import time

def parse_args(args=None):
    parser = argparse.ArgumentParser(description="UDP Game Client for S2Pass spike testing.")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, required=True, help="Server port")
    parser.add_argument("--client-id", default="client1", help="Client ID (default: client1)")
    parser.add_argument("--count", type=int, default=5, help="Number of PINGs (default: 5)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between PINGs in seconds (default: 1.0)")
    parser.add_argument("--timeout", type=float, default=2.0, help="Socket recv timeout in seconds (default: 2.0)")
    return parser.parse_args(args)

def run_client(host="127.0.0.1", port=None, client_id="client1", count=5, interval=1.0, timeout=2.0):
    """
    Runs the UDP game client.

    Arguments:
        host: Server host address.
        port: Server port number.
        client_id: Client identifier string.
        count: Number of PING packets to send.
        interval: Time in seconds between PING packets.
        timeout: Socket recv timeout in seconds.

    Returns:
        tuple (exit_code, stats_dict)
    """
    errors = []
    if port is None or not (1 <= port <= 65535):
        errors.append(f"Port must be between 1 and 65535, got {port}")
    if count < 0:
        errors.append(f"Count must be >= 0, got {count}")
    if interval < 0:
        errors.append(f"Interval must be >= 0, got {interval}")
    if timeout <= 0:
        errors.append(f"Timeout must be > 0, got {timeout}")

    if errors:
        err_msg = "; ".join(errors)
        print(f"Parameter error: {err_msg}", file=sys.stderr)
        return 2, {
            "error": err_msg,
            "joined": False,
            "sent": 0,
            "received": 0,
            "lost": 0,
            "loss_percent": 0.0,
            "avg_rtt": 0.0,
            "unexpected": 0
        }

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    joined = False
    join_attempts = 3

    print(f"Starting UDP Game Client '{client_id}'. Connecting to {host}:{port}...")
    sys.stdout.flush()

    for attempt in range(1, join_attempts + 1):
        print(f"Sending JOIN request to {host}:{port} (attempt {attempt}/{join_attempts})...")
        sys.stdout.flush()
        try:
            sock.sendto(f"JOIN {client_id}".encode("utf-8"), (host, port))
            data, addr = sock.recvfrom(65535)
            response = data.decode("utf-8").strip()
            if response == f"WELCOME {client_id}":
                joined = True
                print(f"Received JOIN confirmation: {response}")
                sys.stdout.flush()
                break
            else:
                print(f"Unexpected response to JOIN: {repr(response)}")
                sys.stdout.flush()
        except socket.timeout:
            print(f"JOIN attempt {attempt} timed out.")
            sys.stdout.flush()
        except Exception as e:
            print(f"Error during JOIN: {e}", file=sys.stderr)
            sys.stdout.flush()
            break

    if not joined:
        print("JOIN failed. Exiting.")
        sys.stdout.flush()
        sock.close()
        return 2, {
            "sent": 0,
            "received": 0,
            "lost": 0,
            "loss_percent": 0.0,
            "avg_rtt": 0.0,
            "joined": False,
            "unexpected": 0
        }

    sent = 0
    received = 0
    unexpected = 0
    rtts = []

    try:
        for seq in range(1, count + 1):
            if seq > 1:
                time.sleep(interval)

            ping_msg = f"PING {seq}"
            start_time = time.perf_counter()
            try:
                sock.sendto(ping_msg.encode("utf-8"), (host, port))
                sent += 1

                data, addr = sock.recvfrom(65535)
                end_time = time.perf_counter()
                rtt_ms = (end_time - start_time) * 1000.0

                try:
                    response = data.decode("utf-8").strip()
                except UnicodeDecodeError:
                    response = repr(data)

                if response == f"PONG {seq}":
                    received += 1
                    rtts.append(rtt_ms)
                    print(f"seq={seq}, RTT={rtt_ms:.2f} ms, response={repr(response)}")
                else:
                    unexpected += 1
                    print(f"seq={seq}, unexpected response={repr(response)}")
                sys.stdout.flush()
            except socket.timeout:
                print(f"seq={seq}, timed out waiting for PONG")
                sys.stdout.flush()
            except Exception as e:
                print(f"seq={seq}, send/recv error: {e}", file=sys.stderr)
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nClient interrupted by user during PING loop.")
        sys.stdout.flush()
    finally:
        sock.close()

    lost = sent - received
    loss_percent = (lost / sent * 100.0) if sent > 0 else 0.0
    avg_rtt = (sum(rtts) / len(rtts)) if rtts else 0.0

    print("\n--- Statistics ---")
    print(f"Sent: {sent}")
    print(f"Received: {received}")
    print(f"Lost: {lost}")
    print(f"Unexpected: {unexpected}")
    print(f"Loss Percent: {loss_percent:.1f}%")
    print(f"Average RTT: {avg_rtt:.2f} ms")
    sys.stdout.flush()

    stats = {
        "sent": sent,
        "received": received,
        "lost": lost,
        "loss_percent": loss_percent,
        "avg_rtt": avg_rtt,
        "joined": True,
        "unexpected": unexpected,
        "rtts": rtts
    }

    exit_code = 1 if (lost > 0 or unexpected > 0) else 0
    if lost > 0 or unexpected > 0:
        print(f"Exit status: 1 (packet loss or unexpected response detected: lost={lost}, unexpected={unexpected})")
    else:
        print("Exit status: 0 (all pings successful)")
    sys.stdout.flush()

    return exit_code, stats

def main():
    args = parse_args()
    exit_code, _ = run_client(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        count=args.count,
        interval=args.interval,
        timeout=args.timeout
    )
    return exit_code

if __name__ == "__main__":
    sys.exit(main())
