import unittest
import socket
import time
from adapters.profile import GameProfile
from adapters.transport import make_fake_pair, FakePairTransport
from adapters.local_udp_bridge_adapter import LocalUdpBridgeAdapter

class TestLocalUdpBridgeAdapter(unittest.TestCase):
    def test_bidirectional_payload_forwarding_and_counters(self):
        """
        Verify that:
        1. A packet sent from Game A to Adapter A is forwarded to Adapter B,
           and then sent to Game B byte-for-byte.
        2. A packet sent from Game B to Adapter B is forwarded to Adapter A,
           and then sent to Game A byte-for-byte.
        3. All counters match expectations.
        """
        profile_a = GameProfile(
            profile_id="adapter_a",
            display_name="Adapter A",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        profile_b = GameProfile(
            profile_id="adapter_b",
            display_name="Adapter B",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )

        t1, t2 = make_fake_pair()
        adapter_a = LocalUdpBridgeAdapter(profile_a, t1)
        adapter_b = LocalUdpBridgeAdapter(profile_b, t2)

        adapter_a.start()
        self.addCleanup(adapter_a.stop)
        adapter_b.start()
        self.addCleanup(adapter_b.stop)

        host_a, port_a = adapter_a.get_local_addr()
        host_b, port_b = adapter_b.get_local_addr()

        # Mock game client sockets
        sock_game_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_game_a.bind(("127.0.0.1", 0))
        sock_game_a.settimeout(2.0)
        self.addCleanup(sock_game_a.close)

        sock_game_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_game_b.bind(("127.0.0.1", 0))
        sock_game_b.settimeout(2.0)
        self.addCleanup(sock_game_b.close)

        # Step 1: Warm up Game A -> Adapter A registration
        sock_game_a.sendto(b"warmup-a", (host_a, port_a))
        # Step 2: Warm up Game B -> Adapter B registration
        sock_game_b.sendto(b"warmup-b", (host_b, port_b))

        # Helper to wait deterministically for counter updates
        def wait_for_counter(adapter, attr, target_val, timeout=2.0):
            start_time = time.time()
            while time.time() - start_time < timeout:
                if getattr(adapter, attr) >= target_val:
                    return True
                time.sleep(0.01)
            return False

        # Wait until warmup packets are registered
        self.assertTrue(wait_for_counter(adapter_a, "packets_from_game", 1))
        self.assertTrue(wait_for_counter(adapter_b, "packets_from_game", 1))

        # Drain any warmup packets that might have made it through (e.g. warmup-b arriving at A)
        sock_game_a.settimeout(0.05)
        try:
            while True:
                sock_game_a.recvfrom(1024)
                sock_game_a.settimeout(0.0)
        except (BlockingIOError, socket.timeout):
            pass
        sock_game_a.settimeout(2.0)

        sock_game_b.settimeout(0.05)
        try:
            while True:
                sock_game_b.recvfrom(1024)
                sock_game_b.settimeout(0.0)
        except (BlockingIOError, socket.timeout):
            pass
        sock_game_b.settimeout(2.0)

        # Save baseline counters
        base_a_from_game = adapter_a.packets_from_game
        base_a_to_game = adapter_a.packets_to_game
        base_a_to_transport = adapter_a.packets_to_transport
        base_a_from_transport = adapter_a.packets_from_transport

        base_b_from_game = adapter_b.packets_from_game
        base_b_to_game = adapter_b.packets_to_game
        base_b_to_transport = adapter_b.packets_to_transport
        base_b_from_transport = adapter_b.packets_from_transport

        # Step 3: Game A sends payload to Adapter A -> Adapter B -> Game B
        payload_a = b"hello-from-a"
        sock_game_a.sendto(payload_a, (host_a, port_a))

        data_b, addr_b = sock_game_b.recvfrom(1024)
        self.assertEqual(data_b, payload_a)
        self.assertEqual(addr_b, (host_b, port_b))

        # Step 4: Game B sends payload to Adapter B -> Adapter A -> Game A
        payload_b = b"hello-from-b"
        sock_game_b.sendto(payload_b, (host_b, port_b))

        data_a, addr_a = sock_game_a.recvfrom(1024)
        self.assertEqual(data_a, payload_b)
        self.assertEqual(addr_a, (host_a, port_a))

        # Step 5: Verify counters update deterministically
        self.assertTrue(wait_for_counter(adapter_a, "packets_to_game", base_a_to_game + 1))
        self.assertTrue(wait_for_counter(adapter_b, "packets_to_game", base_b_to_game + 1))
        self.assertTrue(wait_for_counter(adapter_a, "packets_from_game", base_a_from_game + 1))
        self.assertTrue(wait_for_counter(adapter_b, "packets_from_game", base_b_from_game + 1))
        self.assertTrue(wait_for_counter(adapter_a, "packets_to_transport", base_a_to_transport + 1))
        self.assertTrue(wait_for_counter(adapter_b, "packets_to_transport", base_b_to_transport + 1))
        self.assertTrue(wait_for_counter(adapter_a, "packets_from_transport", base_a_from_transport + 1))
        self.assertTrue(wait_for_counter(adapter_b, "packets_from_transport", base_b_from_transport + 1))

        self.assertEqual(adapter_a.packets_from_game - base_a_from_game, 1)
        self.assertEqual(adapter_a.packets_to_game - base_a_to_game, 1)
        self.assertEqual(adapter_a.packets_to_transport - base_a_to_transport, 1)
        self.assertEqual(adapter_a.packets_from_transport - base_a_from_transport, 1)

        self.assertEqual(adapter_b.packets_from_game - base_b_from_game, 1)
        self.assertEqual(adapter_b.packets_to_game - base_b_to_game, 1)
        self.assertEqual(adapter_b.packets_to_transport - base_b_to_transport, 1)
        self.assertEqual(adapter_b.packets_from_transport - base_b_from_transport, 1)

    def test_cleanup_and_port_release(self):
        """
        Verify that stopping/cleanup releases the UDP socket.
        """
        profile = GameProfile(
            profile_id="adapter_test",
            display_name="Adapter Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        t1, _ = make_fake_pair()
        adapter = LocalUdpBridgeAdapter(profile, t1)

        adapter.start()
        _, port = adapter.get_local_addr()
        self.assertTrue(adapter.is_running())

        # Cleanup
        adapter.cleanup()
        self.assertFalse(adapter.is_running())

        # Attempt to bind to the same port to verify release
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(s.close)
        try:
            s.bind(("127.0.0.1", port))
            success = True
        except Exception:
            success = False

        self.assertTrue(success)

    def test_idempotency(self):
        """
        Verify that start, stop, and cleanup are idempotent and do not crash on redundant calls.
        """
        profile = GameProfile(
            profile_id="adapter_test",
            display_name="Adapter Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        t1, _ = make_fake_pair()
        adapter = LocalUdpBridgeAdapter(profile, t1)

        # Redundant stop when not started
        adapter.stop()
        adapter.cleanup()

        # Normal cycle
        adapter.start()
        self.assertTrue(adapter.is_running())

        # Redundant start
        adapter.start()
        self.assertTrue(adapter.is_running())

        # Multiple stops
        adapter.stop()
        self.assertFalse(adapter.is_running())
        adapter.stop()
        self.assertFalse(adapter.is_running())

        # Multiple cleanups
        adapter.cleanup()
        adapter.cleanup()

    def test_transport_exception_propagation(self):
        """
        Verify that FakePairTransport does not swallow exceptions.
        """
        t1, t2 = make_fake_pair()

        def bad_callback(payload):
            raise ValueError("Test error propagation")

        t2.set_receive_callback(bad_callback)

        with self.assertRaises(ValueError):
            t1.send(b"data")
