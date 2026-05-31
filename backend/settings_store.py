import os
import sys
import json
from typing import Any, Dict

# Enable importing paths from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
import paths

class SettingsStore:
    """Manages application settings config, loading from example if not present."""

    def __init__(self, settings_path: str = None):
        if settings_path:
            self._settings_path = settings_path
        else:
            self._settings_path = os.path.join(paths.config_dir(), "settings.json")
        self._example_path = os.path.join(paths.config_dir(), "settings.example.json")
        self._settings: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load settings from config/settings.json or config/settings.example.json."""
        if os.path.exists(self._settings_path):
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    self._settings = json.load(f)
                return
            except Exception as e:
                # If there's an error reading it, log or raise, or fallback to example
                print(f"Warning: Failed to load settings from {self._settings_path}: {e}", file=sys.stderr)

        # Fallback to example if it exists
        if os.path.exists(self._example_path):
            try:
                with open(self._example_path, "r", encoding="utf-8") as f:
                    self._settings = json.load(f)
                self.save()  # copy example to settings.json
                return
            except Exception as e:
                print(f"Warning: Failed to load example settings: {e}", file=sys.stderr)

        # Hard fallback default settings dict
        self._settings = {
            "default_server_id": "ririko_vps",
            "backend_api_port": 21520,
            "log_level": "INFO",
            "developer_mode": False,
            "theme": "dark"
        }
        self.save()

    def save(self) -> None:
        """Save settings to config/settings.json."""
        try:
            os.makedirs(os.path.dirname(self._settings_path), exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=4, ensure_ascii=False)
        except Exception as e:
            raise IOError(f"Failed to save settings: {e}") from e

    def get_all(self) -> Dict[str, Any]:
        """Get copy of all settings."""
        return dict(self._settings)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a specific setting."""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a setting value in memory (must call save() to persist)."""
        self._settings[key] = value
