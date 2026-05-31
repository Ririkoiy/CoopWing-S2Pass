import os
import sys
import shlex
import subprocess
from typing import Optional
from adapters.base import AdapterBase
from adapters.profile import GameProfile

def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == 'win32':
        import ctypes
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        access = SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
        
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            err = kernel32.GetLastError()
            if err == 5: # ERROR_ACCESS_DENIED
                return True
            return False
        
        try:
            res = kernel32.WaitForSingleObject(handle, 0)
            return res == 258  # 258 is WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

class LaunchAdapter(AdapterBase):
    def __init__(self, profile: GameProfile):
        super().__init__(profile)
        self.pid: Optional[int] = None
        self._process: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self.is_running():
            return

        exe = self.profile.exe_path
        if not exe:
            raise ValueError("Executable path is not specified in the profile.")
        
        if not os.path.exists(exe):
            raise FileNotFoundError(f"Executable not found at path: {exe}")
        if not os.path.isfile(exe):
            raise ValueError(f"Path is not a file: {exe}")

        cwd = self.profile.working_dir
        if not cwd:
            cwd = os.path.dirname(os.path.abspath(exe))
        
        if not os.path.exists(cwd):
            raise FileNotFoundError(f"Working directory does not exist: {cwd}")

        # Parse arguments
        args = []
        if self.profile.launch_args:
            if sys.platform == 'win32':
                args = shlex.split(self.profile.launch_args, posix=False)
            else:
                args = shlex.split(self.profile.launch_args, posix=True)

        cmd = [exe] + args

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.pid = self._process.pid
        except Exception as e:
            self._process = None
            self.pid = None
            raise RuntimeError(f"Failed to launch process '{exe}': {e}") from e

    def stop(self) -> None:
        # Only terminate process started by this LaunchAdapter instance.
        if not self._process:
            return
            
        # If the process has already exited, stop() should be a no-op.
        if self._process.poll() is not None:
            self._process = None
            self.pid = None
            return

        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        except Exception:
            pass
        finally:
            self._process = None
            self.pid = None

    def is_running(self) -> bool:
        # Prefer subprocess.Popen.poll()
        if self._process:
            status = self._process.poll()
            if status is None:
                return True
            else:
                self._process = None
                self.pid = None
                return False
        
        # Fallback to checking by PID
        if self.pid:
            running = is_pid_running(self.pid)
            if not running:
                self.pid = None
            return running

        return False

    def get_pid(self) -> Optional[int]:
        if self.is_running():
            return self.pid
        return None
