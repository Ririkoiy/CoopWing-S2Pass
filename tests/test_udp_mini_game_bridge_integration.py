import unittest
import threading
import time

from adapters.profile import GameProfile
from adapters.transport import make_fake_pair
from adapters.local_udp_bridge_adapter import LocalUdpBridgeAdapter
from tools.udp_game_server import run_server
from tools.udp_game_client import run_client


class TestUdpMiniGameBridgeIntegration(unittest.TestCase):
    def test_e2e_integration_smoke(self):
        """
        Verify that:
        1. udp_game_client can connect to Adapter A.
        2. Adapter A forwards to Adapter B via FakePairTransport.
        3. Adapter B forwards to udp_game_server using fixed_local_target_addr fallback.
        4. udp_game_server responds to Adapter B, which forwards back to Adapter A, then to client.
        5. Handshake (JOIN/WELCOME) and Ping/Pong loops complete successfully.
        6. Counters on both adapters increment correctly.
        7. Server, adapters, sockets, and threads are cleaned up even if assertions fail.
        """
        # 1. Start UDP mini game server in a background thread.
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
                "ready_callback": ready_callback,
            },
            daemon=True,
        )
        server_thread.start()

        def cleanup_server():
            # Idempotent cleanup: safe to call manually and again via addCleanup.
            stop_event.set()
            if server_thread.is_alive():
                server_thread.join(timeout=2.0)

        # Register cleanup immediately after the thread starts.
        # This prevents server thread leakage if any later assertion fails.
        self.addCleanup(cleanup_server)

        self.assertTrue(ready_event.wait(timeout=3.0), "Server failed to bind in time")
        self.assertIsNotNone(bound_addr[0], "Server did not report a bound address")
        server_host, server_port = bound_addr[0]

        # 2. Set up Adapters and FakePairTransport.
        profile_a = GameProfile(
            profile_id="adapter_a",
            display_name="Adapter A",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
        )
        profile_b = GameProfile(
            profile_id="adapter_b",
            display_name="Adapter B",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
        )

        t_client, t_server = make_fake_pair()

        # Adapter A: client-side local bridge.
        adapter_a = LocalUdpBridgeAdapter(profile_a, t_client)

        # Adapter B: server-side local bridge.
        # fixed_local_target_addr solves the server-side first-packet problem:
        # Adapter B may receive the client's JOIN from transport before the local
        # udp_game_server has ever sent a packet to Adapter B.
        adapter_b = LocalUdpBridgeAdapter(
            profile_b,
            t_server,
            fixed_local_target_addr=(server_host, server_port),
        )

        adapter_a.start()
        self.addCleanup(adapter_a.stop)

        adapter_b.start()
        self.addCleanup(adapter_b.stop)

        host_a, port_a = adapter_a.get_local_addr()
        self.assertIsNotNone(host_a)
        self.assertIsNotNone(port_a)
        self.assertGreater(port_a, 0)

        # 3. Run udp_game_client targeting Adapter A's local UDP address.
        exit_code, stats = run_client(
            host=host_a,
            port=port_a,
            client_id="smoke_client",
            count=3,
            interval=0.01,
            timeout=1.0,
        )

        # 4. Verify client-level outcomes.
        self.assertEqual(exit_code, 0, f"Client exit_code must be 0, got {exit_code}")
        self.assertTrue(stats["joined"], "Client failed to join server")
        self.assertEqual(stats["lost"], 0, "No packet loss allowed")
        self.assertEqual(stats["received"], 3, "Expected 3 PONG responses")
        self.assertEqual(stats.get("unexpected", 0), 0, "No unexpected responses allowed")

        # 5. Wait briefly for background adapter counters to flush.
        # Client sends: 1 JOIN + 3 PING = 4 packets.
        # Server replies: 1 WELCOME + 3 PONG = 4 packets.
        expected_packets_each_direction = 4

        start_wait = time.time()
        while time.time() - start_wait < 1.0:
            if (
                adapter_a.packets_from_game >= expected_packets_each_direction
                and adapter_a.packets_to_transport >= expected_packets_each_direction
                and adapter_a.packets_from_transport >= expected_packets_each_direction
                and adapter_a.packets_to_game >= expected_packets_each_direction
                and adapter_b.packets_from_transport >= expected_packets_each_direction
                and adapter_b.packets_to_game >= expected_packets_each_direction
                and adapter_b.packets_from_game >= expected_packets_each_direction
                and adapter_b.packets_to_transport >= expected_packets_each_direction
            ):
                break
            time.sleep(0.01)

        # 6. Verify adapter counters exactly.
        self.assertEqual(adapter_a.packets_from_game, expected_packets_each_direction)
        self.assertEqual(adapter_a.packets_to_transport, expected_packets_each_direction)
        self.assertEqual(adapter_a.packets_from_transport, expected_packets_each_direction)
        self.assertEqual(adapter_a.packets_to_game, expected_packets_each_direction)

        self.assertEqual(adapter_b.packets_from_transport, expected_packets_each_direction)
        self.assertEqual(adapter_b.packets_to_game, expected_packets_each_direction)
        self.assertEqual(adapter_b.packets_from_game, expected_packets_each_direction)
        self.assertEqual(adapter_b.packets_to_transport, expected_packets_each_direction)

        # 7. Explicit shutdown and verification.
        # addCleanup would also do this, but doing it here lets the test assert clean exit.
        cleanup_server()
        self.assertFalse(server_thread.is_alive(), "Server thread did not stop cleanly")
