import unittest
import threading
import time
from typing import Callable, Optional

from adapters.profile import GameProfile
from adapters.core_transport_adapter import CoreTransportAdapter
from adapters.local_udp_bridge_adapter import LocalUdpBridgeAdapter
from tools.udp_game_server import run_server
from tools.udp_game_client import run_client


class FakeLoop:
    """
    Fake event loop to schedule callbacks immediately in a threadsafe manner
    without async or timer dependency.
    """
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, callback: Callable, *args, **kwargs) -> None:
        self.calls.append((callback, args, kwargs))
        callback(*args, **kwargs)


class LinkedFakeCore:
    """
    Fake core that links directly with a peer and forwards payloads byte-for-byte
    without using JSON, network calls, or constructing protocol packets.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self.payload_callback: Optional[Callable[[bytes], None]] = None
        self.peer: Optional['LinkedFakeCore'] = None
        self.sent_payloads = []

    def set_payload_callback(self, callback: Callable[[bytes], None]) -> None:
        with self._lock:
            self.payload_callback = callback

    def send_payload(self, payload: bytes) -> None:
        with self._lock:
            self.sent_payloads.append(payload)
            peer = self.peer
        if peer:
            with peer._lock:
                callback = peer.payload_callback
            if callback:
                callback(payload)


class TestCoreTransportAdapterIntegration(unittest.TestCase):
    def test_e2e_core_transport_adapter_smoke(self):
        """
        Verify end-to-end byte-for-byte payload routing over FakeCore link:
        udp_game_client -> LocalUdpBridgeAdapter A -> CoreTransportAdapter A -> FakeCore A
        -> FakeCore B -> CoreTransportAdapter B -> LocalUdpBridgeAdapter B -> udp_game_server.
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
            stop_event.set()
            if server_thread.is_alive():
                server_thread.join(timeout=2.0)

        # Register cleanup immediately to prevent leaks.
        self.addCleanup(cleanup_server)

        self.assertTrue(ready_event.wait(timeout=3.0), "Server failed to bind in time")
        self.assertIsNotNone(bound_addr[0], "Server bound address is None")
        server_host, server_port = bound_addr[0]

        # 2. Set up Fake Loops and Linked Cores.
        loop_a = FakeLoop()
        loop_b = FakeLoop()

        core_a = LinkedFakeCore()
        core_b = LinkedFakeCore()

        core_a.peer = core_b
        core_b.peer = core_a

        # 3. Create Core Transport Adapters.
        transport_a = CoreTransportAdapter(core_a, loop_a)
        transport_b = CoreTransportAdapter(core_b, loop_b)

        # 4. Set up Local UDP Bridge Adapters.
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

        adapter_a = LocalUdpBridgeAdapter(profile_a, transport_a)
        adapter_b = LocalUdpBridgeAdapter(
            profile_b,
            transport_b,
            fixed_local_target_addr=(server_host, server_port)
        )

        # Start adapters and register their cleanup immediately.
        adapter_a.start()
        self.addCleanup(adapter_a.stop)

        adapter_b.start()
        self.addCleanup(adapter_b.stop)

        host_a, port_a = adapter_a.get_local_addr()
        self.assertIsNotNone(host_a)
        self.assertIsNotNone(port_a)
        self.assertGreater(port_a, 0)

        # 5. Run the client pointing to Adapter A's address.
        exit_code, stats = run_client(
            host=host_a,
            port=port_a,
            client_id="smoke_client",
            count=3,
            interval=0.01,
            timeout=1.0,
        )

        # 6. Verify client statistics.
        self.assertEqual(exit_code, 0, f"Client exit_code must be 0, got {exit_code}")
        self.assertTrue(stats["joined"], "Client failed to join server")
        self.assertEqual(stats["lost"], 0, "No packet loss expected")
        self.assertEqual(stats["received"], 3, "Expected exactly 3 PONG responses")
        self.assertEqual(stats.get("unexpected", 0), 0, "No unexpected responses allowed")

        # 7. Wait briefly for adapter/core counters to flush.
        expected_count = 4  # 1 JOIN + 3 PING
        start_wait = time.time()
        while time.time() - start_wait < 1.0:
            if (
                adapter_a.packets_from_game >= expected_count
                and len(core_a.sent_payloads) >= expected_count
                and len(core_b.sent_payloads) >= expected_count
                and adapter_b.packets_to_game >= expected_count
            ):
                break
            time.sleep(0.01)

        # 8. Assert adapter and core counters.
        self.assertEqual(adapter_a.packets_from_game, expected_count)
        self.assertEqual(adapter_a.packets_to_transport, expected_count)
        self.assertEqual(adapter_a.packets_from_transport, expected_count)
        self.assertEqual(adapter_a.packets_to_game, expected_count)

        self.assertEqual(adapter_b.packets_from_transport, expected_count)
        self.assertEqual(adapter_b.packets_to_game, expected_count)
        self.assertEqual(adapter_b.packets_from_game, expected_count)
        self.assertEqual(adapter_b.packets_to_transport, expected_count)

        self.assertEqual(len(core_a.sent_payloads), expected_count)
        self.assertEqual(len(core_b.sent_payloads), expected_count)

        # 9. Verify payload content integrity.
        self.assertEqual(core_a.sent_payloads, [
            b"JOIN smoke_client",
            b"PING 1",
            b"PING 2",
            b"PING 3"
        ])
        self.assertEqual(core_b.sent_payloads, [
            b"WELCOME smoke_client",
            b"PONG 1",
            b"PONG 2",
            b"PONG 3"
        ])

        # 10. Clean up server thread and assert shutdown.
        cleanup_server()
        self.assertFalse(server_thread.is_alive(), "Server thread did not stop cleanly")

    def test_static_boundaries(self):
        """Verify strict protocol isolation and import boundaries."""
        # 1. core_transport_adapter.py must not import json.
        import adapters.core_transport_adapter
        self.assertFalse(hasattr(adapters.core_transport_adapter, "json"))

        with open(adapters.core_transport_adapter.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("import json", content)
        self.assertNotIn("json.dumps", content)
        self.assertNotIn("json.loads", content)

        # 2. core_transport_adapter.py must not contain _build_relay_packet.
        self.assertNotIn("_build_relay_packet", content)

        # 3. core_transport_adapter.py must not contain _send_udp_to_relay.
        self.assertNotIn("_send_udp_to_relay", content)

        # 4. The new test file must not import the real client core class.
        # 5. The new test must not connect to the real server.
        import sys
        test_file_path = __file__
        with open(test_file_path, "r", encoding="utf-8") as f:
            test_content = f.read()
        real_core_classname = "".join(["S", "2", "P", "a", "s", "s", "C", "l", "i", "e", "n", "t", "C", "o", "r", "e"])
        self.assertNotIn(real_core_classname, test_content)
        import_server_str = " ".join(["import", "server"])
        from_server_str = " ".join(["from", "server"])
        self.assertNotIn(import_server_str, test_content)
        self.assertNotIn(from_server_str, test_content)
