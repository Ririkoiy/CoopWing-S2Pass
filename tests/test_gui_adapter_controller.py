"""
Tests for GuiAdapterController — Preview 0.1

Uses sys.executable with a short sleep command instead of notepad.exe
for automated launch adapter tests.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.profile import GameProfile
from gui_adapter_controller import GuiAdapterController
import paths


class TestPaths(unittest.TestCase):
    """Tests for paths.py helpers."""

    def test_app_base_dir_is_project_root(self):
        """app_base_dir() should return the directory containing paths.py."""
        base = paths.app_base_dir()
        self.assertTrue(os.path.isdir(base))
        self.assertTrue(os.path.isfile(os.path.join(base, "paths.py")))

    def test_resource_path(self):
        p = paths.resource_path("tools")
        self.assertEqual(p, os.path.join(paths.app_base_dir(), "tools"))

    def test_config_dir(self):
        self.assertEqual(paths.config_dir(),
                         os.path.join(paths.app_base_dir(), "config"))

    def test_diagnostics_dir(self):
        self.assertEqual(paths.diagnostics_dir(),
                         os.path.join(paths.app_base_dir(), "diagnostics"))

    def test_tools_dir(self):
        self.assertEqual(paths.tools_dir(),
                         os.path.join(paths.app_base_dir(), "tools"))

    def test_logs_dir(self):
        self.assertEqual(paths.logs_dir(),
                         os.path.join(paths.app_base_dir(), "logs"))

    def test_no_hardcoded_paths(self):
        """Paths must not contain hardcoded user-specific fragments."""
        for fn in [paths.app_base_dir, paths.config_dir, paths.diagnostics_dir,
                   paths.tools_dir, paths.logs_dir]:
            # Just ensure they are relative to app_base_dir
            result = fn()
            self.assertTrue(result.startswith(paths.app_base_dir()) or
                            result == paths.app_base_dir())


class TestControllerProfiles(unittest.TestCase):
    """Profile management tests using a temp directory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_messages = []

        def log_cb(source, level, message):
            self.log_messages.append((source, level, message))

        self.ctrl = GuiAdapterController(log_callback=log_cb)

        # Patch paths.config_dir to use tmpdir
        self._patcher = patch('paths.config_dir', return_value=self.tmpdir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_missing_file(self):
        """Loading when profiles.json doesn't exist returns empty list."""
        profiles = self.ctrl.load_all_profiles()
        self.assertEqual(profiles, [])
        self.assertTrue(any("Loaded 0" in m for _, _, m in self.log_messages))

    def test_save_and_load(self):
        """Save profiles then load them back."""
        p = GameProfile(
            profile_id="test1",
            display_name="Test Profile",
            exe_path="C:\\Windows\\System32\\notepad.exe",
        )
        self.ctrl.add_profile(p)
        self.ctrl.save_all_profiles()

        # Verify file exists
        fp = os.path.join(self.tmpdir, "profiles.json")
        self.assertTrue(os.path.isfile(fp))

        # Load back
        profiles = self.ctrl.load_all_profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].display_name, "Test Profile")

    def test_add_and_get_names(self):
        p1 = GameProfile(profile_id="a", display_name="Alpha", exe_path="")
        p2 = GameProfile(profile_id="b", display_name="Beta", exe_path="")
        self.ctrl.add_profile(p1)
        self.ctrl.add_profile(p2)
        self.assertEqual(self.ctrl.get_profile_names(), ["Alpha", "Beta"])

    def test_select_profile(self):
        p = GameProfile(profile_id="x", display_name="X", exe_path="")
        self.ctrl.add_profile(p)
        selected = self.ctrl.select_profile(0)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.profile_id, "x")
        self.assertEqual(self.ctrl.selected_index, 0)

    def test_select_out_of_range(self):
        result = self.ctrl.select_profile(99)
        self.assertIsNone(result)
        self.assertEqual(self.ctrl.selected_index, -1)

    def test_update_profile(self):
        p = GameProfile(profile_id="u", display_name="Old", exe_path="")
        self.ctrl.add_profile(p)
        p2 = GameProfile(profile_id="u", display_name="New", exe_path="")
        self.ctrl.update_profile(0, p2)
        self.assertEqual(self.ctrl.get_profiles()[0].display_name, "New")

    def test_remove_profile(self):
        p = GameProfile(profile_id="r", display_name="Remove", exe_path="")
        self.ctrl.add_profile(p)
        self.ctrl.remove_profile(0)
        self.assertEqual(len(self.ctrl.get_profiles()), 0)

    def test_log_callback_invoked(self):
        self.ctrl.load_all_profiles()
        self.assertTrue(len(self.log_messages) > 0)
        sources = [s for s, _, _ in self.log_messages]
        self.assertIn("Profile", sources)


