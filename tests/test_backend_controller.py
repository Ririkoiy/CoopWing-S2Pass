import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Enable importing from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from adapters.profile import GameProfile
from backend.backend_controller import BackendController
from backend.api_models import BackendError
from backend.s2pass_backend import handle_get_health

class TestBackendController(unittest.TestCase):
    def setUp(self):
        # Create temp files for profiles, settings, servers
        self.temp_profiles = tempfile.mktemp(suffix=".json")
        self.temp_settings = tempfile.mktemp(suffix=".json")
        self.temp_servers = tempfile.mktemp(suffix=".json")
        
        # Write template example files in case controller falls back to them
        self.temp_settings_example = self.temp_settings + ".example.json"
        self.temp_servers_example = self.temp_servers + ".example.json"
        
        # We will patch the store paths in setUp or pass them to constructor
        # Since controller allows customizing paths:
        # BackendController(settings_path, servers_path, profiles_path)
        
    def tearDown(self):
        for path in [self.temp_profiles, self.temp_settings, self.temp_servers, 
                     self.temp_settings_example, self.temp_servers_example]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def test_init_controller_does_not_crash(self):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        self.assertIsNotNone(controller)
        self.assertTrue(os.path.exists(self.temp_settings))
        self.assertTrue(os.path.exists(self.temp_servers))

    def test_get_health_returns_ok(self):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        res = handle_get_health(controller)
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["status"], "ready")
        self.assertEqual(res["data"]["version"], "0.2.0-preview")

    def test_profiles_file_not_exist_returns_empty_list(self):
        # Ensure profile file does not exist
        if os.path.exists(self.temp_profiles):
            os.remove(self.temp_profiles)
            
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        self.assertEqual(controller.get_profiles(), [])

    def test_create_profile_draft_from_exe_valid(self):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        
        # Create a dummy exe file
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZtest")
            dummy_exe = f.name
            
        try:
            draft = controller.create_profile_draft_from_exe(dummy_exe)
            self.assertEqual(draft.profile_id, "")  # empty for draft
            self.assertEqual(draft.display_name, os.path.splitext(os.path.basename(dummy_exe))[0])
            self.assertEqual(draft.exe_path, dummy_exe)
            self.assertEqual(draft.working_dir, os.path.dirname(dummy_exe))
            self.assertEqual(draft.adapter_type, "launch_only")
            self.assertEqual(draft.local_bind_host, "127.0.0.1")
            self.assertIsNone(draft.local_bind_port)
        finally:
            if os.path.exists(dummy_exe):
                os.remove(dummy_exe)

    def test_create_profile_draft_from_exe_invalid_extensions(self):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        
        # 1. Non exe extension
        with self.assertRaises(BackendError) as ctx:
            controller.create_profile_draft_from_exe("game.txt")
        self.assertEqual(ctx.exception.code, "PROFILE_INVALID_EXE")
        
        # 2. Directory path
        temp_dir = tempfile.mkdtemp()
        try:
            dir_exe_path = os.path.join(temp_dir, "test.exe")
            os.makedirs(dir_exe_path)
            with self.assertRaises(BackendError) as ctx:
                controller.create_profile_draft_from_exe(dir_exe_path)
            self.assertEqual(ctx.exception.code, "PROFILE_EXE_IS_DIRECTORY")
        finally:
            if os.path.exists(temp_dir):
                import shutil
                shutil.rmtree(temp_dir)

        # 3. File not found
        with self.assertRaises(BackendError) as ctx:
            controller.create_profile_draft_from_exe("C:\\nonexistent_game_path_xyz.exe")
        self.assertEqual(ctx.exception.code, "PROFILE_EXE_NOT_FOUND")

    def test_settings_and_servers_migration_from_example(self):
        # Write example json files
        example_settings = {
            "default_server_id": "ririko_vps",
            "backend_api_port": 21520,
            "log_level": "INFO",
            "developer_mode": False,
            "theme": "dark"
        }
        example_servers = [
            {
                "server_id": "ririko_vps",
                "display_name": "Default VPS",
                "host": "120.27.210.184",
                "description": "Default Preview relay/signaling server",
                "enabled": True
            }
        ]
        
        with open(self.temp_settings_example, "w", encoding="utf-8") as f:
            import json
            json.dump(example_settings, f)
            
        with open(self.temp_servers_example, "w", encoding="utf-8") as f:
            json.dump(example_servers, f)
            
        # Patch the example paths in SettingsStore and ServerStore
        with patch('backend.settings_store.paths.config_dir', return_value=os.path.dirname(self.temp_settings)), \
             patch('backend.server_store.paths.config_dir', return_value=os.path.dirname(self.temp_servers)):
            
            # Change the example paths inside stores
            with patch('backend.settings_store.SettingsStore.__init__', 
                       lambda s, path: setattr(s, '_settings_path', path) or setattr(s, '_example_path', self.temp_settings_example) or s.load()), \
                 patch('backend.server_store.ServerStore.__init__', 
                       lambda s, path: setattr(s, '_servers_path', path) or setattr(s, '_example_path', self.temp_servers_example) or s.load()):
                
                controller = BackendController(
                    settings_path=self.temp_settings,
                    servers_path=self.temp_servers,
                    profiles_path=self.temp_profiles
                )
                
                # Check default server host
                default_srv_id = controller.settings_store.get("default_server_id")
                default_srv = controller.server_store.get(default_srv_id)
                self.assertEqual(default_srv["host"], "120.27.210.184")

    def test_launch_error_path(self):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        
        # 1. Start with non-existent profile
        with self.assertRaises(BackendError) as ctx:
            controller.start_launch("invalid_profile_id")
        self.assertEqual(ctx.exception.code, "PROFILE_NOT_FOUND")
        
        # 2. Add profile with non-existent exe path
        prof = GameProfile(
            profile_id="test_prof",
            display_name="Test",
            exe_path="C:\\nonexistent_file_xyz.exe"
        )
        # Bypassing the check in add_profile since it checks file existence
        controller._profiles.append(prof)
        
        with self.assertRaises(BackendError) as ctx:
            controller.start_launch("test_prof")
        self.assertEqual(ctx.exception.code, "PROFILE_EXE_NOT_FOUND")

    @patch('subprocess.Popen')
    def test_doctor_command_safety_and_spaces(self, mock_popen):
        controller = BackendController(
            settings_path=self.temp_settings,
            servers_path=self.temp_servers,
            profiles_path=self.temp_profiles
        )
        
        mock_process = MagicMock()
        mock_process.pid = 9999
        mock_process.poll.return_value = None
        mock_popen.return_value = mock_process
        
        # Run with paths containing spaces (paths.py uses sys.executable or paths, let's patch paths.tools_dir)
        with patch('backend.doctor_runner.paths.tools_dir', return_value="C:\\Folder With Spaces\\tools"), \
             patch('backend.doctor_runner.paths.diagnostics_dir', return_value="C:\\Folder With Spaces\\diagnostics"), \
             patch('backend.doctor_runner.paths.logs_dir', return_value="C:\\Folder With Spaces\\logs"), \
             patch('os.path.isfile', return_value=True):
                 
            # Ensure open() doesn't fail on local files
            with patch('builtins.open', unittest.mock.mock_open()):
                started = controller.doctor_runner.run(
                    peer_ip="1.1.1.1",
                    interface="Ethernet 1",
                    server_host="vps.host.com"
                )
                self.assertTrue(started)
                
                # Check call args of Popen
                self.assertTrue(mock_popen.called)
                args, kwargs = mock_popen.call_args
                
                # First arg is command list
                cmd_list = args[0]
                self.assertIsInstance(cmd_list, list)
                
                # Verify shell is False
                self.assertFalse(kwargs.get('shell', True))
                
                # Verify separate parameters
                self.assertIn("1.1.1.1", cmd_list)
                self.assertIn("Ethernet 1", cmd_list)
                self.assertIn("vps.host.com", cmd_list)
                
                # Verify script path is separate argument, not combined shell string
                self.assertNotEqual(cmd_list[0], ' '.join(cmd_list))

if __name__ == "__main__":
    unittest.main()
