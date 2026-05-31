import os
import sys
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Optional

# Enable importing paths from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
import paths

class DoctorRunner:
    """Manages the Network Doctor subprocess lifecycle, logging, and status checking."""

    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.process: Optional[subprocess.Popen] = None
        self.cmd_list: List[str] = []
        self.log_file_path: str = ""
        self._log_file_handle = None
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None
        self.task_id: str = ""
        self.start_time: Optional[str] = None

    def _doctor_tool_path(self) -> str:
        """Resolve the Network Doctor tool path based on running mode."""
        if getattr(sys, 'frozen', False):
            return os.path.join(paths.tools_dir(), "network_doctor.exe")
        else:
            return os.path.join(paths.tools_dir(), "network_doctor.py")

    def run(self,
            peer_ip: str = "",
            interface: str = "",
            server_host: str = "",
            no_zip: bool = False) -> bool:
        """Launch the Network Doctor subprocess. Returns True if successfully started."""
        if self.process and self.process.poll() is None:
            self.event_bus.publish("error", {
                "code": "DOCTOR_ALREADY_RUNNING",
                "message": "Network Doctor is already running"
            })
            return False

        tool_path = self._doctor_tool_path()
        diag_dir = paths.diagnostics_dir()
        logs_dir = paths.logs_dir()

        # Ensure directories exist
        os.makedirs(diag_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)

        # Build command list
        if getattr(sys, 'frozen', False):
            if not os.path.isfile(tool_path):
                self.event_bus.publish("error", {
                    "code": "DOCTOR_TOOL_NOT_FOUND",
                    "message": f"Network Doctor executable not found: {tool_path}"
                })
                return False
            self.cmd_list = [tool_path]
        else:
            if not os.path.isfile(tool_path):
                self.event_bus.publish("error", {
                    "code": "DOCTOR_TOOL_NOT_FOUND",
                    "message": f"Network Doctor script not found: {tool_path}"
                })
                return False
            self.cmd_list = [sys.executable, tool_path]

        self.cmd_list.extend(["--output-dir", diag_dir])

        if peer_ip.strip():
            self.cmd_list.extend(["--peer-ip", peer_ip.strip()])
        if interface.strip():
            self.cmd_list.extend(["--interface", interface.strip()])
        if server_host.strip():
            self.cmd_list.extend(["--server-host", server_host.strip()])
        if no_zip:
            self.cmd_list.append("--no-zip")

        # Setup log file
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = os.path.join(logs_dir, f"network_doctor_{timestamp_str}.log")
        self.task_id = f"doctor-run-{int(datetime.now().timestamp())}"
        self.start_time = datetime.now().isoformat()

        self.event_bus.publish("doctor_started", {
            "task_id": self.task_id,
            "cmd_list": self.cmd_list,
            "log_file_path": self.log_file_path
        })

        try:
            # Open log file for stdout/stderr redirection
            self._log_file_handle = open(self.log_file_path, "w", encoding="utf-8")
            
            # Start process
            self.process = subprocess.Popen(
                self.cmd_list,
                stdout=self._log_file_handle,
                stderr=subprocess.STDOUT,
                shell=False
            )
            self.pid = self.process.pid
            self.exit_code = None
            
            self.event_bus.publish("log", {
                "source": "Doctor",
                "level": "INFO",
                "message": f"Network Doctor process started with PID {self.pid}"
            })
            return True
        except Exception as e:
            if self._log_file_handle:
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None
            
            self.exit_code = -1
            self.process = None
            self.pid = None
            
            self.event_bus.publish("doctor_failed", {
                "task_id": self.task_id,
                "exit_code": -1,
                "error": f"Failed to start Network Doctor: {e}"
            })
            return False

    def poll(self) -> Optional[int]:
        """Poll the running process. Returns exit code if finished, else None."""
        if not self.process:
            return None

        rc = self.process.poll()
        if rc is not None:
            self.exit_code = rc
            
            # Close log file handle
            if self._log_file_handle:
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None
                
            self.process = None
            self.pid = None
            
            # Read log file to check for output ZIP filename if not using no_zip
            report_filename = ""
            if rc == 0 and os.path.exists(self.log_file_path):
                try:
                    # Let's search the log file for typical patterns:
                    # e.g., "Created zip archive: ...\s2pass_diagnostics_20260522_160000.zip"
                    with open(self.log_file_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in reversed(lines):
                        if "Created zip archive:" in line or "Created archive:" in line:
                            # Extract base filename
                            parts = line.strip().split()
                            if parts:
                                last_part = parts[-1]
                                report_filename = os.path.basename(last_part.strip("'\""))
                                break
                except Exception:
                    pass

            if rc == 0:
                self.event_bus.publish("doctor_finished", {
                    "task_id": self.task_id,
                    "exit_code": rc,
                    "report_filename": report_filename
                })
                # If report was created, emit report_created event
                if report_filename:
                    report_path = os.path.join(paths.diagnostics_dir(), report_filename)
                    size_bytes = 0
                    if os.path.exists(report_path):
                        size_bytes = os.path.getsize(report_path)
                    self.event_bus.publish("report_created", {
                        "filename": report_filename,
                        "size_bytes": size_bytes
                    })
            else:
                self.event_bus.publish("doctor_failed", {
                    "task_id": self.task_id,
                    "exit_code": rc,
                    "error": f"Network Doctor process exited with code {rc}"
                })

            return rc
        return None

    def get_status(self) -> Dict[str, Any]:
        """Get the current running status of the doctor runner."""
        is_running = self.process is not None and self.process.poll() is None
        return {
            "running": is_running,
            "last_exit_code": self.exit_code,
            "last_run_time": self.start_time
        }
