import unittest
import unittest.mock
import os
import sys
import shutil
import tempfile
import time
from adapters.profile import GameProfile, load_profiles, save_profiles
from adapters.launch_adapter import LaunchAdapter, is_pid_running

class TestGameProfile(unittest.TestCase):
    def test_default_profile(self):
        profile = GameProfile(
            profile_id="test_id",
            display_name="Test Game",
            exe_path="C:\\games\\test.exe"
        )
        self.assertEqual(profile.profile_id, "test_id")
        self.assertEqual(profile.display_name, "Test Game")
        self.assertEqual(profile.exe_path, "C:\\games\\test.exe")
        # working_dir should be resolved to the directory of exe_path
        self.assertEqual(profile.working_dir, "C:\\games")
        self.assertEqual(profile.launch_args, "")
        self.assertEqual(profile.adapter_type, "launch_only")
        self.assertEqual(profile.local_bind_host, "127.0.0.1")
        self.assertIsNone(profile.local_bind_port)
        self.assertEqual(profile.expected_ports, [])
        self.assertEqual(profile.doctor_profile, {})

    def test_custom_working_dir(self):
        profile = GameProfile(
            profile_id="test_id",
            display_name="Test Game",
            exe_path="C:\\games\\test.exe",
            working_dir="D:\\custom_dir"
        )
        self.assertEqual(profile.working_dir, "D:\\custom_dir")

    def test_to_from_dict(self):
        profile = GameProfile(
            profile_id="test_id",
            display_name="Test Game",
            exe_path="C:\\games\\test.exe",
            local_bind_port=1234,
            expected_ports=[1234, 5678],
            doctor_profile={"key": "value"}
        )
        d = profile.to_dict()
        self.assertEqual(d["profile_id"], "test_id")
        self.assertEqual(d["local_bind_port"], 1234)
        self.assertEqual(d["expected_ports"], [1234, 5678])
        self.assertEqual(d["doctor_profile"], {"key": "value"})

        restored = GameProfile.from_dict(d)
        self.assertEqual(restored.profile_id, "test_id")
        self.assertEqual(restored.local_bind_port, 1234)
        self.assertEqual(restored.expected_ports, [1234, 5678])
        self.assertEqual(restored.doctor_profile, {"key": "value"})

class TestProfilesIO(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.file_path = os.path.join(self.test_dir, "config", "profiles.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_save_load_profiles(self):
        # Verify load on non-existent file
        profiles = load_profiles(self.file_path)
        self.assertEqual(profiles, [])

        # Create profiles
        p1 = GameProfile(profile_id="p1", display_name="Game 1", exe_path="C:\\g1.exe")
        p2 = GameProfile(profile_id="p2", display_name="Game 2", exe_path="C:\\g2.exe")
        
        # Save profiles (should create config directory automatically)
        save_profiles([p1, p2], self.file_path)
        self.assertTrue(os.path.exists(self.file_path))

        # Load profiles
        loaded = load_profiles(self.file_path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].profile_id, "p1")
        self.assertEqual(loaded[1].profile_id, "p2")

class TestLaunchAdapter(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_invalid_executable(self):
        # Empty exe_path
        profile = GameProfile(profile_id="p", display_name="P", exe_path="")
        adapter = LaunchAdapter(profile)
        with self.assertRaises(ValueError):
            adapter.start()

        # Non-existent exe_path
        profile = GameProfile(profile_id="p", display_name="P", exe_path=os.path.join(self.test_dir, "nonexistent.exe"))
        adapter = LaunchAdapter(profile)
        with self.assertRaises(FileNotFoundError):
            adapter.start()

        # Directory instead of file
        profile = GameProfile(profile_id="p", display_name="P", exe_path=self.test_dir)
        adapter = LaunchAdapter(profile)
        with self.assertRaises(ValueError):
            adapter.start()

    def test_invalid_working_dir(self):
        # Non-existent working directory
        exe_file = os.path.join(self.test_dir, "dummy.exe")
        with open(exe_file, "w") as f:
            f.write("")
        profile = GameProfile(
            profile_id="p", 
            display_name="P", 
            exe_path=exe_file, 
            working_dir=os.path.join(self.test_dir, "nonexistent_dir")
        )
        adapter = LaunchAdapter(profile)
        with self.assertRaises(FileNotFoundError):
            adapter.start()

    def test_launch_and_lifecycle(self):
        if sys.platform == 'win32':
            system_root = os.environ.get('SystemRoot', 'C:\\Windows')
            exe = os.path.join(system_root, 'System32', 'ping.exe')
            # ping localhost 5 times (takes ~4 seconds to finish on its own)
            launch_args = '-n 5 127.0.0.1'
        else:
            import shutil
            exe = shutil.which('sleep') or '/bin/sleep'
            launch_args = '5'

        # Make sure the executable exists
        if not os.path.exists(exe):
            self.skipTest(f"Required test utility {exe} not found on system.")

        profile = GameProfile(
            profile_id="sys_test",
            display_name="System Utility Test",
            exe_path=exe,
            launch_args=launch_args
        )

        adapter = LaunchAdapter(profile)
        self.assertFalse(adapter.is_running())
        self.assertIsNone(adapter.get_pid())

        # Start the process
        adapter.start()
        self.assertTrue(adapter.is_running())
        pid = adapter.get_pid()
        self.assertIsNotNone(pid)
        self.assertGreater(pid, 0)

        # Check PID check utility directly
        self.assertTrue(is_pid_running(pid))

        # Stop process
        adapter.stop()
        self.assertFalse(adapter.is_running())
        self.assertIsNone(adapter.get_pid())
        self.assertFalse(is_pid_running(pid))

        # Check that stop() is a no-op when called again
        adapter.stop()

    @unittest.mock.patch('subprocess.Popen')
    @unittest.mock.patch('os.path.exists')
    @unittest.mock.patch('os.path.isfile')
    def test_launch_args_passed_correctly(self, mock_isfile, mock_exists, mock_popen):
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        # Mock Popen return value
        mock_process = unittest.mock.MagicMock()
        mock_process.pid = 9999
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process

        launch_args = '-path "C:\\Program Files\\My Game" -args "some parameter" --flag'
        profile = GameProfile(
            profile_id="test",
            display_name="Test",
            exe_path="C:\\games\\game.exe",
            launch_args=launch_args
        )
        
        adapter = LaunchAdapter(profile)
        adapter.start()
        
        # Verify subprocess.Popen was called with correct command array
        mock_popen.assert_called_once()
        called_args, called_kwargs = mock_popen.call_args
        
        cmd = called_args[0]
        if sys.platform == 'win32':
            # On Windows, posix=False retains double quotes
            self.assertEqual(cmd, [
                "C:\\games\\game.exe",
                "-path",
                '"C:\\Program Files\\My Game"',
                "-args",
                '"some parameter"',
                "--flag"
            ])
        else:
            # On non-Windows, posix=True strips double quotes
            self.assertEqual(cmd, [
                "C:\\games\\game.exe",
                "-path",
                "C:\\Program Files\\My Game",
                "-args",
                "some parameter",
                "--flag"
            ])
