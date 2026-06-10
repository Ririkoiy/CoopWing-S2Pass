import unittest
import asyncio
from typing import Callable, Optional

from adapters.core_transport_adapter import CoreTransportAdapter

class FakeLoop:
    def __init__(self):
        self.calls = []

    def call_soon_threadsafe(self, callback: Callable, *args, **kwargs) -> None:
        self.calls.append((callback, args, kwargs))
        callback(*args, **kwargs)

class FakeCore:
    def __init__(self):
        self.payload_callback: Optional[Callable[[bytes], None]] = None
        self.sent_payloads = []
        self.payload_send_attempts = 0
        self.payload_send_bytes = 0
        self.udp_relay_send_attempts = 0
        self.udp_relay_send_bytes = 0
        self.udp_relay_send_noop_no_transport = 0
        self.udp_relay_send_noop_no_target = 0
        self.udp_relay_send_exceptions = 0
        self.last_payload_send_error = None
        self.relay_packets_received = 0
        self.relay_payload_callback_calls = 0
        self.relay_payload_callback_bytes = 0
        self.relay_drop_not_relay_prefix = 0
        self.relay_drop_invalid_header = 0
        self.relay_drop_token_mismatch = 0
        self.relay_drop_no_callback = 0
        self.last_relay_receive_error = None
        self.peer_ip = None
        self.peer_port = None

    def set_payload_callback(self, callback: Callable[[bytes], None]) -> None:
        self.payload_callback = callback

    def send_payload(self, payload: bytes) -> None:
        self.sent_payloads.append(payload)

    def inject_payload(self, payload: bytes) -> None:
        if self.payload_callback:
            self.payload_callback(payload)

class TestCoreTransportAdapter(unittest.TestCase):
    def test_constructor_registers_callback(self):
        """Constructor registers _on_core_payload with core.set_payload_callback."""
        core = FakeCore()
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)
        self.assertEqual(core.payload_callback, adapter._on_core_payload)

    def test_receive_callback_forwards_exact_bytes(self):
        """set_receive_callback stores callback and _on_core_payload forwards exact bytes."""
        core = FakeCore()
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        received = []
        def cb(payload: bytes):
            received.append(payload)

        adapter.set_receive_callback(cb)

        payload_bytes = b"hello\x00world\xff"
        core.inject_payload(payload_bytes)

        self.assertEqual(received, [payload_bytes])

    def test_callback_exceptions_not_swallowed(self):
        """Callback exceptions from receive callback are not swallowed."""
        core = FakeCore()
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        def cb(payload: bytes):
            raise ValueError("callback error")

        adapter.set_receive_callback(cb)

        with self.assertRaises(ValueError) as ctx:
            core.inject_payload(b"test")
        self.assertEqual(str(ctx.exception), "callback error")

    def test_send_schedules_via_call_soon_threadsafe(self):
        """send(payload) schedules a wrapped call through loop.call_soon_threadsafe."""
        core = FakeCore()
        loop = FakeLoop()
        loop.calls = []
        def record_only(callback, *args, **kwargs):
            loop.calls.append((callback, args, kwargs))
            # Execute immediately so we can verify core.send_payload was called
            callback(*args, **kwargs)
        loop.call_soon_threadsafe = record_only

        adapter = CoreTransportAdapter(core, loop)
        payload_bytes = b"scheduled_payload"
        adapter.send(payload_bytes)

        self.assertEqual(len(loop.calls), 1)
        _func, _args, _kwargs = loop.calls[0]
        # The wrapped callback is a local function _safe_send, not core.send_payload directly.
        # Verify core.send_payload was called with the correct payload.
        self.assertEqual(core.sent_payloads, [payload_bytes])
        # Verify diagnostic counters
        self.assertEqual(adapter.send_attempts, 1)
        self.assertEqual(adapter.send_scheduled, 1)
        self.assertEqual(adapter.send_exceptions, 0)

    def test_send_preserves_bytes_exactly(self):
        """send(payload) preserves bytes exactly."""
        core = FakeCore()
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        payload_bytes = b"exact_\x00\xff_bytes"
        adapter.send(payload_bytes)

        self.assertEqual(core.sent_payloads, [payload_bytes])

    def test_send_after_close_raises_runtime_error(self):
        """send(payload) after close raises RuntimeError."""
        core = FakeCore()
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        adapter.close()
        with self.assertRaises(RuntimeError) as ctx:
            adapter.send(b"data")
        self.assertEqual(str(ctx.exception), "Adapter is closed")

    def test_close_neutralizes_callback_and_does_not_call_core_close(self):
        """close() neutralizes local receive callback and does not close core."""
        core = FakeCore()
        core_closed = False
        def fake_close():
            nonlocal core_closed
            core_closed = True
        core.close = fake_close

        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        received = []
        adapter.set_receive_callback(received.append)

        adapter.close()

        # Injecting payload after close should not result in receive callback execution
        core.inject_payload(b"ignored")
        self.assertEqual(received, [])
        self.assertFalse(core_closed)

    def test_payload_diagnostics_include_peer_endpoint_when_core_has_peer_info(self):
        core = FakeCore()
        core.peer_ip = "198.51.100.44"
        core.peer_port = 42001
        loop = FakeLoop()
        adapter = CoreTransportAdapter(core, loop)

        diagnostics = adapter.get_payload_diagnostics()

        self.assertEqual(diagnostics["peer_endpoint"], {
            "host": "198.51.100.44",
            "port": 42001,
            "source": "core_peer_info",
        })

    def test_no_json_import_and_no_protocol_packet_construction(self):
        """CoreTransportAdapter does not import json and does not construct protocol packets."""
        import adapters.core_transport_adapter
        self.assertFalse(hasattr(adapters.core_transport_adapter, "json"))

        with open(adapters.core_transport_adapter.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("import json", content)
        self.assertNotIn("json.dumps", content)
        self.assertNotIn("json.loads", content)

    def test_no_direct_packet_building_or_udp_relay_calls(self):
        """CoreTransportAdapter does not call _build_relay_packet or _send_udp_to_relay directly."""
        import adapters.core_transport_adapter
        with open(adapters.core_transport_adapter.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("_build_relay_packet", content)
        self.assertNotIn("_send_udp_to_relay", content)
