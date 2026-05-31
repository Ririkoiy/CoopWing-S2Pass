import socket
import sys
import argparse
import time
from datetime import datetime

def parse_args(args=None):
    parser = argparse.ArgumentParser(description="UDP Game Server for S2Pass spike testing.")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="Port to bind (default: 0 for dynamic)")
    parser.add_argument("--timeout", type=float, default=None, help="Server total run timeout in seconds (default: None)")
    return parser.parse_args(args)

def run_server(host="127.0.0.1", port=0, timeout=None, stop_event=None, ready_callback=None):
    """
    Runs the UDP game server.

    Arguments:
        host: Host address to bind.
        port: Port to bind.
        timeout: Server total run timeout in seconds.
        stop_event: threading.Event to signal shutdown programmatically.
        ready_callback: callable(host, port) to invoke once server has successfully bound.
    """
    if not (0 <= port <= 65535):
        raise ValueError(f"Port must be between 0 and 65535, got {port}")
    if timeout is not None and timeout <= 0:
        raise ValueError(f"Timeout must be positive, got {timeout}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind((host, port))
    except Exception as e:
        print(f"Server bind failed on {host}:{port}: {e}", file=sys.stderr)
        sock.close()
        return 1

    actual_host, actual_port = sock.getsockname()
    print(f"UDP Server listening on {actual_host}:{actual_port}")
    sys.stdout.flush()

    if ready_callback:
        try:
            ready_callback(actual_host, actual_port)
        except Exception as e:
            print(f"Error in ready_callback: {e}", file=sys.stderr)

    # Use a small polling timeout to handle stop_event check and KeyboardInterrupt
    sock.settimeout(0.5)

    start_time = time.time()
    try:
        while True:
            if stop_event and stop_event.is_set():
                print("Server received stop event. Shutting down.")
                break

            if timeout is not None and (time.time() - start_time) > timeout:
                print(f"Server runtime exceeded timeout of {timeout}s. Exiting.")
                break

            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                # Socket might be closed externally
                break

            # Log the received packet
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            try:
                payload = data.decode("utf-8")
                log_payload = repr(payload)
                is_valid_utf8 = True
            except UnicodeDecodeError:
                log_payload = repr(data)
                is_valid_utf8 = False

            print(f"[{timestamp}] From {addr[0]}:{addr[1]} - Received: {log_payload}")
            sys.stdout.flush()

            if not is_valid_utf8:
                sock.sendto(b"ERR unknown_command", addr)
                continue

            # Simple protocol parsing
            parts = payload.strip().split(None, 1)
            if not parts:
                continue

            cmd = parts[0]
            if cmd == "JOIN":
                client_id = parts[1] if len(parts) > 1 else ""
                reply = f"WELCOME {client_id}"
                sock.sendto(reply.encode("utf-8"), addr)
            elif cmd == "PING":
                seq = parts[1] if len(parts) > 1 else ""
                reply = f"PONG {seq}"
                sock.sendto(reply.encode("utf-8"), addr)
            else:
                reply = "ERR unknown_command"
                sock.sendto(reply.encode("utf-8"), addr)

    except KeyboardInterrupt:
        print("\nServer shutting down via KeyboardInterrupt.")
    finally:
        sock.close()
        print("Server socket closed. Safe exit.")
        sys.stdout.flush()
    return 0

def main():
    args = parse_args()
    try:
        return run_server(host=args.host, port=args.port, timeout=args.timeout)
    except ValueError as e:
        print(f"Parameter error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