class TestControllerLaunch(unittest.TestCase):
    """LaunchAdapter tests using sys.executable with a short sleep."""

    def setUp(self):
        self.log_messages = []

        def log_cb(source, level, message):
            self.log_messages.append((source, level, message))

        self.ctrl = GuiAdapterController(log_callback=log_cb)

    def tearDown(self):
        self.ctrl.cleanup()

    def test_start_and_stop(self):
        """Start a short-lived python process and stop it."""
        profile = GameProfile(
            profile_id="test_launch",
            display_name="Sleep Test",
            exe_path=sys.executable,
            launch_args="-c \"import time; time.sleep(30)\"",
        )
        pid = self.ctrl.start_launch(profile)
        self.assertIsNotNone(pid)
        self.assertIsInstance(pid, int)

        status = self.ctrl.get_launch_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["pid"], pid)

        self.ctrl.stop_launch()
        status2 = self.ctrl.get_launch_status()
        self.assertFalse(status2["running"])

    def test_start_invalid_exe(self):
        """Starting with a nonexistent exe should log an error."""
        profile = GameProfile(
            profile_id="bad",
            display_name="Bad",
            exe_path="C:\\nonexistent\\fake.exe",
        )
        pid = self.ctrl.start_launch(profile)
        self.assertIsNone(pid)
        self.assertTrue(any("ERROR" in lvl for _, lvl, _ in self.log_messages))

    def test_stop_when_nothing_running(self):
        """Stopping with no process should not crash."""
        self.ctrl.stop_launch()
        self.assertTrue(any("No process" in m for _, _, m in self.log_messages))


class TestControllerUdp(unittest.TestCase):
    """UDP Adapter tests."""

    def setUp(self):
        self.log_messages = []

        def log_cb(source, level, message):
            self.log_messages.append((source, level, message))

        self.ctrl = GuiAdapterController(log_callback=log_cb)

    def tearDown(self):
        self.ctrl.cleanup()

    def test_start_echo_and_stop(self):
        """Start UDP adapter in echo mode, check stats, stop."""
        profile = GameProfile(
            profile_id="udp_test",
            display_name="UDP Echo",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,  # auto-assign
        )
        ok = self.ctrl.start_udp(profile, mode="echo")
        self.assertTrue(ok)

        stats = self.ctrl.get_udp_stats()
        self.assertTrue(stats["running"])
        self.assertIsNotNone(stats["local_host"])
        self.assertIsNotNone(stats["local_port"])
        self.assertEqual(stats["received_packets"], 0)

        self.ctrl.stop_udp()
        stats2 = self.ctrl.get_udp_stats()
        self.assertFalse(stats2["running"])

    def test_start_forward_missing_target(self):
        """Forward mode without remote target should fail."""
        profile = GameProfile(
            profile_id="udp_fwd",
            display_name="UDP Fwd",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
        )
        ok = self.ctrl.start_udp(profile, mode="forward")
        self.assertFalse(ok)
        self.assertTrue(any("ERROR" in lvl for _, lvl, _ in self.log_messages))

    def test_stop_when_not_running(self):
        """Stopping with no adapter should not crash."""
        self.ctrl.stop_udp()
        self.assertTrue(any("No UDP" in m for _, _, m in self.log_messages))

    def test_double_start(self):
        """Starting twice should warn, not crash."""
        profile = GameProfile(
            profile_id="udp2",
            display_name="UDP2",
            exe_path="",
            local_bind_host="127.0.0.1",
            local_bind_port=0,
        )
        self.ctrl.start_udp(profile, mode="echo")
        ok = self.ctrl.start_udp(profile, mode="echo")
        self.assertFalse(ok)
        self.assertTrue(any("already running" in m for _, _, m in self.log_messages))
        self.ctrl.stop_udp()


