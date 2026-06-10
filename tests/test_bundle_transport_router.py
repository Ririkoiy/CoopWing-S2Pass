# -*- coding: utf-8 -*-
"""Tests for adapters.bundle_transport_router and end-to-end relay paths."""
from __future__ import annotations

import json
import socket
import threading
import time
import unittest

from adapters.bundle_transport_router import (
    BundleTransportRouter,
    RoutedTransport,
    _extract_adapter_namespace,
)
from adapters.transport import FakePairTransport, make_fake_pair
from adapters.tcp_relay_adapter import TcpRelayAdapter, encode_tcp_relay_frame
from adapters.udp_broadcast_forward_adapter import GenericUdpBroadcastForwardAdapter
from adapters.udp_raw_bridge_adapter import (
    ADAPTER_NAME as UDP_RAW_BRIDGE_ADAPTER_NAME,
    UdpRawBridgeAdapter,
    encode_udp_raw_bridge_frame,
)
from adapters.profile import GameProfile
from backend.adapter_manager import AdapterManager
from backend.models import AdapterConfig


def _profile(profile_id="test", bind_port=0, target_port=None, protocol="udp"):
    return GameProfile(
        profile_id=profile_id,
        display_name=profile_id,
        exe_path="",
        adapter_type="test",
        protocol=protocol,
        local_bind_host="127.0.0.1",
        local_bind_port=bind_port,
        remote_target_host="127.0.0.1",
        remote_target_port=target_port,
    )


