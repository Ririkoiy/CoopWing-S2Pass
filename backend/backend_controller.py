import os
import sys
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

# Enable importing from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import paths
from adapters.profile import GameProfile, load_profiles, save_profiles
from adapters.launch_adapter import LaunchAdapter
from adapters.udp_adapter import GenericUdpForwardAdapter

from backend.settings_store import SettingsStore
from backend.server_store import ServerStore
from backend.event_bus import EventBus
from backend.doctor_runner import DoctorRunner
from backend.api_models import BackendError

class BackendController:
    """Core controller for S2Pass Backend, running independently of DPG and Flutter UI."""

    def __init__(self, settings_path: Optional[str] = None, servers_path: Optional[str] = None, profiles_path: Optional[str] = None):
        # Event Bus
        self.event_bus = EventBus()

        # Stores
        self.settings_store = SettingsStore(settings_path)
        self.server_store = ServerStore(servers_path)

        if profiles_path:
            self._profiles_path = profiles_path
        else:
            self._profiles_path = os.path.join(paths.config_dir(), "profiles.json")

        self._profiles: List[GameProfile] = []
        self.load_all_profiles()

        # Running Adapters
        self._launch_adapter: Optional[LaunchAdapter] = None
        self._launch_profile_id: Optional[str] = None

        self._udp_adapter: Optional[GenericUdpForwardAdapter] = None
        self._udp_profile_id: Optional[str] = None

        # Doctor Runner
        self.doctor_runner = DoctorRunner(self.event_bus)

        # Register event bus listener for logging
        self.event_bus.register_listener(self._on_event)

        # Emit backend_ready
        self.event_bus.publish("backend_ready", {
            "version": "0.2.0-preview"
        })

    def _on_event(self, event_obj: Dict[str, Any]) -> None:
        """Route internal events optionally to general logging or tracking."""
        pass

    # ------------------------------------------------------------------
    # Profiles CRUD
    # ------------------------------------------------------------------

    def load_all_profiles(self) -> List[GameProfile]:
        """Load profiles from JSON storage."""
        try:
            self._profiles = load_profiles(self._profiles_path)
            self.event_bus.publish("profile_loaded", {
                "count": len(self._profiles)
            })
        except Exception as e:
            self._profiles = []
            self.event_bus.publish("error", {
                "code": "INTERNAL_ERROR",
                "message": f"Failed to load profiles: {e}"
            })
        return list(self._profiles)

    def save_all_profiles(self) -> None:
        """Persist profiles to JSON storage."""
        try:
            os.makedirs(os.path.dirname(self._profiles_path), exist_ok=True)
            save_profiles(self._profiles, self._profiles_path)
            self.event_bus.publish("profile_saved", {
                "count": len(self._profiles)
            })
        except Exception as e:
            self.event_bus.publish("error", {
                "code": "INTERNAL_ERROR",
                "message": f"Failed to save profiles: {e}"
            })
            raise BackendError("INTERNAL_ERROR", f"Failed to save profiles: {e}")

    def get_profiles(self) -> List[GameProfile]:
        """Get in-memory profiles."""
        return list(self._profiles)

    def get_profile(self, profile_id: str) -> GameProfile:
        """Retrieve a specific profile or raise PROFILE_NOT_FOUND."""
        for p in self._profiles:
            if p.profile_id == profile_id:
                return p
        raise BackendError("PROFILE_NOT_FOUND", f"Profile '{profile_id}' not found.")

    def add_profile(self, profile: GameProfile) -> GameProfile:
        """Add and save a profile. Validates exe_path if present."""
        if not profile.profile_id:
            profile.profile_id = str(uuid.uuid4())
            
        # Basic check if exe_path exists
        if profile.exe_path:
            if not os.path.exists(profile.exe_path):
                raise BackendError("PROFILE_EXE_NOT_FOUND", f"Executable path does not exist: {profile.exe_path}")

        self._profiles.append(profile)
        self.save_all_profiles()
        return profile

    def update_profile(self, profile_id: str, updated_fields: Dict[str, Any]) -> GameProfile:
        """Update fields of an existing profile."""
        profile = self.get_profile(profile_id)
        
        # Check if the profile is currently in use
        if self._launch_profile_id == profile_id or self._udp_profile_id == profile_id:
            raise BackendError("PROFILE_IN_USE", f"Profile '{profile_id}' is currently running and cannot be updated.")

        # Update allowed fields
        for k, v in updated_fields.items():
            if hasattr(profile, k) and k != "profile_id":
                # Validate exe_path if it's changing
                if k == "exe_path" and v:
                    if not os.path.exists(v):
                        raise BackendError("PROFILE_EXE_NOT_FOUND", f"Executable path does not exist: {v}")
                setattr(profile, k, v)

        # Trigger __post_init__ to recalculate working_dir if it was not manually specified
        if "exe_path" in updated_fields and "working_dir" not in updated_fields:
            profile.__post_init__()

        self.save_all_profiles()
        return profile

    def delete_profile(self, profile_id: str) -> None:
        """Delete an existing profile."""
        profile = self.get_profile(profile_id)

        if self._launch_profile_id == profile_id or self._udp_profile_id == profile_id:
            raise BackendError("PROFILE_IN_USE", f"Profile '{profile_id}' is currently running and cannot be deleted.")

        self._profiles.remove(profile)
        self.save_all_profiles()
        self.event_bus.publish("profile_deleted", {
            "profile_id": profile_id,
            "display_name": profile.display_name
        })

    # ------------------------------------------------------------------
    # Profile Draft from Exe Path
    # ------------------------------------------------------------------

    def create_profile_draft_from_exe(self, exe_path: str) -> GameProfile:
        """Generate a profile draft from a dragged-in .exe path."""
        if not exe_path or not isinstance(exe_path, str):
            raise BackendError("PROFILE_INVALID_EXE", "Path is empty or invalid.")

        exe_path = exe_path.strip()
        if not exe_path:
            raise BackendError("PROFILE_INVALID_EXE", "Path is empty or invalid.")

        # Check .exe extension
        if not exe_path.lower().endswith(".exe"):
            raise BackendError("PROFILE_INVALID_EXE", "File must have a .exe extension.")

        # Check exists
        if not os.path.exists(exe_path):
            raise BackendError("PROFILE_EXE_NOT_FOUND", "Executable file not found on disk.")

        # Check if directory
        if os.path.isdir(exe_path):
            raise BackendError("PROFILE_EXE_IS_DIRECTORY", "Path points to a directory, not a file.")

        # Check access/permission
        try:
            with open(exe_path, "rb") as f:
                # Read 1 byte to ensure read permissions
                f.read(1)
        except PermissionError:
            raise BackendError("PROFILE_EXE_ACCESS_DENIED", "Access to the file was denied due to permission constraints.")
        except Exception as e:
            raise BackendError("PROFILE_EXE_ACCESS_DENIED", f"Cannot access the file: {e}")

        # Construct GameProfile draft
        filename = os.path.basename(exe_path)
        display_name = os.path.splitext(filename)[0]
        working_dir = os.path.dirname(os.path.abspath(exe_path))

        draft = GameProfile(
            profile_id="",  # empty for draft
            display_name=display_name,
            exe_path=exe_path,
            working_dir=working_dir,
            adapter_type="launch_only",
            protocol="",
            local_bind_host="127.0.0.1",
            local_bind_port=None,
            remote_target_host="",
            remote_target_port=None,
            expected_ports=[],
            doctor_profile={},
            notes=""
        )

        self.event_bus.publish("game_profile_created_from_exe", {
            "display_name": display_name,
            "exe_path": exe_path
        })

        return draft

    # ------------------------------------------------------------------
    # Process Launch
    # ------------------------------------------------------------------

    def start_launch(self, profile_id: str) -> int:
        """Start a game process via LaunchAdapter."""
        profile = self.get_profile(profile_id)

        if self._launch_adapter and self._launch_adapter.is_running():
            raise BackendError("LAUNCH_ALREADY_RUNNING", "A game process is already running.")

        # Double check exe path exists
        if not profile.exe_path or not os.path.exists(profile.exe_path):
            raise BackendError("PROFILE_EXE_NOT_FOUND", f"Game executable not found: {profile.exe_path}")

        # Check if path is a directory
        if os.path.isdir(profile.exe_path):
            raise BackendError("PROFILE_EXE_IS_DIRECTORY", f"Game path is a directory: {profile.exe_path}")

        self._launch_adapter = LaunchAdapter(profile)
        try:
            self._launch_adapter.start()
            pid = self._launch_adapter.get_pid()
            self._launch_profile_id = profile_id
            
            self.event_bus.publish("launch_started", {
                "profile_id": profile_id,
                "pid": pid
            })
            return pid
        except Exception as e:
            self._launch_adapter = None
            self._launch_profile_id = None
            self.event_bus.publish("launch_failed", {
                "profile_id": profile_id,
                "error": str(e)
            })
            raise BackendError("LAUNCH_FAILED", f"Launch failed: {e}")

    def stop_launch(self) -> None:
        """Stop the currently launched game process."""
        if not self._launch_adapter or not self._launch_adapter.is_running():
            raise BackendError("LAUNCH_NOT_RUNNING", "No game process is currently running.")

        profile_id = self._launch_profile_id
        try:
            self._launch_adapter.stop()
            self.event_bus.publish("launch_stopped", {
                "profile_id": profile_id,
                "exit_code": 0
            })
        except Exception as e:
            self.event_bus.publish("error", {
                "code": "INTERNAL_ERROR",
                "message": f"Failed to stop launch process cleanly: {e}"
            })
        finally:
            self._launch_adapter = None
            self._launch_profile_id = None

    def get_launch_status(self) -> Dict[str, Any]:
        """Retrieve launch status."""
        is_running = self._launch_adapter is not None and self._launch_adapter.is_running()
        return {
            "running": is_running,
            "pid": self._launch_adapter.get_pid() if is_running else None,
            "profile_id": self._launch_profile_id if is_running else None
        }

    # ------------------------------------------------------------------
    # UDP Adapter
    # ------------------------------------------------------------------

    def start_udp(self, profile_id: str, mode: str = "echo") -> Dict[str, Any]:
        """Start UDP adapter for a profile."""
        profile = self.get_profile(profile_id)

        # Validate adapter_type is generic_udp_forward (not launch_only/diagnostics_only)
        if profile.adapter_type != "generic_udp_forward":
            raise BackendError("PROFILE_NOT_UDP_TYPE", f"Profile adapter type '{profile.adapter_type}' is not UDP-compatible.")

        if self._udp_adapter and self._udp_adapter.is_running():
            raise BackendError("UDP_ALREADY_RUNNING", "UDP adapter is already running.")

        try:
            self._udp_adapter = GenericUdpForwardAdapter(profile, mode=mode)
            self._udp_adapter.start()
            self._udp_profile_id = profile_id
            
            host, port = self._udp_adapter.get_local_addr()
            local_addr = f"{host}:{port}"
            
            self.event_bus.publish("udp_started", {
                "profile_id": profile_id,
                "local_addr": local_addr,
                "mode": mode
            })
            
            return {
                "local_addr": local_addr,
                "mode": mode
            }
        except Exception as e:
            self._udp_adapter = None
            self._udp_profile_id = None
            raise BackendError("UDP_BIND_FAILED", f"Failed to bind UDP adapter: {e}")

    def stop_udp(self) -> None:
        """Stop the running UDP adapter."""
        if not self._udp_adapter or not self._udp_adapter.is_running():
            raise BackendError("UDP_NOT_RUNNING", "UDP adapter is not running.")

        profile_id = self._udp_profile_id
        try:
            self._udp_adapter.stop()
            self.event_bus.publish("udp_stopped", {
                "profile_id": profile_id
            })
        except Exception as e:
            self.event_bus.publish("error", {
                "code": "INTERNAL_ERROR",
                "message": f"Failed to stop UDP adapter cleanly: {e}"
            })
        finally:
            self._udp_adapter = None
            self._udp_profile_id = None

    def get_udp_status(self) -> Dict[str, Any]:
        """Get UDP adapter status and stats."""
        is_running = self._udp_adapter is not None and self._udp_adapter.is_running()
        if not is_running:
            return {"running": False}

        stats = self._udp_adapter.get_stats()
        # Keep stats properties in sync with get_stats output
        host, port = self._udp_adapter.get_local_addr()
        return {
            "running": True,
            "mode": self._udp_adapter._mode,
            "local_addr": f"{host}:{port}",
            "received_packets": stats.get("received_packets", 0),
            "sent_packets": stats.get("sent_packets", 0),
            "received_bytes": stats.get("received_bytes", 0),
            "sent_bytes": stats.get("sent_bytes", 0)
        }

    # ------------------------------------------------------------------
    # Network Doctor reports
    # ------------------------------------------------------------------

    def get_doctor_reports(self) -> List[Dict[str, Any]]:
        """List and parse available Network Doctor reports in diagnostics directory."""
        diag_dir = paths.diagnostics_dir()
        if not os.path.exists(diag_dir):
            return []

        reports = []
        try:
            entries = os.listdir(diag_dir)
        except Exception:
            return []

        for name in entries:
            full_path = os.path.join(diag_dir, name)
            
            # Zip file report
            if os.path.isfile(full_path) and name.lower().endswith(".zip"):
                mtime = os.path.getmtime(full_path)
                created_at = datetime.fromtimestamp(mtime).isoformat()
                
                # Check if matching directory exists to locate summary.json
                base_name = name[:-4]
                matching_dir = os.path.join(diag_dir, base_name)
                summary_path = None
                if os.path.isdir(matching_dir):
                    candidate_summary = os.path.join(matching_dir, "summary.json")
                    if os.path.exists(candidate_summary):
                        summary_path = os.path.abspath(candidate_summary)

                reports.append({
                    "filename": name,
                    "created_at": created_at,
                    "size_bytes": os.path.getsize(full_path),
                    "report_type": "zip",
                    "summary_path": summary_path,
                    "zip_path": os.path.abspath(full_path)
                })

            # Directory report
            elif os.path.isdir(full_path):
                # Ignore folder if it matches a zip filename and isn't the primary report directory
                # Check for summary.json inside the directory
                summary_file = os.path.join(full_path, "summary.json")
                if os.path.exists(summary_file):
                    mtime = os.path.getmtime(full_path)
                    created_at = datetime.fromtimestamp(mtime).isoformat()
                    
                    reports.append({
                        "filename": name,
                        "created_at": created_at,
                        "size_bytes": None,
                        "report_type": "directory",
                        "summary_path": os.path.abspath(summary_file),
                        "zip_path": None
                    })

        # Sort reports by created_at descending
        reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return reports

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Cleanup all active components."""
        if self._launch_adapter:
            try:
                self.stop_launch()
            except Exception:
                pass
        if self._udp_adapter:
            try:
                self.stop_udp()
            except Exception:
                pass
        if self.doctor_runner and self.doctor_runner.process:
            try:
                self.doctor_runner.process.terminate()
                self.doctor_runner.process.wait(timeout=2.0)
            except Exception:
                pass
