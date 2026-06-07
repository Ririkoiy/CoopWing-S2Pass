import base64
import inspect
import json
import socket
import time
import unittest

from adapters.profile import GameProfile
from adapters.transport import make_fake_pair
from adapters.udp_broadcast_forward_adapter import GenericUdpBroadcastForwardAdapter
import adapters.udp_broadcast_forward_adapter as broadcast_module


def reserve_udp_port(host="127.0.0.1"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def make_profile(bind_port, target_port):
    return GameProfile(
        profile_id="udp_broadcast_forward",
        display_name="UDP Broadcast Forward",
        exe_path="",
        local_bind_host="127.0.0.1",
        local_bind_port=bind_port,
        remote_target_host="127.0.0.1",
        remote_target_port=target_port,
    )


def wait_for(predicate, timeout=2.0):
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make_remote_envelope(packet_id, payload, target_port, origin_id="remote", hop_count=1):
    return json.dumps(
        {
            "adapter": "udp_broadcast_forward",
            "kind": "broadcast_packet",
            "origin_id": origin_id,
            "packet_id": packet_id,
            "hop_count": hop_count,
            "target_port": target_port,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class TestGenericUdpBroadcastForwardAdapter(unittest.TestCase):
    def test_start_stop_and_stop_idempotency(self):
        bind_port = reserve_udp_port()
        target_port = reserve_udp_port()
        t1, _ = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)

        adapter.stop()
        adapter.start()
        self.addCleanup(adapter.stop)
        self.assertTrue(adapter.is_running())
        self.assertEqual(adapter.get_local_addr(), ("127.0.0.1", bind_port))

        adapter.start()
        self.assertTrue(adapter.is_running())
        adapter.stop()
        self.assertFalse(adapter.is_running())
        adapter.stop()
        self.assertFalse(adapter.is_running())

    def test_local_udp_packet_is_forwarded_to_transport(self):
        bind_port = reserve_udp_port()
        target_port = reserve_udp_port()
        t1, t2 = make_fake_pair()
        captured = []
        t2.set_receive_callback(captured.append)

        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1, origin_id="local-a")
        adapter.start()
        self.addCleanup(adapter.stop)

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(client.close)
        payload = b"LAN_DISCOVERY_QUERY"
        client.sendto(payload, ("127.0.0.1", bind_port))

        self.assertTrue(wait_for(lambda: len(captured) == 1))
        envelope = json.loads(captured[0].decode("utf-8"))
        self.assertEqual(envelope["adapter"], "udp_broadcast_forward")
        self.assertEqual(envelope["kind"], "broadcast_packet")
        self.assertEqual(envelope["origin_id"], "local-a")
        self.assertEqual(envelope["hop_count"], 1)
        self.assertEqual(envelope["target_port"], target_port)
        self.assertEqual(base64.b64decode(envelope["payload_b64"]), payload)
        self.assertNotIn("type", envelope)
        self.assertNotIn("room_id", envelope)
        self.assertNotIn("relay_token", envelope)

    def test_transport_packet_is_sent_to_configured_local_target(self):
        bind_port = reserve_udp_port()
        target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target.bind(("127.0.0.1", 0))
        target.settimeout(1.0)
        self.addCleanup(target.close)
        _, target_port = target.getsockname()

        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)
        adapter.start()
        self.addCleanup(adapter.stop)

        payload = b"LAN_DISCOVERY_RESPONSE"
        t2.send(make_remote_envelope("packet-1", payload, target_port))

        data, addr = target.recvfrom(2048)
        self.assertEqual(data, payload)
        self.assertEqual(addr, ("127.0.0.1", bind_port))

    def test_payload_is_preserved_byte_for_byte(self):
        bind_port = reserve_udp_port()
        target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target.bind(("127.0.0.1", 0))
        target.settimeout(1.0)
        self.addCleanup(target.close)
        _, target_port = target.getsockname()

        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)
        adapter.start()
        self.addCleanup(adapter.stop)

        payload = bytes(range(256)) + b"\x00\xff\x10LAN"
        t2.send(make_remote_envelope("packet-bytes", payload, target_port))

        data, _ = target.recvfrom(2048)
        self.assertEqual(data, payload)

    def test_oversize_local_packet_is_not_forwarded(self):
        bind_port = reserve_udp_port()
        target_port = reserve_udp_port()
        t1, t2 = make_fake_pair()
        captured = []
        t2.set_receive_callback(captured.append)

        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)
        adapter.start()
        self.addCleanup(adapter.stop)

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(client.close)
        client.sendto(b"x" * 1501, ("127.0.0.1", bind_port))

        self.assertTrue(wait_for(lambda: adapter.get_stats()["dropped_oversize_packets"] == 1))
        self.assertEqual(captured, [])

    def test_invalid_envelope_does_not_crash(self):
        bind_port = reserve_udp_port()
        target_port = reserve_udp_port()
        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)
        adapter.start()
        self.addCleanup(adapter.stop)

        t2.send(b"not-json")
        t2.send(json.dumps({"adapter": "udp_broadcast_forward", "type": "CREATE_ROOM"}).encode("utf-8"))

        self.assertTrue(adapter.is_running())
        self.assertEqual(adapter.get_stats()["dropped_invalid_envelopes"], 2)

    def test_hop_count_over_limit_is_dropped(self):
        bind_port = reserve_udp_port()
        target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target.bind(("127.0.0.1", 0))
        target.settimeout(0.1)
        self.addCleanup(target.close)
        _, target_port = target.getsockname()

        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1, max_hop_count=1)
        adapter.start()
        self.addCleanup(adapter.stop)

        t2.send(make_remote_envelope("packet-hop", b"drop-me", target_port, hop_count=2))

        self.assertEqual(adapter.get_stats()["dropped_hop_limit"], 1)
        with self.assertRaises(socket.timeout):
            target.recvfrom(2048)

    def test_self_origin_is_dropped(self):
        bind_port = reserve_udp_port()
        target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target.bind(("127.0.0.1", 0))
        target.settimeout(0.1)
        self.addCleanup(target.close)
        _, target_port = target.getsockname()

        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1, origin_id="self-origin")
        adapter.start()
        self.addCleanup(adapter.stop)

        t2.send(make_remote_envelope("packet-self", b"drop-me", target_port, origin_id="self-origin"))

        self.assertEqual(adapter.get_stats()["dropped_self_origin"], 1)
        with self.assertRaises(socket.timeout):
            target.recvfrom(2048)

    def test_repeated_packet_id_is_dropped_temporarily(self):
        bind_port = reserve_udp_port()
        target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target.bind(("127.0.0.1", 0))
        target.settimeout(1.0)
        self.addCleanup(target.close)
        _, target_port = target.getsockname()

        t1, t2 = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1)
        adapter.start()
        self.addCleanup(adapter.stop)

        envelope = make_remote_envelope("packet-repeat", b"once-only", target_port)
        t2.send(envelope)
        t2.send(envelope)

        data, _ = target.recvfrom(2048)
        self.assertEqual(data, b"once-only")
        target.settimeout(0.1)
        with self.assertRaises(socket.timeout):
            target.recvfrom(2048)
        self.assertEqual(adapter.get_stats()["dropped_recent_packet"], 1)

    def test_envelope_does_not_contain_core_protocol_fields_or_message_types(self):
        bind_port = reserve_udp_port()
        target_port = reserve_udp_port()
        t1, _ = make_fake_pair()
        adapter = GenericUdpBroadcastForwardAdapter(make_profile(bind_port, target_port), t1, origin_id="local-only")

        envelope = json.loads(adapter.build_envelope(b"payload").decode("utf-8"))
        forbidden_fields = {
            "type",
            "room_id",
            "player_id",
            "relay_token",
            "relay_ip",
            "relay_port",
        }
        forbidden_values = {
            "CREATE_ROOM",
            "JOIN_ROOM",
            "RELAY_ENABLED",
        }
        self.assertFalse(forbidden_fields.intersection(envelope.keys()))
        self.assertFalse(forbidden_values.intersection(str(value) for value in envelope.values()))

    def test_adapter_does_not_import_core_backend_or_flutter(self):
        source = inspect.getsource(broadcast_module)
        self.assertNotIn("network_core", source)
        self.assertNotIn("server.py", source)
        self.assertNotIn("backend", source)
        self.assertNotIn("flutter", source.lower())


if __name__ == "__main__":
    unittest.main()
