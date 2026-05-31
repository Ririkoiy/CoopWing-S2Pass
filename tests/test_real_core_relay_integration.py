import unittest
import asyncio
import subprocess
import sys
import time
import threading
from pathlib import Path

from adapters.profile import GameProfile
from adapters.core_transport_adapter import CoreTransportAdapter
from adapters.local_udp_bridge_adapter import LocalUdpBridgeAdapter
from tools.udp_game_server import run_server
from tools.udp_game_client import run_client
from network_core import S2PassClientCore, S2PassConfig

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class TestRealCoreRelayIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Smoke test running a real server.py process and two real S2PassClientCore
    instances in payload mode, bridged through CoreTransportAdapter and
    LocalUdpBridgeAdapter to verify end-to-end game payload routing over relay.
    """

    async def _wait_for_port(self, port: int, timeout: float = 5.0) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return True
            except Exception:
                await asyncio.sleep(0.1)
        return False

    async def test_real_core_relay_smoke(self):
        # 1. Locate and start server.py in a subprocess.
        project_root = Path(__file__).resolve().parent.parent
        server_path = project_root / "server.py"

        server_process = subprocess.Popen(
            [sys.executable, str(server_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def cleanup_server():
            if server_process.poll() is None:
                server_process.terminate()
                try:
                    server_process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    if server_process.poll() is None:
                        server_process.kill()
                        server_process.wait()
            if server_process.stdout:
                server_process.stdout.close()
            if server_process.stderr:
                server_process.stderr.close()

        self.addCleanup(cleanup_server)

        # Wait until server TCP port 9000 is open.
        port_ready = await self._wait_for_port(9000, timeout=5.0)
        self.assertTrue(
            port_ready,
            "Local server.py TCP port 9000 did not become ready in time",
        )

        # 2. Start udp_game_server in a background thread.
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

        def cleanup_game_server():
            stop_event.set()
            if server_thread.is_alive():
                server_thread.join(timeout=2.0)

        self.addCleanup(cleanup_game_server)

        self.assertTrue(ready_event.wait(timeout=3.0), "Game server failed to bind in time")
        self.assertIsNotNone(bound_addr[0], "Game server bound address is None")
        game_server_host, game_server_port = bound_addr[0]

        # 3. Initialize events and callbacks to track cores.
        room_id_event = asyncio.Event()
        room_id_container = []
        relay_ready_a = asyncio.Event()
        relay_ready_b = asyncio.Event()
        errors_container = []

        def cb_a(event):
            if event.type == "ROOM_CREATED":
                room_id_container.append(event.data["room_id"])
                room_id_event.set()
            elif event.type == "RELAY_ENABLED":
                relay_ready_a.set()
            elif event.type == "ERROR":
                errors_container.append(f"Core A error: {event.message}")

        def cb_b(event):
            if event.type == "RELAY_ENABLED":
                relay_ready_b.set()
            elif event.type == "ERROR":
                errors_container.append(f"Core B error: {event.message}")

        # 4. Start Core A (role=create).
        config_a = S2PassConfig(
            host="127.0.0.1",
            port=9000,
            udp_port=9001,
            player_name="Alice",
            role="create",
            force_relay=True,
            is_payload_mode=True,
            send_test=False,
            lobby_timeout=10,
        )
        core_a = S2PassClientCore(config_a, event_callback=cb_a)
        task_a = asyncio.create_task(core_a.run())

        async def cleanup_core_a():
            if not task_a.done():
                task_a.cancel()
                try:
                    await task_a
                except asyncio.CancelledError:
                    pass
            await core_a.close()

        self.addCleanup(cleanup_core_a)

        # Wait for Room ID.
        try:
            await asyncio.wait_for(room_id_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self.fail(f"ROOM_CREATED timeout. Errors: {errors_container}")

        room_id = room_id_container[0]

        # 5. Start Core B (role=join).
        config_b = S2PassConfig(
            host="127.0.0.1",
            port=9000,
            udp_port=9001,
            player_name="Bob",
            role="join",
            room_id=room_id,
            force_relay=True,
            is_payload_mode=True,
            send_test=False,
            lobby_timeout=10,
        )
        core_b = S2PassClientCore(config_b, event_callback=cb_b)
        task_b = asyncio.create_task(core_b.run())

        async def cleanup_core_b():
            if not task_b.done():
                task_b.cancel()
                try:
                    await task_b
                except asyncio.CancelledError:
                    pass
            await core_b.close()

        self.addCleanup(cleanup_core_b)

        # 6. Wait for relay ready on both cores.
        try:
            await asyncio.wait_for(
                asyncio.gather(relay_ready_a.wait(), relay_ready_b.wait()),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            self.fail(f"RELAY_ENABLED timeout. Errors: {errors_container}")

        # 7. Create CoreTransportAdapter A and B.
        loop = asyncio.get_running_loop()
        transport_a = CoreTransportAdapter(core_a, loop)
        transport_b = CoreTransportAdapter(core_b, loop)

        self.addCleanup(transport_a.close)
        self.addCleanup(transport_b.close)

        # 8. Create LocalUdpBridgeAdapter A and B.
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
            fixed_local_target_addr=(game_server_host, game_server_port),
        )

        adapter_a.start()
        self.addCleanup(adapter_a.stop)

        adapter_b.start()
        self.addCleanup(adapter_b.stop)

        host_a, port_a = adapter_a.get_local_addr()
        self.assertIsNotNone(host_a)
        self.assertIsNotNone(port_a)
        self.assertGreater(port_a, 0)

        # 9. Run udp_game_client pointing to Adapter A local UDP address.
        # Run it in a background thread so the asyncio event loop stays unblocked.
        exit_code, stats = await asyncio.to_thread(
            run_client,
            host=host_a,
            port=port_a,
            client_id="smoke_client",
            count=3,
            interval=0.01,
            timeout=2.0,
        )

        # 10. Verify results.
        self.assertEqual(exit_code, 0, f"Client exit_code must be 0, got {exit_code}")
        self.assertTrue(stats["joined"], "Client failed to join server")
        self.assertEqual(stats["lost"], 0, "No packet loss expected")
        self.assertEqual(stats["received"], 3, "Expected exactly 3 PONG responses")
        self.assertEqual(stats.get("unexpected", 0), 0, "No unexpected responses allowed")

        # Wait for adapter counters to flush.
        expected_count = 4  # 1 JOIN + 3 PING/PONG messages.
        start_wait = time.time()
        while time.time() - start_wait < 2.0:
            if (
                adapter_a.packets_from_game >= expected_count
                and adapter_a.packets_to_game >= expected_count
                and adapter_b.packets_from_game >= expected_count
                and adapter_b.packets_to_game >= expected_count
            ):
                break
            await asyncio.sleep(0.01)

        self.assertEqual(adapter_a.packets_from_game, expected_count)
        self.assertEqual(adapter_a.packets_to_transport, expected_count)
        self.assertEqual(adapter_a.packets_from_transport, expected_count)
        self.assertEqual(adapter_a.packets_to_game, expected_count)

        self.assertEqual(adapter_b.packets_from_transport, expected_count)
        self.assertEqual(adapter_b.packets_to_game, expected_count)
        self.assertEqual(adapter_b.packets_from_game, expected_count)
        self.assertEqual(adapter_b.packets_to_transport, expected_count)

    def test_static_boundaries(self):
        """Verify strict protocol isolation and import boundaries."""
        test_file_path = __file__
        with open(test_file_path, "r", encoding="utf-8") as f:
            test_content = f.read()

        # 1. No repository command invocation.
        repo_cmd = "g" + "i" + "t" + " "
        self.assertNotIn(repo_cmd, test_content)

        # 2. No forbidden protocol/direct-mode strings.
        # Values are assembled dynamically so this test does not match itself.
        forbidden = [
            "nat" + "_" + "test",
            "P" + "UNCH",
            "D" + "ATA\n",
            "P" + "ROTOCOL" + "_" + "LOCK",
            "p" + "rotocol" + "_" + "lock",
        ]
        for word in forbidden:
            self.assertNotIn(word, test_content)

        # 3. No direct imports of server.py.
        import_server = "import" + " " + "server"
        from_server = "from" + " " + "server"
        self.assertNotIn(import_server, test_content)
        self.assertNotIn(from_server, test_content)