class TestControllerDoctor(unittest.TestCase):
    """Network Doctor subprocess tests."""

    def setUp(self):
        self.log_messages = []

        def log_cb(source, level, message):
            self.log_messages.append((source, level, message))

        self.ctrl = GuiAdapterController(log_callback=log_cb)

    def tearDown(self):
        self.ctrl.cleanup()

    def test_doctor_tool_path_source_mode(self):
        """In source mode, doctor path should end with network_doctor.py."""
        path = self.ctrl._doctor_tool_path()
        self.assertTrue(path.endswith("network_doctor.py"))
        # Should be under tools_dir
        self.assertTrue(path.startswith(paths.tools_dir()))

    def test_run_doctor_no_zip(self):
        """Verify command construction with --no-zip (mocked, no real execution)."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 99999
            mock_popen.return_value.poll.return_value = None

            ok = self.ctrl.run_network_doctor(no_zip=True)
            self.assertTrue(ok)

            # Verify Popen was called exactly once
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]

            # Verify command contains expected elements
            self.assertTrue(any("network_doctor" in arg for arg in cmd),
                            f"cmd must reference network_doctor: {cmd}")
            self.assertIn("--output-dir", cmd)
            self.assertIn("--no-zip", cmd)
            # --output-dir should be followed by diagnostics path
            idx = cmd.index("--output-dir")
            self.assertIn("diagnostics", cmd[idx + 1])
            # Verify log messages
            self.assertTrue(any("started" in m.lower() for _, _, m in self.log_messages))

    def test_run_doctor_with_peer_ip(self):
        """Verify --peer-ip is included in the command."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 99998
            mock_popen.return_value.poll.return_value = None

            ok = self.ctrl.run_network_doctor(peer_ip="10.0.0.5")
            self.assertTrue(ok)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--peer-ip", cmd)
            idx = cmd.index("--peer-ip")
            self.assertEqual(cmd[idx + 1], "10.0.0.5")

    def test_run_doctor_with_interface(self):
        """Verify --interface is included in the command."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 99997
            mock_popen.return_value.poll.return_value = None

            ok = self.ctrl.run_network_doctor(interface="et_tun0")
            self.assertTrue(ok)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--interface", cmd)
            idx = cmd.index("--interface")
            self.assertEqual(cmd[idx + 1], "et_tun0")

    def test_run_doctor_with_server_host(self):
        """Verify --server-host is included in the command."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 99996
            mock_popen.return_value.poll.return_value = None

            ok = self.ctrl.run_network_doctor(server_host="s2pass.example.com")
            self.assertTrue(ok)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--server-host", cmd)
            idx = cmd.index("--server-host")
            self.assertEqual(cmd[idx + 1], "s2pass.example.com")

    def test_run_doctor_all_params(self):
        """Verify all optional params are included together."""
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 99995
            mock_popen.return_value.poll.return_value = None

            ok = self.ctrl.run_network_doctor(
                peer_ip="192.168.1.100",
                interface="Ethernet 2",
                server_host="my.server.net",
                no_zip=True,
            )
            self.assertTrue(ok)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--peer-ip", cmd)
            self.assertIn("--interface", cmd)
            self.assertIn("--server-host", cmd)
            self.assertIn("--no-zip", cmd)
            self.assertIn("--output-dir", cmd)

    def test_open_diagnostics_dir(self):
        """open_diagnostics_dir should not crash (creates dir if missing)."""
        with patch('os.startfile') as mock_startfile:
            self.ctrl.open_diagnostics_dir()
            mock_startfile.assert_called_once()
            self.assertTrue(any("Opened" in m for _, _, m in self.log_messages))


class TestControllerCleanup(unittest.TestCase):
    """Cleanup tests."""

    def test_cleanup_with_nothing_running(self):
        """Cleanup with no adapters should not crash."""
        ctrl = GuiAdapterController()
        ctrl.cleanup()  # Should not raise


if __name__ == '__main__':
    unittest.main()
