import unittest
import socket
import time
from adapters.profile import GameProfile
from adapters.udp_adapter import GenericUdpForwardAdapter

class TestGenericUdpForwardAdapter(unittest.TestCase):
    def test_ephemeral_port_binding(self):
        """
        Verify that the adapter can bind to an ephemeral port (0)
        and successfully return the actual allocated host and port.
        """
        profile = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        adapter = GenericUdpForwardAdapter(profile, mode="echo")
        self.assertFalse(adapter.is_running())
        self.assertIsNone(adapter.get_pid())

        adapter.start()
        self.addCleanup(adapter.stop)
        self.assertTrue(adapter.is_running())

        host, port = adapter.get_local_addr()
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)

        # Include checking in stats
        stats = adapter.get_stats()
        self.assertTrue(stats["running"])
        self.assertEqual(stats["local_host"], "127.0.0.1")
        self.assertEqual(stats["local_port"], port)

        adapter.stop()
        self.assertFalse(adapter.is_running())
        self.assertIsNone(adapter.get_local_addr()[0])
        self.assertIsNone(adapter.get_local_addr()[1])

    def test_lifecycle_redundant_calls(self):
        """
        Verify that redundant calls to start/stop do not cause crashes.
        """
        profile = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        adapter = GenericUdpForwardAdapter(profile, mode="echo")

        # Redundant stop when not running should not crash
        adapter.stop()
        adapter.stop()

        adapter.start()
        self.addCleanup(adapter.stop)
        self.assertTrue(adapter.is_running())

        # Redundant start when running should be a no-op
        adapter.start()
        self.assertTrue(adapter.is_running())

        adapter.stop()
        self.assertFalse(adapter.is_running())

        # Redundant stop after stop
        adapter.stop()

    def test_echo_mode_raw_bytes(self):
        """
        Verify that sending raw bytes to the adapter in echo mode
        returns the exact same raw payload (no protocol or JSON wrapping).
        """
        profile = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        adapter = GenericUdpForwardAdapter(profile, mode="echo")
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()

        # Create a client socket
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(1.0)
        self.addCleanup(client.close)

        payload = b"hello-s2pass"
        client.sendto(payload, (host, port))

        data, addr = client.recvfrom(1024)
        self.assertEqual(data, payload)  # raw bytes, no wrapping

        # Poll stats to allow async receiver thread to update counters
        stats = {}
        for _ in range(50):
            stats = adapter.get_stats()
            if stats["sent_packets"] == 1:
                break
            time.sleep(0.01)

        # Verify stats and counters
        self.assertEqual(stats["received_packets"], 1)
        self.assertEqual(stats["sent_packets"], 1)
        self.assertEqual(stats["received_bytes"], len(payload))
        self.assertEqual(stats["sent_bytes"], len(payload))
        self.assertIsNone(stats["last_error"])
        self.assertEqual(stats["last_peer_addr"][0], "127.0.0.1")

    def test_port_release(self):
        """
        Verify that stopping the adapter properly releases the bound port.
        """
        profile = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        adapter = GenericUdpForwardAdapter(profile, mode="echo")
        adapter.start()

        _, port = adapter.get_local_addr()
        adapter.stop()

        # Attempt to bind to the same port with a standard socket to verify port release
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(s.close)
        try:
            s.bind(("127.0.0.1", port))
            success = True
        except Exception:
            success = False

        self.assertTrue(success)

    def test_invalid_bind_error(self):
        """
        Verify that attempting to bind to an invalid host yields a clear error.
        """
        profile = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="999.999.999.999",  # Invalid IP address
            local_bind_port=12345
        )
        adapter = GenericUdpForwardAdapter(profile, mode="echo")
        with self.assertRaises((RuntimeError, OSError)):
            adapter.start()

        self.assertFalse(adapter.is_running())

    def test_forward_mode_validation_errors(self):
        """
        Verify that constructing in forward mode checks for target host and port.
        """
        # Missing remote_target_host
        p1 = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_port=5000
        )
        with self.assertRaises(ValueError):
            GenericUdpForwardAdapter(p1, mode="forward")

        # Missing remote_target_port
        p2 = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_host="127.0.0.1"
        )
        with self.assertRaises(ValueError):
            GenericUdpForwardAdapter(p2, mode="forward")

    def test_forward_mode_self_loop_error(self):
        """
        Verify that start() raises a ValueError if the remote target is identical
        to the bound local host/port (to prevent infinite loops).
        """
        p = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=18888,
            remote_target_host="127.0.0.1",
            remote_target_port=18888
        )
        adapter = GenericUdpForwardAdapter(p, mode="forward")
        with self.assertRaises(ValueError):
            adapter.start()

        p2 = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=18889,
            remote_target_host="localhost",
            remote_target_port=18889
        )
        adapter2 = GenericUdpForwardAdapter(p2, mode="forward")
        with self.assertRaises(ValueError):
            adapter2.start()

    def test_forward_mode_success(self):
        """
        Verify that forward mode forwards raw payload exactly to the remote target.
        """
        # Set up a target mock UDP server
        mock_target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        mock_target.bind(("127.0.0.1", 0))
        mock_target.settimeout(1.0)
        self.addCleanup(mock_target.close)
        target_host, target_port = mock_target.getsockname()

        # Configure profile to forward to mock target
        p = GameProfile(
            profile_id="test_udp",
            display_name="UDP Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_host=target_host,
            remote_target_port=target_port
        )

        adapter = GenericUdpForwardAdapter(p, mode="forward")
        adapter.start()
        self.addCleanup(adapter.stop)

        host, port = adapter.get_local_addr()

        # Client sends payload to adapter local bind address
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(1.0)
        self.addCleanup(client.close)
        payload = b"hello-s2pass-forward"
        client.sendto(payload, (host, port))

        # Mock target should receive the forwarded payload
        data, addr = mock_target.recvfrom(1024)
        self.assertEqual(data, payload)

        # Poll stats to allow async receiver thread to update counters
        stats = {}
        for _ in range(50):
            stats = adapter.get_stats()
            if stats["sent_packets"] == 1:
                break
            time.sleep(0.01)

        # Verify stats counters
        self.assertEqual(stats["received_packets"], 1)
        self.assertEqual(stats["sent_packets"], 1)
        self.assertEqual(stats["received_bytes"], len(payload))
        self.assertEqual(stats["sent_bytes"], len(payload))

    def test_default_mode_is_echo(self):
        """
        Verify that:
        1. Default mode is 'echo'.
        2. No remote_target_host/port does not throw an error during constructor or start.
        3. Can start, echo payload, and stop normally.
        """
        profile = GameProfile(
            profile_id="test_udp_default",
            display_name="UDP Default Mode Test",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0
        )
        # 1. Verification of default mode
        adapter = GenericUdpForwardAdapter(profile)
        self.assertEqual(adapter.mode, "echo")

        # 2. Verification that start() does not throw
        adapter.start()
        self.addCleanup(adapter.stop)
        self.assertTrue(adapter.is_running())

        host, port = adapter.get_local_addr()

        # 3. Verification of echo capability
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(1.0)
        self.addCleanup(client.close)

        payload = b"hello-s2pass-default-echo"
        client.sendto(payload, (host, port))

        data, addr = client.recvfrom(1024)
        self.assertEqual(data, payload)

        # Wait for stats
        stats = {}
        for _ in range(50):
            stats = adapter.get_stats()
            if stats["sent_packets"] == 1:
                break
            time.sleep(0.01)

        self.assertEqual(stats["received_packets"], 1)
        self.assertEqual(stats["sent_packets"], 1)

        adapter.stop()
        self.assertFalse(adapter.is_running())