def _free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class BundleTransportRouterTests(unittest.TestCase):
    """Tests for BundleTransportRouter demux logic."""

    def test_tcp_relay_and_broadcast_callbacks_coexist(self):
        """Both adapters register callbacks through the router without
        overwriting each other."""
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        udp_received = []

        tcp_transport = router.get_transport("tcp_relay")
        udp_transport = router.get_transport("udp_broadcast_forward")
        tcp_transport.set_receive_callback(lambda p: tcp_received.append(p))
        udp_transport.set_receive_callback(lambda p: udp_received.append(p))

        # Send tcp_relay envelope through the pair
        tcp_env = json.dumps({"adapter": "tcp_relay", "kind": "open", "conn_id": "c1"}).encode()
        t2.send(tcp_env)
        self.assertEqual(len(tcp_received), 1)
        self.assertEqual(len(udp_received), 0)
        self.assertEqual(tcp_received[0], tcp_env)

        # Send broadcast envelope through the pair
        udp_env = json.dumps({"adapter": "udp_broadcast_forward", "kind": "broadcast_packet"}).encode()
        t2.send(udp_env)
        self.assertEqual(len(tcp_received), 1)
        self.assertEqual(len(udp_received), 1)
        self.assertEqual(udp_received[0], udp_env)

    def test_tcp_relay_envelope_routes_only_to_tcp_callback(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        udp_received = []
        tcp_transport = router.get_transport("tcp_relay")
        udp_transport = router.get_transport("udp_broadcast_forward")
        tcp_transport.set_receive_callback(lambda p: tcp_received.append(p))
        udp_transport.set_receive_callback(lambda p: udp_received.append(p))

        env = encode_tcp_relay_frame("open", "conn1")
        t2.send(env)

        self.assertEqual(len(tcp_received), 1)
        self.assertEqual(len(udp_received), 0)

    def test_udp_broadcast_envelope_routes_only_to_broadcast_callback(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        udp_received = []
        tcp_transport = router.get_transport("tcp_relay")
        udp_transport = router.get_transport("udp_broadcast_forward")
        tcp_transport.set_receive_callback(lambda p: tcp_received.append(p))
        udp_transport.set_receive_callback(lambda p: udp_received.append(p))

        env = json.dumps({
            "adapter": "udp_broadcast_forward",
            "kind": "broadcast_packet",
            "origin_id": "ubf_test",
            "packet_id": "ubfp_1",
            "hop_count": 1,
            "target_port": 9999,
            "payload_b64": "dGVzdA==",
        }, separators=(",", ":"), sort_keys=True).encode()
        t2.send(env)

        self.assertEqual(len(tcp_received), 0)
        self.assertEqual(len(udp_received), 1)

    def test_udp_raw_bridge_binary_frame_routes_only_to_raw_callback(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        raw_received = []
        tcp_transport = router.get_transport("tcp_relay")
        raw_transport = router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME)
        tcp_transport.set_receive_callback(lambda p: tcp_received.append(p))
        raw_transport.set_receive_callback(lambda p: raw_received.append(p))

        frame = encode_udp_raw_bridge_frame(b"raw-udp\x00bytes\xff")
        t2.send(frame)

        self.assertEqual(tcp_received, [])
        self.assertEqual(raw_received, [frame])
        self.assertEqual(router.dispatch_count, 1)
        self.assertEqual(router.unknown_namespace_count, 0)

    def test_tcp_relay_still_routes_after_binary_router_support(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        raw_received = []
        router.get_transport("tcp_relay").set_receive_callback(tcp_received.append)
        router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME).set_receive_callback(raw_received.append)

        frame = encode_tcp_relay_frame("open", "conn1")
        t2.send(frame)

        self.assertEqual(tcp_received, [frame])
        self.assertEqual(raw_received, [])
        self.assertEqual(router.dispatch_count, 1)
        self.assertEqual(router.unknown_namespace_count, 0)

    def test_tcp_relay_and_udp_raw_bridge_callbacks_coexist(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        tcp_received = []
        raw_received = []
        router.get_transport("tcp_relay").set_receive_callback(tcp_received.append)
        router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME).set_receive_callback(raw_received.append)

        tcp_frame = encode_tcp_relay_frame("open", "conn1")
        raw_frame = encode_udp_raw_bridge_frame(b"raw-frame")
        t2.send(tcp_frame)
        t2.send(raw_frame)

        self.assertEqual(tcp_received, [tcp_frame])
        self.assertEqual(raw_received, [raw_frame])
        self.assertEqual(router.dispatch_count, 2)
        self.assertEqual(router.unknown_namespace_count, 0)

    def test_unknown_binary_payload_is_dropped_and_counted(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)
        router.get_transport("tcp_relay")
        router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME)

        t2.send(b"CWG_UNKNOWN\0payload")

        self.assertEqual(router.unknown_namespace_count, 1)
        self.assertEqual(router.dispatch_count, 0)

    def test_unknown_adapter_is_dropped_and_counted(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)
        router.get_transport("tcp_relay")
        router.get_transport("udp_broadcast_forward")

        env = json.dumps({"adapter": "unknown_adapter", "kind": "test"}).encode()
        t2.send(env)

        self.assertEqual(router.unknown_namespace_count, 1)
        self.assertEqual(router.dispatch_count, 0)

    def test_unparseable_payload_is_dropped_and_counted(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)
        router.get_transport("tcp_relay")

        t2.send(b"\xff\xfe\xfd")

        self.assertEqual(router.unknown_namespace_count, 1)

    def test_send_forwards_unchanged_to_underlying(self):
        t1, t2 = make_fake_pair()
        router = BundleTransportRouter(t1)

        received = []
        t2.set_receive_callback(lambda p: received.append(p))

        tcp_transport = router.get_transport("tcp_relay")
        payload = b"raw-payload-bytes"
        tcp_transport.send(payload)

        self.assertEqual(received, [payload])

    def test_get_transport_returns_same_instance(self):
        t1, _ = make_fake_pair()
        router = BundleTransportRouter(t1)

        a = router.get_transport("tcp_relay")
        b = router.get_transport("tcp_relay")
        self.assertIs(a, b)

    def test_extract_adapter_namespace_valid(self):
        self.assertEqual(
            _extract_adapter_namespace(b'{"adapter":"tcp_relay"}'),
            "tcp_relay",
        )

    def test_extract_adapter_namespace_missing(self):
        self.assertIsNone(_extract_adapter_namespace(b'{"kind":"test"}'))

    def test_extract_adapter_namespace_non_json(self):
        self.assertIsNone(_extract_adapter_namespace(b"not json"))


class EndToEndRelayTests(unittest.TestCase):
    """End-to-end tests proving TCP and UDP relay through BundleTransportRouter.

    These tests use real sockets and FakePairTransport to simulate
    Host<->Join relay without a real signaling server.
    """

    def _make_bundle_pair(self):
        """Set up a Host + Join AdapterManager pair connected by FakePairTransport."""
        host_transport, join_transport = make_fake_pair()

        host_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: host_transport,
        )
        join_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: join_transport,
        )
        return host_manager, join_manager

    def test_udp_raw_bridge_exact_payload_through_bundle_router(self):
        sink_received = []
        sink_event = threading.Event()
        sink_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sink_sock.bind(("127.0.0.1", 0))
        game_port = sink_sock.getsockname()[1]
        sink_sock.settimeout(5.0)

        def udp_sink():
            try:
                data, _ = sink_sock.recvfrom(65535)
                sink_received.append(data)
                sink_event.set()
            except Exception:
                pass

        sink_thread = threading.Thread(target=udp_sink, daemon=True)
        sink_thread.start()

        host_underlying, join_underlying = make_fake_pair()
        host_router = BundleTransportRouter(host_underlying)
        join_router = BundleTransportRouter(join_underlying)
        host_adapter = UdpRawBridgeAdapter(
            _profile("host_raw", bind_port=0, target_port=game_port),
            host_router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME),
            fixed_local_target_addr=("127.0.0.1", game_port),
        )
        join_adapter = UdpRawBridgeAdapter(
            _profile("join_raw", bind_port=0),
            join_router.get_transport(UDP_RAW_BRIDGE_ADAPTER_NAME),
        )

        try:
            host_adapter.start()
            join_adapter.start()
            join_host, join_port = join_adapter.get_local_addr()
            self.assertIsNotNone(join_port)

            payload = b"raw-udp-exact\x00bytes\xff\n"
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                client_sock.sendto(payload, (join_host or "127.0.0.1", join_port))
            finally:
                client_sock.close()

            self.assertTrue(sink_event.wait(timeout=5.0))
            self.assertEqual(sink_received, [payload])
            self.assertEqual(host_router.dispatch_count, 1)
            self.assertEqual(host_adapter.get_stats()["bytes_to_game"], len(payload))
            expected_frame_len = len(encode_udp_raw_bridge_frame(payload))
            deadline = time.time() + 2.0
            join_stats = join_adapter.get_stats()
            while time.time() < deadline and join_stats["bytes_to_transport"] < expected_frame_len:
                time.sleep(0.01)
                join_stats = join_adapter.get_stats()
            self.assertEqual(join_stats["bytes_to_transport"], expected_frame_len)
        finally:
            join_adapter.stop()
            host_adapter.stop()
            sink_sock.close()
            sink_thread.join(timeout=2.0)

    def test_join_tcp_reaches_host_tcp_sink(self):
        """Join TCP bytes sent to local_game_connection reach Host TCP sink."""
        # Host side: TCP sink on a known port
        tcp_received = []
        tcp_event = threading.Event()
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        game_port = server_sock.getsockname()[1]
        server_sock.listen(1)
        server_sock.settimeout(5.0)

        def tcp_server():
            try:
                conn, _ = server_sock.accept()
                with conn:
                    chunks = []
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                    tcp_received.append(b"".join(chunks))
                    tcp_event.set()
            except Exception:
                pass

        server_thread = threading.Thread(target=tcp_server, daemon=True)
        server_thread.start()

        host_manager, join_manager = self._make_bundle_pair()

        # Configure Host (Create side)
        host_manager.configure("host", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=game_port,
        ))
        host_status = host_manager.start("host")
        self.assertEqual(host_status.status, "ready")

        # Configure Join (no target)
        join_manager.configure("join", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=0,
        ))
        join_status = join_manager.start("join")
        self.assertEqual(join_status.status, "ready")

        join_diag = join_status.payload_diagnostics
        join_port = join_diag["local_game_connection"]["port"]
        self.assertTrue(join_diag["local_game_connection"]["tcp_available"])

        try:
            # Send TCP from game client through Join
            payload = b"hello-tcp-relay-e2e"
            with socket.create_connection(("127.0.0.1", join_port), timeout=5.0) as sock:
                sock.sendall(payload)
                sock.shutdown(socket.SHUT_WR)

            # Wait for Host TCP sink to receive the data
            self.assertTrue(tcp_event.wait(timeout=10.0),
                            "TCP payload did not reach Host sink")
            self.assertEqual(tcp_received[0], payload)
        finally:
            join_manager.stop("join")
            host_manager.stop("host")
            server_sock.close()
            server_thread.join(timeout=2.0)

    def test_join_udp_reaches_host_udp_sink(self):
        """Join UDP datagram sent to local_game_connection reaches Host UDP sink."""
        # Host UDP sink
        udp_received = []
        udp_event = threading.Event()
        sink_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sink_sock.bind(("127.0.0.1", 0))
        game_port = sink_sock.getsockname()[1]
        sink_sock.settimeout(5.0)

        def udp_server():
            try:
                data, _ = sink_sock.recvfrom(4096)
                udp_received.append(data)
                udp_event.set()
            except Exception:
                pass

        server_thread = threading.Thread(target=udp_server, daemon=True)
        server_thread.start()

        host_manager, join_manager = self._make_bundle_pair()

        host_manager.configure("host", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=game_port,
        ))
        host_status = host_manager.start("host")
        self.assertEqual(host_status.status, "ready")

        join_manager.configure("join", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=0,
        ))
        join_status = join_manager.start("join")
        self.assertEqual(join_status.status, "ready")

        join_diag = join_status.payload_diagnostics
        join_port = join_diag["local_game_connection"]["port"]
        self.assertTrue(join_diag["local_game_connection"]["udp_available"])

        try:
            payload = b"hello-udp-broadcast-e2e"
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(payload, ("127.0.0.1", join_port))
            finally:
                sock.close()

            self.assertTrue(udp_event.wait(timeout=10.0),
                            "UDP payload did not reach Host sink")
            self.assertEqual(udp_received[0], payload)
        finally:
            join_manager.stop("join")
            host_manager.stop("host")
            sink_sock.close()
            server_thread.join(timeout=2.0)

    def test_host_udp_response_returns_to_join_last_sender(self):
        """Host UDP response returns to Join last local sender."""
        # This test sends UDP from Join to Host, then Host responds
        # back through the relay, and Join delivers to last_local_sender.
        host_transport, join_transport = make_fake_pair()

        # Host side: real game server that echoes UDP back
        echo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        echo_sock.bind(("127.0.0.1", 0))
        game_port = echo_sock.getsockname()[1]
        echo_sock.settimeout(5.0)

        echo_stop = threading.Event()

        def udp_echo():
            while not echo_stop.is_set():
                try:
                    data, addr = echo_sock.recvfrom(4096)
                    # Echo response back to the sender (Host broadcast adapter)
                    echo_sock.sendto(b"ECHO:" + data, addr)
                except socket.timeout:
                    continue
                except Exception:
                    break

        echo_thread = threading.Thread(target=udp_echo, daemon=True)
        echo_thread.start()

        host_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: host_transport,
        )
        join_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: join_transport,
        )

        host_manager.configure("host", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=game_port,
        ))
        host_status = host_manager.start("host")
        self.assertEqual(host_status.status, "ready")

        join_manager.configure("join", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=0,
        ))
        join_status = join_manager.start("join")
        self.assertEqual(join_status.status, "ready")

        join_port = join_status.payload_diagnostics["local_game_connection"]["port"]

        try:
            # Game client sends UDP to Join's local_game_connection
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_sock.settimeout(10.0)
            client_sock.bind(("127.0.0.1", 0))
            client_addr = client_sock.getsockname()

            payload = b"ping-udp-echo"
            client_sock.sendto(payload, ("127.0.0.1", join_port))

            # Wait for echo response from Host via relay
            try:
                response, from_addr = client_sock.recvfrom(4096)
                self.assertEqual(response, b"ECHO:" + payload)
            except socket.timeout:
                self.fail("UDP echo response did not return to Join last sender")
            finally:
                client_sock.close()
        finally:
            join_manager.stop("join")
            host_manager.stop("host")
            echo_stop.set()
            echo_sock.close()
            echo_thread.join(timeout=2.0)

    def test_both_callbacks_remain_active_simultaneously(self):
        """tcp_relay and udp_broadcast_forward callbacks both remain active
        after both adapters are started through BundleTransportRouter."""
        host_transport, join_transport = make_fake_pair()

        # Host TCP echo server
        tcp_echo_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_echo_sock.bind(("127.0.0.1", 0))
        game_port = tcp_echo_sock.getsockname()[1]
        tcp_echo_sock.listen(1)
        tcp_echo_sock.settimeout(10.0)

        # Host UDP echo server on same port
        udp_echo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_echo_sock.bind(("127.0.0.1", game_port))
        udp_echo_sock.settimeout(10.0)

        tcp_received = []
        udp_received = []
        tcp_event = threading.Event()
        udp_event = threading.Event()
        echo_stop = threading.Event()

        def tcp_echo():
            while not echo_stop.is_set():
                try:
                    conn, _ = tcp_echo_sock.accept()
                    with conn:
                        data = conn.recv(4096)
                        if data:
                            tcp_received.append(data)
                            tcp_event.set()
                except socket.timeout:
                    continue
                except Exception:
                    break

        def udp_echo():
            while not echo_stop.is_set():
                try:
                    data, addr = udp_echo_sock.recvfrom(4096)
                    udp_received.append(data)
                    udp_event.set()
                    udp_echo_sock.sendto(b"RESP:" + data, addr)
                except socket.timeout:
                    continue
                except Exception:
                    break

        tcp_thread = threading.Thread(target=tcp_echo, daemon=True)
        udp_thread = threading.Thread(target=udp_echo, daemon=True)
        tcp_thread.start()
        udp_thread.start()

        host_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: host_transport,
        )
        join_manager = AdapterManager(
            bundle_transport_factory=lambda sid, cfg: join_transport,
        )

        host_manager.configure("host", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=game_port,
        ))
        host_status = host_manager.start("host")
        self.assertEqual(host_status.status, "ready")

        join_manager.configure("join", AdapterConfig(
            enabled=True, adapter_type="bundle",
            bind_port=0, target_port=0,
        ))
        join_status = join_manager.start("join")
        self.assertEqual(join_status.status, "ready")

        join_port = join_status.payload_diagnostics["local_game_connection"]["port"]
        self.assertTrue(join_status.payload_diagnostics["local_game_connection"]["tcp_available"])
        self.assertTrue(join_status.payload_diagnostics["local_game_connection"]["udp_available"])

        try:
            # Send TCP through Join -> Host
            tcp_payload = b"tcp-both-active"
            with socket.create_connection(("127.0.0.1", join_port), timeout=5.0) as sock:
                sock.sendall(tcp_payload)
                sock.shutdown(socket.SHUT_WR)

            # Send UDP through Join -> Host
            udp_payload = b"udp-both-active"
            udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                udp_sock.sendto(udp_payload, ("127.0.0.1", join_port))
            finally:
                udp_sock.close()

            # Verify both arrive at Host
            self.assertTrue(tcp_event.wait(timeout=10.0),
                            "TCP not received — callback may have been overwritten")
            self.assertTrue(udp_event.wait(timeout=10.0),
                            "UDP not received — callback may have been overwritten")
            self.assertEqual(tcp_received[0], tcp_payload)
            self.assertEqual(udp_received[0], udp_payload)
        finally:
            join_manager.stop("join")
            host_manager.stop("host")
            echo_stop.set()
            tcp_echo_sock.close()
            udp_echo_sock.close()
            tcp_thread.join(timeout=2.0)
            udp_thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
