import os
import sys
import json
import re
from typing import List, Dict, Any, Optional

# Enable importing paths from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
import paths

class ServerStore:
    """Manages servers preset config, loading from example if not present."""

    def __init__(self, servers_path: str = None):
        if servers_path:
            self._servers_path = servers_path
        else:
            self._servers_path = os.path.join(paths.config_dir(), "servers.json")
        self._example_path = os.path.join(paths.config_dir(), "servers.example.json")
        self._servers: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        """Load servers from config/servers.json or config/servers.example.json."""
        if os.path.exists(self._servers_path):
            try:
                with open(self._servers_path, "r", encoding="utf-8") as f:
                    self._servers = json.load(f)
                return
            except Exception as e:
                print(f"Warning: Failed to load servers from {self._servers_path}: {e}", file=sys.stderr)

        # Fallback to example if it exists
        if os.path.exists(self._example_path):
            try:
                with open(self._example_path, "r", encoding="utf-8") as f:
                    self._servers = json.load(f)
                self.save()  # copy example to servers.json
                return
            except Exception as e:
                print(f"Warning: Failed to load example servers: {e}", file=sys.stderr)

        # Hard fallback default servers list
        self._servers = [
            {
                "server_id": "ririko_vps",
                "display_name": "Default VPS",
                "host": "120.27.210.184",
                "description": "Default Preview relay/signaling server",
                "enabled": True
            }
        ]
        self.save()

    def save(self) -> None:
        """Save servers to config/servers.json."""
        try:
            os.makedirs(os.path.dirname(self._servers_path), exist_ok=True)
            with open(self._servers_path, "w", encoding="utf-8") as f:
                json.dump(self._servers, f, indent=4, ensure_ascii=False)
        except Exception as e:
            raise IOError(f"Failed to save servers: {e}") from e

    def get_all(self) -> List[Dict[str, Any]]:
        """Get copy of all servers."""
        return [dict(s) for s in self._servers]

    def get(self, server_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific server."""
        for s in self._servers:
            if s.get("server_id") == server_id:
                return dict(s)
        return None

    def validate_host(self, host: str) -> bool:
        """Validate if host format is correct (IPv4, IPv6, or domain name)."""
        if not host or not isinstance(host, str):
            return False
        host = host.strip()
        if not host:
            return False
        # No spaces, no slashes, no colons except for IPv6/port, no question marks
        if " " in host or "/" in host or "?" in host:
            return False
        return True

    def add(self, display_name: str, host: str, description: str = "", enabled: bool = True) -> Dict[str, Any]:
        """Add a new server with validation."""
        host = host.strip()
        if not self.validate_host(host):
            raise ValueError("SERVER_INVALID_HOST")

        # Check duplicate host
        for s in self._servers:
            if s.get("host") == host:
                raise ValueError("SERVER_DUPLICATE_HOST")

        # Generate unique server_id
        import uuid
        server_id = f"srv_{uuid.uuid4().hex[:12]}"

        new_server = {
            "server_id": server_id,
            "display_name": display_name,
            "host": host,
            "description": description,
            "enabled": enabled
        }
        self._servers.append(new_server)
        self.save()
        return dict(new_server)

    def update(self, server_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing server."""
        server = None
        for s in self._servers:
            if s.get("server_id") == server_id:
                server = s
                break

        if not server:
            raise KeyError("SERVER_NOT_FOUND")

        # Validate host if updated
        if "host" in data:
            new_host = data["host"].strip()
            if not self.validate_host(new_host):
                raise ValueError("SERVER_INVALID_HOST")
            # Check duplicate host (excluding itself)
            for s in self._servers:
                if s.get("server_id") != server_id and s.get("host") == new_host:
                    raise ValueError("SERVER_DUPLICATE_HOST")
            server["host"] = new_host

        if "display_name" in data:
            server["display_name"] = data["display_name"]
        if "description" in data:
            server["description"] = data["description"]
        if "enabled" in data:
            server["enabled"] = bool(data["enabled"])

        self.save()
        return dict(server)

    def delete(self, server_id: str, default_server_id: str = "") -> None:
        """Delete an existing server."""
        if server_id == default_server_id:
            raise ValueError("SERVER_IS_DEFAULT")

        idx = -1
        for i, s in enumerate(self._servers):
            if s.get("server_id") == server_id:
                idx = i
                break

        if idx == -1:
            raise KeyError("SERVER_NOT_FOUND")

        self._servers.pop(idx)
        self.save()
