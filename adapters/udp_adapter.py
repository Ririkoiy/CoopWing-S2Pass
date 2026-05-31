import socket
import threading
from typing import Optional, Dict, Any, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile

class GenericUdpForwardAdapter(AdapterBase):
    """
    Generic UDP Forward Adapter for S2Pass.
    Supports local plumbing: binding, receiving, forwarding/echoing, counting, and port release.
    """
    def __init__(self, profile: GameProfile, mode: str = "echo"):
        super().__init__(profile)
        if mode not in ("echo", "forward"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'echo' or 'forward'.")
        self.mode = mode

        # Validation for forward mode parameters
        if self.mode == "forward":
            if not self.profile.remote_target_host:
                raise ValueError("remote_target_host is required for forward mode")
            if not self.profile.remote_target_port:
                raise ValueError("remote_target_port is required for forward mode")

        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._is_running = False

        # Bound address details
        self.local_host: Optional[str] = None
        self.local_port: Optional[int] = None

        # Statistics and counters
        self.received_packets = 0
        self.sent_packets = 0
        self.received_bytes = 0
        self.sent_bytes = 0
        self.last_peer_addr: Optional[Tuple[str, int]] = None
        self.last_error: Optional[str] = None

        # Phase 2 integration status markers
        self.core_integration = "pending"
        self.udp_adapter_plumbing = "implemented"
        self.real_s2pass_transport_integration = "not implemented"

    def start(self) -> None:
        """
        Starts the UDP adapter, binds the socket, and launches the background receiver thread.
        """
        if self._is_running:
            return

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Apply standard SO_REUSEADDR option
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Use short timeout as requested for stopping check
        self.sock.settimeout(0.2)

        bind_host = self.profile.local_bind_host or "127.0.0.1"
        bind_port = self.profile.local_bind_port if self.profile.local_bind_port is not None else 0

        try:
            self.sock.bind((bind_host, bind_port))
        except OSError as e:
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            raise RuntimeError(f"Failed to bind UDP socket to {bind_host}:{bind_port}: {e}") from e

        # Retrieve actual bound port & host
        self.local_host, self.local_port = self.sock.getsockname()

        # Check self-loop in forward mode
        if self.mode == "forward":
            is_self = False
            if self.local_port == self.profile.remote_target_port:
                try:
                    local_ips = {self.local_host}
                    if self.local_host == "0.0.0.0":
                        local_ips.update({"127.0.0.1", "0.0.0.0"})
                        try:
                            local_ips.add(socket.gethostbyname("localhost"))
                        except Exception:
                            pass
                    remote_ip = socket.gethostbyname(self.profile.remote_target_host)
                    if remote_ip in local_ips:
                        is_self = True
                except Exception:
                    # Fallback to string comparison if DNS resolution fails
                    if self.profile.remote_target_host == self.local_host or \
                       (self.local_host == "0.0.0.0" and self.profile.remote_target_host in ("127.0.0.1", "localhost")):
                        is_self = True

            if is_self:
                # Clean up socket before raising error
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.local_host = None
                self.local_port = None
                raise ValueError(
                    f"Self-loop detected: remote target {self.profile.remote_target_host}:{self.profile.remote_target_port} "
                    f"points back to the bound address {self.profile.local_bind_host}:{bind_port}"
                )

        self.stop_event.clear()
        self.thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._is_running = True
        self.thread.start()

    def stop(self) -> None:
        """
        Stops the receiver thread, closes the socket to release the port, and resets state.
        """
        if not self._is_running:
            return

        self.stop_event.set()

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

        self.local_host = None
        self.local_port = None
        self._is_running = False

    def is_running(self) -> bool:
        """
        Check if the adapter is currently running.
        """
        return self._is_running

    def get_pid(self) -> Optional[int]:
        """
        UDP adapter doesn't spawn any subprocess, returns None.
        """
        return None

    def get_local_addr(self) -> Tuple[Optional[str], Optional[int]]:
        """
        Returns the actually bound host and port.
        """
        return self.local_host, self.local_port

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns the stats of the adapter.
        """
        return {
            "running": self._is_running,
            "local_host": self.local_host,
            "local_port": self.local_port,
            "received_packets": self.received_packets,
            "sent_packets": self.sent_packets,
            "received_bytes": self.received_bytes,
            "sent_bytes": self.sent_bytes,
            "last_peer_addr": self.last_peer_addr,
            "last_error": self.last_error,
            "core_integration": self.core_integration,
            "udp_adapter_plumbing": self.udp_adapter_plumbing,
            "real_s2pass_transport_integration": self.real_s2pass_transport_integration
        }

    def _recv_loop(self) -> None:
        """
        Background thread loop to receive UDP packets.
        """
        while not self.stop_event.is_set():
            try:
                # 65535 is maximum theoretical UDP packet size
                data, addr = self.sock.recvfrom(65535)
                self.received_packets += 1
                self.received_bytes += len(data)
                self.last_peer_addr = addr

                if self.mode == "echo":
                    self.sock.sendto(data, addr)
                    self.sent_packets += 1
                    self.sent_bytes += len(data)
                elif self.mode == "forward":
                    target = (self.profile.remote_target_host, self.profile.remote_target_port)
                    self.sock.sendto(data, target)
                    self.sent_packets += 1
                    self.sent_bytes += len(data)
            except socket.timeout:
                continue
            except (OSError, ValueError) as e:
                # If stop_event is set or socket is already closed/None, this is a normal shutdown exception.
                # Adjustment 4: do not record as last_error
                if self.stop_event.is_set() or self.sock is None:
                    break
                self.last_error = str(e)
                break
            except Exception as e:
                if self.stop_event.is_set() or self.sock is None:
                    break
                self.last_error = str(e)
                break
