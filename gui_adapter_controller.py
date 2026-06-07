"""
S2Pass GUI Adapter Controller — Preview 0.1

Lightweight controller that mediates between DearPyGui callbacks and the
adapter / tools layer.  DearPyGui callbacks never directly touch sockets,
processes, or file I/O — they call controller methods instead.

This module has NO dependency on DearPyGui.
"""

import os
import sys
import subprocess
import time
from typing import List, Optional, Callable, Dict, Any

import paths
from adapters.profile import GameProfile, load_profiles, save_profiles
from adapters.launch_adapter import LaunchAdapter
from adapters.udp_adapter import GenericUdpForwardAdapter


class GuiAdapterController:
    """Controller for the Game Adapter tab in the GUI."""

    def __init__(self, log_callback: Optional[Callable[[str, str, str], None]] = None):
        """
        Args:
            log_callback: Optional function(source, level, message) called on
                          every log event.  The GUI sets this to route events
                          into the adapter log widget.
        """
        self._log_callback = log_callback

        # Profile state
        self._profiles: List[GameProfile] = []
        self._selected_index: int = -1

        # Adapter instances (one at a time)
        self._launch_adapter: Optional[LaunchAdapter] = None
        self._udp_adapter: Optional[GenericUdpForwardAdapter] = None

        # Network Doctor subprocess handle
        self._doctor_process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def append_log(self, source: str, level: str, message: str) -> None:
        """Emit a log event through the registered callback."""
        if self._log_callback:
            self._log_callback(source, level, message)

    def set_log_callback(self, cb: Callable[[str, str, str], None]) -> None:
        """Set or replace the log callback."""
        self._log_callback = cb

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def _profiles_path(self) -> str:
        return os.path.join(paths.config_dir(), "profiles.json")

    def load_all_profiles(self) -> List[GameProfile]:
        """Load profiles from config/profiles.json.  Returns empty list if
        the file does not exist.  Never crashes on missing file."""
        try:
            self._profiles = load_profiles(self._profiles_path())
            self.append_log("Profile", "INFO",
                            f"Loaded {len(self._profiles)} profile(s)")
        except Exception as e:
            self._profiles = []
            self.append_log("Profile", "ERROR", f"Failed to load profiles: {e}")
        self._selected_index = -1
        return list(self._profiles)

    def save_all_profiles(self) -> None:
        """Save current profiles to config/profiles.json.
        Creates config/ directory on demand."""
        try:
            cfg = paths.config_dir()
            if not os.path.exists(cfg):
                os.makedirs(cfg, exist_ok=True)
            save_profiles(self._profiles, self._profiles_path())
            self.append_log("Profile", "INFO",
                            f"Saved {len(self._profiles)} profile(s)")
        except Exception as e:
            self.append_log("Profile", "ERROR", f"Failed to save profiles: {e}")

    def get_profiles(self) -> List[GameProfile]:
        """Return the current in-memory profile list."""
        return list(self._profiles)

    def get_profile_names(self) -> List[str]:
        """Return display names for the listbox."""
        return [p.display_name or p.profile_id for p in self._profiles]

    def add_profile(self, profile: GameProfile) -> None:
        """Add a profile to the in-memory list (does not auto-save)."""
        self._profiles.append(profile)
        self.append_log("Profile", "INFO",
                        f"Added profile: {profile.display_name}")

    def update_profile(self, index: int, profile: GameProfile) -> None:
        """Replace the profile at *index* (does not auto-save)."""
        if 0 <= index < len(self._profiles):
            self._profiles[index] = profile
            self.append_log("Profile", "INFO",
                            f"Updated profile: {profile.display_name}")

    def remove_profile(self, index: int) -> None:
        """Remove the profile at *index* (does not auto-save)."""
        if 0 <= index < len(self._profiles):
            name = self._profiles[index].display_name
            del self._profiles[index]
            if self._selected_index >= len(self._profiles):
                self._selected_index = len(self._profiles) - 1
            self.append_log("Profile", "INFO", f"Removed profile: {name}")

    def select_profile(self, index: int) -> Optional[GameProfile]:
        """Select a profile by index.  Returns the profile or None."""
        if 0 <= index < len(self._profiles):
            self._selected_index = index
            p = self._profiles[index]
            self.append_log("Profile", "INFO",
                            f"Selected profile: {p.display_name}")
            return p
        self._selected_index = -1
        return None

    @property
    def selected_index(self) -> int:
        return self._selected_index

    @property
    def selected_profile(self) -> Optional[GameProfile]:
        if 0 <= self._selected_index < len(self._profiles):
            return self._profiles[self._selected_index]
        return None

    # ------------------------------------------------------------------
    # LaunchAdapter
    # ------------------------------------------------------------------

    def start_launch(self, profile: GameProfile) -> Optional[int]:
        """Start a game process via LaunchAdapter.  Returns PID on success."""
        if self._launch_adapter and self._launch_adapter.is_running():
            self.append_log("Launch", "WARN",
                            "A process is already running — stop it first")
            return self._launch_adapter.get_pid()

        self._launch_adapter = LaunchAdapter(profile)
        try:
            self._launch_adapter.start()
            pid = self._launch_adapter.get_pid()
            self.append_log("Launch", "INFO",
                            f"Process started: PID={pid}")
            return pid
        except Exception as e:
            self.append_log("Launch", "ERROR", f"Launch failed: {e}")
            self._launch_adapter = None
            return None

    def stop_launch(self) -> None:
        """Stop the currently launched process (only the one we started)."""
        if not self._launch_adapter:
            self.append_log("Launch", "INFO", "No process to stop")
            return
        try:
            self._launch_adapter.stop()
            self.append_log("Launch", "INFO", "Process stopped")
        except Exception as e:
            self.append_log("Launch", "ERROR", f"Stop failed: {e}")
        finally:
            self._launch_adapter = None

    def get_launch_status(self) -> Dict[str, Any]:
        """Return launch adapter status dict."""
        if not self._launch_adapter:
            return {"running": False, "pid": None}
        return {
            "running": self._launch_adapter.is_running(),
            "pid": self._launch_adapter.get_pid(),
        }

    # ------------------------------------------------------------------
    # GenericUdpForwardAdapter
    # ------------------------------------------------------------------

    def start_udp(self, profile: GameProfile, mode: str = "echo") -> bool:
        """Start the UDP adapter.  Returns True on success."""
        if self._udp_adapter and self._udp_adapter.is_running():
            self.append_log("UDP", "WARN",
                            "UDP Adapter is already running — stop it first")
            return False

        try:
            self._udp_adapter = GenericUdpForwardAdapter(profile, mode=mode)
            self._udp_adapter.start()
            host, port = self._udp_adapter.get_local_addr()
            self.append_log("UDP", "INFO",
                            f"UDP Adapter started ({mode} mode) on "
                            f"{host}:{port}")
            return True
        except Exception as e:
            self.append_log("UDP", "ERROR", f"UDP Adapter start failed: {e}")
            self._udp_adapter = None
            return False

    def stop_udp(self) -> None:
        """Stop the UDP adapter and release the port."""
        if not self._udp_adapter:
            self.append_log("UDP", "INFO", "No UDP Adapter to stop")
            return
        try:
            self._udp_adapter.stop()
            self.append_log("UDP", "INFO", "UDP Adapter stopped")
        except Exception as e:
            self.append_log("UDP", "ERROR", f"UDP Adapter stop failed: {e}")
        finally:
            self._udp_adapter = None

    def get_udp_stats(self) -> Dict[str, Any]:
        """Return UDP adapter stats dict."""
        if not self._udp_adapter:
            return {"running": False}
        return self._udp_adapter.get_stats()

    # ------------------------------------------------------------------
    # Network Doctor
    # ------------------------------------------------------------------

    def _doctor_tool_path(self) -> str:
        """Resolve the Network Doctor tool path.

        - Source mode: sys.executable + tools/network_doctor.py
        - Frozen mode: tools/network_doctor.exe beside the packaged app
        """
        if getattr(sys, 'frozen', False):
            return os.path.join(paths.tools_dir(), "network_doctor.exe")
        else:
            return os.path.join(paths.tools_dir(), "network_doctor.py")

    def run_network_doctor(self,
                           peer_ip: str = "",
                           interface: str = "",
                           server_host: str = "",
                           no_zip: bool = False) -> bool:
        """Launch Network Doctor as a subprocess.  Returns True if started."""
        if self._doctor_process and self._doctor_process.poll() is None:
            self.append_log("Doctor", "WARN",
                            "Network Doctor is already running")
            return False

        tool_path = self._doctor_tool_path()
        diag = paths.diagnostics_dir()

        # Build command
        if getattr(sys, 'frozen', False):
            # Frozen mode: run the packaged exe directly
            if not os.path.isfile(tool_path):
                self.append_log("Doctor", "ERROR",
                                f"Network Doctor tool not found: {tool_path}")
                return False
            cmd = [tool_path]
        else:
            # Source mode: python tools/network_doctor.py
            if not os.path.isfile(tool_path):
                self.append_log("Doctor", "ERROR",
                                f"Network Doctor script not found: {tool_path}")
                return False
            cmd = [sys.executable, tool_path]

        cmd.extend(["--output-dir", diag])

        if peer_ip.strip():
            cmd.extend(["--peer-ip", peer_ip.strip()])
        if interface.strip():
            cmd.extend(["--interface", interface.strip()])
        if server_host.strip():
            cmd.extend(["--server-host", server_host.strip()])
        if no_zip:
            cmd.append("--no-zip")

        self.append_log("Doctor", "INFO",
                        f"Starting Network Doctor: {' '.join(cmd)}")
        try:
            self._doctor_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.append_log("Doctor", "INFO",
                            f"Network Doctor started (PID={self._doctor_process.pid})")
            return True
        except Exception as e:
            self.append_log("Doctor", "ERROR",
                            f"Failed to start Network Doctor: {e}")
            self._doctor_process = None
            return False

    def poll_doctor(self) -> Optional[int]:
        """Check if doctor process has finished.  Returns exit code or None."""
        if not self._doctor_process:
            return None
        rc = self._doctor_process.poll()
        if rc is not None:
            self.append_log("Doctor", "INFO",
                            f"Network Doctor finished (exit code {rc})")
            self._doctor_process = None
            return rc
        return None

    def open_diagnostics_dir(self) -> None:
        """Open the diagnostics directory in the system file browser."""
        diag = paths.diagnostics_dir()
        if not os.path.exists(diag):
            os.makedirs(diag, exist_ok=True)
        try:
            os.startfile(diag)
            self.append_log("Doctor", "INFO",
                            f"Opened diagnostics directory: {diag}")
        except Exception as e:
            self.append_log("Doctor", "ERROR",
                            f"Failed to open diagnostics directory: {e}")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Stop all running adapters and subprocesses.  Called on GUI exit."""
        if self._launch_adapter and self._launch_adapter.is_running():
            try:
                self._launch_adapter.stop()
            except Exception:
                pass
            self._launch_adapter = None

        if self._udp_adapter and self._udp_adapter.is_running():
            try:
                self._udp_adapter.stop()
            except Exception:
                pass
            self._udp_adapter = None

        if self._doctor_process and self._doctor_process.poll() is None:
            try:
                self._doctor_process.terminate()
                self._doctor_process.wait(timeout=2.0)
            except Exception:
                pass
            self._doctor_process = None
