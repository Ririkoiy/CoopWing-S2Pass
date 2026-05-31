import os
import unittest
import tempfile
import json
from backend.settings_store import SettingsStore
from backend.server_store import ServerStore

class TestBackendSettings(unittest.TestCase):
    def setUp(self):
        # Create temp files for settings and servers so we don't touch actual user config during test
        self.temp_settings = tempfile.mktemp(suffix=".json")
        self.temp_servers = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        if os.path.exists(self.temp_settings):
            os.remove(self.temp_settings)
        if os.path.exists(self.temp_servers):
            os.remove(self.temp_servers)

    def test_settings_store_fallback(self):
        # File doesn't exist, example doesn't exist (since paths.config_dir() is mocked/different or temp)
        # Should fallback to hardcoded default values
        store = SettingsStore(self.temp_settings)
        self.assertEqual(store.get("backend_api_port"), 21520)
        self.assertEqual(store.get("theme"), "dark")
        self.assertTrue(os.path.exists(self.temp_settings))

    def test_settings_store_get_set(self):
        store = SettingsStore(self.temp_settings)
        store.set("theme", "light")
        store.set("backend_api_port", 9999)
        store.save()
        
        # Load fresh copy
        store2 = SettingsStore(self.temp_settings)
        self.assertEqual(store2.get("theme"), "light")
        self.assertEqual(store2.get("backend_api_port"), 9999)

    def test_server_store_fallback(self):
        store = ServerStore(self.temp_servers)
        servers = store.get_all()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["server_id"], "ririko_vps")
        self.assertEqual(servers[0]["host"], "120.27.210.184")
        self.assertTrue(os.path.exists(self.temp_servers))

    def test_server_store_crud(self):
        store = ServerStore(self.temp_servers)
        
        # Add server
        new_srv = store.add(display_name="Test Srv", host="1.2.3.4", description="A test host")
        self.assertEqual(new_srv["display_name"], "Test Srv")
        self.assertEqual(new_srv["host"], "1.2.3.4")
        
        # Verify get
        retrieved = store.get(new_srv["server_id"])
        self.assertEqual(retrieved["display_name"], "Test Srv")
        
        # Add duplicate host should fail
        with self.assertRaises(ValueError):
            store.add(display_name="Duplicate Srv", host="1.2.3.4")
            
        # Add invalid host should fail
        with self.assertRaises(ValueError):
            store.add(display_name="Bad Srv", host="invalid host with spaces")

        # Update server
        store.update(new_srv["server_id"], {"display_name": "Updated Srv", "host": "1.2.3.5"})
        retrieved2 = store.get(new_srv["server_id"])
        self.assertEqual(retrieved2["display_name"], "Updated Srv")
        self.assertEqual(retrieved2["host"], "1.2.3.5")
        
        # Delete default server should fail
        with self.assertRaises(ValueError):
            store.delete("ririko_vps", default_server_id="ririko_vps")
            
        # Delete server
        store.delete(new_srv["server_id"])
        self.assertIsNone(store.get(new_srv["server_id"]))

if __name__ == "__main__":
    unittest.main()
