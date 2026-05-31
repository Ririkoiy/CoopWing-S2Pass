import unittest
import socket
import time
import threading
from typing import Optional
from adapters.profile import GameProfile
from adapters.udp_adapter import GenericUdpForwardAdapter

class TestUdpDualInstance(unittest.TestCase):
    def setUp(self):
        self.receiver_sock: Optional[socket.socket] = None
        self.adapter: Optional[GenericUdpForwardAdapter] = None
        self.client_sock: Optional[socket.socket] = None
        
        self.received_payloads = []
        self.receiver_thread: Optional[threading.Thread] = None
        self.stop_receiver = threading.Event()

    def tearDown(self):
        # Ensure cleanup runs even if assertions fail, avoiding port leaks.
        if self.adapter:
            try:
                self.adapter.stop()
            except Exception:
                pass
        self.stop_receiver.set()
        if self.receiver_sock:
            try:
                self.receiver_sock.close()
            except Exception:
                pass
        if self.client_sock:
            try:
                self.client_sock.close()
            except Exception:
                pass
        if self.receiver_thread and self.receiver_thread.is_alive():
            self.receiver_thread.join(timeout=1.0)

    def test_udp_dual_instance_forward(self):
        """
        Verify that:
        1. A UDP payload from client -> Adapter A reaches Mock Receiver B via forward mode.
        2. Payload is byte-for-byte identical.
        3. Statistics counters increment correctly.
        4. Port is released cleanly upon stopping.
        """
        # 1. Start a mock UDP receiver B, binding to 127.0.0.1:0
        self.receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.receiver_sock.settimeout(0.5)  # Timeout to prevent infinite blocking
        self.receiver_sock.bind(("127.0.0.1", 0))
        
        # 2. Get the bound receiver port
        receiver_host, receiver_port = self.receiver_sock.getsockname()
        
        # Background receiver loop
        def receive_loop():
            while not self.stop_receiver.is_set():
                try:
                    data, addr = self.receiver_sock.recvfrom(65535)
                    self.received_payloads.append((data, addr))
                except socket.timeout:
                    continue
                except Exception:
                    break

        self.receiver_thread = threading.Thread(target=receive_loop, daemon=True)
        self.receiver_thread.start()

        # 3. Create GameProfile A targeting Mock Receiver B
        profile_a = GameProfile(
            profile_id="test_dual_instance_a",
            display_name="Dual Instance Test A",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
            remote_target_host="127.0.0.1",
            remote_target_port=receiver_port
        )

        # 4. Start GenericUdpForwardAdapter A in forward mode
        self.adapter = GenericUdpForwardAdapter(profile_a, mode="forward")
        self.adapter.start()
        
        adapter_host, adapter_port = self.adapter.get_local_addr()
        self.assertTrue(self.adapter.is_running())
        self.assertIsNotNone(adapter_port)
        self.assertGreater(adapter_port, 0)

        # 5. Send UDP payload to Adapter A local address
        self.client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client_sock.settimeout(1.0)
        
        payload = b"dual-instance-test-payload-12345"
        self.client_sock.sendto(payload, (adapter_host, adapter_port))

        # 6. Verify Mock Receiver B receives the exact same payload
        start_time = time.time()
        while len(self.received_payloads) == 0 and (time.time() - start_time) < 1.0:
            time.sleep(0.01)

        self.assertEqual(len(self.received_payloads), 1, "Mock receiver did not receive the payload")
        recv_data, recv_addr = self.received_payloads[0]
        
        # Verify payload is byte-for-byte identical
        self.assertEqual(recv_data, payload, "Payload payload byte-for-byte mismatch")

        # 7. Check adapter A stats/counters
        stats = {}
        for _ in range(50):
            stats = self.adapter.get_stats()
            if stats["sent_packets"] == 1:
                break
            time.sleep(0.01)

        self.assertEqual(stats["received_packets"], 1, "Incorrect received_packets counter")
        self.assertEqual(stats["sent_packets"], 1, "Incorrect sent_packets counter")
        self.assertEqual(stats["received_bytes"], len(payload), "Incorrect received_bytes counter")
        self.assertEqual(stats["sent_bytes"], len(payload), "Incorrect sent_bytes counter")
        self.assertIsNone(stats["last_error"], f"Unexpected last error: {stats['last_error']}")

        # 8. Stop the adapter
        self.adapter.stop()
        self.assertFalse(self.adapter.is_running())

        # 9. Verify port release
        verify_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            verify_sock.bind(("127.0.0.1", adapter_port))
            port_freed = True
        except Exception:
            port_freed = False
        finally:
            verify_sock.close()
        self.assertTrue(port_freed, "Adapter port was not released successfully after stopping")
