import unittest
import threading
import socket
import time
from tools.udp_game_server import run_server, parse_args as server_parse_args
from tools.udp_game_client import run_client, parse_args as client_parse_args

class TestUdpMiniGameTools(unittest.TestCase):
    def test_argument_parsing(self):
        """Verify that CLI arguments parse correctly for both server and client."""
        # Server args
        s_args = server_parse_args(["--host", "127.0.0.2", "--port", "12345", "--timeout", "10.0"])
        self.assertEqual(s_args.host, "127.0.0.2")
        self.assertEqual(s_args.port, 12345)
        self.assertEqual(s_args.timeout, 10.0)

        # Client args
        c_args = client_parse_args([
            "--host", "127.0.0.3",
            "--port", "54321",
            "--client-id", "custom_id",
            "--count", "10",
            "--interval", "0.5",
            "--timeout", "1.5"
        ])
        self.assertEqual(c_args.host, "127.0.0.3")
        self.assertEqual(c_args.port, 54321)
        self.assertEqual(c_args.client_id, "custom_id")
        self.assertEqual(c_args.count, 10)
        self.assertEqual(c_args.interval, 0.5)
        self.assertEqual(c_args.timeout, 1.5)

    def test_server_client_handshake_and_ping(self):
        """Verify server/client can complete JOIN/WELCOME and PING/PONG with valid stats."""
        ready_event = threading.Event()
        stop_event = threading.Event()
        bound_addr = [None]

        def ready_callback(host, port):
            bound_addr[0] = (host, port)
            ready_event.set()

        # Start server in a background thread
        server_thread = threading.Thread(
            target=run_server,
            kwargs={
                "host": "127.0.0.1",
                "port": 0,
                "timeout": None,
                "stop_event": stop_event,
                "ready_callback": ready_callback
            }
        )
        server_thread.daemon = True
        server_thread.start()

        # Wait for server to bind
        self.assertTrue(ready_event.wait(timeout=3.0), "Server failed to bind in time")
        server_host, server_port = bound_addr[0]

        # Run client programmatically
        exit_code, stats = run_client(
            host=server_host,
            port=server_port,
            client_id="test_client",
            count=3,
            interval=0.01,
            timeout=1.0
        )

        # Verify client outcomes
        self.assertEqual(exit_code, 0, f"Client returned error exit code: {exit_code}")
        self.assertTrue(stats["joined"])
        self.assertEqual(stats["sent"], 3)
        self.assertEqual(stats["received"], 3)
        self.assertEqual(stats["lost"], 0)
        self.assertEqual(stats["loss_percent"], 0.0)
        self.assertGreater(stats["avg_rtt"], 0.0)
        self.assertEqual(len(stats["rtts"]), 3)

        # Shutdown server
        stop_event.set()
        server_thread.join(timeout=2.0)
        self.assertFalse(server_thread.is_alive())

    def test_client_join_timeout(self):
        """Verify client exits with non-zero when JOIN times out on an inactive address."""
        # Bind a temporary socket to get a guaranteed free/unbound port
        temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp_sock.bind(("127.0.0.1", 0))
        _, free_port = temp_sock.getsockname()
        temp_sock.close()

        # Run client targeting the closed port with low timeout to keep test fast
        exit_code, stats = run_client(
            host="127.0.0.1",
            port=free_port,
            client_id="timeout_client",
            count=1,
            interval=0.01,
            timeout=0.1
        )

        self.assertEqual(exit_code, 2, f"Expected exit code 2 on JOIN timeout, got {exit_code}")
        self.assertFalse(stats["joined"])
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(stats["received"], 0)

    def test_server_unknown_command(self):
        """Verify server replies ERR unknown_command for invalid commands."""
        ready_event = threading.Event()
        stop_event = threading.Event()
        bound_addr = [None]

        def ready_callback(host, port):
            bound_addr[0] = (host, port)
            ready_event.set()

        server_thread = threading.Thread(
            target=run_server,
            kwargs={
                "host": "127.0.0.1",
                "port": 0,
                "timeout": None,
                "stop_event": stop_event,
                "ready_callback": ready_callback
            }
        )
        server_thread.daemon = True
        server_thread.start()

        self.assertTrue(ready_event.wait(timeout=3.0), "Server failed to bind in time")
        server_host, server_port = bound_addr[0]

        # Send invalid command raw payload
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        try:
            sock.sendto(b"INVALID_CMD options", (server_host, server_port))
            data, addr = sock.recvfrom(1024)
            reply = data.decode("utf-8").strip()
            self.assertEqual(reply, "ERR unknown_command")
        finally:
            sock.close()

        # Shutdown server
        stop_event.set()
        server_thread.join(timeout=2.0)
        self.assertFalse(server_thread.is_alive())

    def test_server_non_utf8_payload(self):
        """Verify server replies ERR unknown_command and logs gracefully for non-UTF-8 payloads."""
        ready_event = threading.Event()
        stop_event = threading.Event()
        bound_addr = [None]

        def ready_callback(host, port):
            bound_addr[0] = (host, port)
            ready_event.set()

        server_thread = threading.Thread(
            target=run_server,
            kwargs={
                "host": "127.0.0.1",
                "port": 0,
                "timeout": None,
                "stop_event": stop_event,
                "ready_callback": ready_callback
            }
        )
        server_thread.daemon = True
        server_thread.start()

        self.assertTrue(ready_event.wait(timeout=3.0), "Server failed to bind in time")
        server_host, server_port = bound_addr[0]

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        try:
            # Send raw non-UTF-8 bytes
            sock.sendto(b"\xff\xfe\xfd", (server_host, server_port))
            data, addr = sock.recvfrom(1024)
            reply = data.decode("utf-8").strip()
            self.assertEqual(reply, "ERR unknown_command")
        finally:
            sock.close()

        # Shutdown server
        stop_event.set()
        server_thread.join(timeout=2.0)
        self.assertFalse(server_thread.is_alive())

    def test_client_invalid_params(self):
        """Verify client validates parameter ranges and returns non-zero code + stats containing error."""
        # Port 0 (invalid)
        exit_code, stats = run_client(port=0)
        self.assertEqual(exit_code, 2)
        self.assertFalse(stats["joined"])
        self.assertIn("error", stats)

        # Port out of range
        exit_code, stats = run_client(port=70000)
        self.assertEqual(exit_code, 2)
        self.assertFalse(stats["joined"])
        self.assertIn("error", stats)

        # Negative count
        exit_code, stats = run_client(port=1234, count=-1)
        self.assertEqual(exit_code, 2)
        self.assertFalse(stats["joined"])
        self.assertIn("error", stats)

        # Non-positive timeout
        exit_code, stats = run_client(port=1234, timeout=0)
        self.assertEqual(exit_code, 2)
        self.assertFalse(stats["joined"])
        self.assertIn("error", stats)

    def test_server_invalid_params(self):
        """Verify server validates parameter ranges and raises ValueError."""
        # Port out of range
        with self.assertRaises(ValueError):
            run_server(port=-1)

        with self.assertRaises(ValueError):
            run_server(port=70000)

        # Non-positive timeout
        with self.assertRaises(ValueError):
            run_server(timeout=0)
