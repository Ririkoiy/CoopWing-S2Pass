import socket
import threading
from typing import Optional, Tuple

from adapters.base import AdapterBase
from adapters.profile import GameProfile
from adapters.transport import Transport

class LocalUdpBridgeAdapter(AdapterBase):
    """
    Local UDP Bridge Adapter.
    Listens on a local UDP socket, forwards local game bytes to the Transport,
    and forwards incoming Transport bytes back to the last known game address.

    Guarantees payload byte-for-byte transmission.
    """
    def __init__(self, profile: GameProfile, transport: Transport, fixed_local_target_addr: Optional[Tuple[str, int]] = None):
        super().__init__(profile)
        self.transport = transport
        self.fixed_local_target_addr = fixed_local_target_addr
        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._is_running = False

        # Bound details
        self.local_host: Optional[str] = None
        self.local_port: Optional[int] = None

        # Thread-safe lock for state updates and socket operations
        self._lock = threading.Lock()

        # Counters conforming to specifications
        self.packets_from_game = 0
        self.packets_to_game = 0
        self.packets_to_transport = 0
        self.packets_from_transport = 0
        self.bytes_from_game = 0
        self.bytes_to_game = 0
        self.bytes_to_transport = 0
        self.bytes_from_transport = 0

        # Last sender game address
        self.last_local_game_addr: Optional[Tuple[str, int]] = None

        # Set receive callback on the transport
        self.transport.set_receive_callback(self._on_transport_receive)

    def start(self) -> None:
        """
        Starts the UDP adapter, binds the socket, and launches the background receiver thread.
        """
        with self._lock:
            if self._is_running:
                return

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Apply standard SO_REUSEADDR option
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Use short timeout to periodically check stop_event
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

            self.stop_event.clear()
            self.thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._is_running = True
            self.thread.start()

    def stop(self) -> None:
        """
        Stops the receiver thread, closes the socket to release the port, and resets state.
        This operation is idempotent.
        """
        with self._lock:
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
            try:
                self.thread.join(timeout=1.0)
            except Exception:
                pass
            self.thread = None

        with self._lock:
            self.local_host = None
            self.local_port = None
            self._is_running = False

    def cleanup(self) -> None:
        """
        Cleans up resources and releases the UDP socket.
        This operation is idempotent.
        """
        self.stop()

    def is_running(self) -> bool:
        """
        Checks if the adapter receiver thread is currently running.
        """
        with self._lock:
            return self._is_running

    def get_pid(self) -> Optional[int]:
        """
        Bridge adapter doesn't spawn any subprocess, returns None.
        """
        return None

    def get_local_addr(self) -> Tuple[Optional[str], Optional[int]]:
        """
        Returns the actually bound host and port.
        """
        with self._lock:
            return self.local_host, self.local_port

    def _recv_loop(self) -> None:
        """
        Background thread loop to receive UDP packets from the local game.
        """
        while not self.stop_event.is_set():
            try:
                sock = None
                with self._lock:
                    sock = self.sock
                if sock is None:
                    break

                data, addr = sock.recvfrom(65535)

                with self._lock:
                    self.last_local_game_addr = addr
                    self.packets_from_game += 1
                    self.bytes_from_game += len(data)

                # Send payload byte-for-byte to the transport
                self.transport.send(data)

                with self._lock:
                    self.packets_to_transport += 1
                    self.bytes_to_transport += len(data)
            except socket.timeout:
                continue
            except (OSError, ValueError):
                # Normal socket closure / timeout during stop
                break
            except Exception:
                if self.stop_event.is_set():
                    break
                raise

    def _on_transport_receive(self, payload: bytes) -> None:
        """
        Callback triggered when the transport receives a byte payload from the remote peer.

        Guarantees that the payload is sent back to the game client byte-for-byte.
        This callback is fully thread-safe and can be safely executed from the transport thread.
        """
        with self._lock:
            self.packets_from_transport += 1
            self.bytes_from_transport += len(payload)
            is_running = self._is_running
            sock = self.sock
            addr = self.last_local_game_addr or self.fixed_local_target_addr

        if not is_running or sock is None or addr is None:
            # No local game client has sent a packet yet or adapter is stopped. We cannot forward back.
            return

        # Propagate socket sendto errors directly (outside the lock)
        sock.sendto(payload, addr)

        with self._lock:
            self.packets_to_game += 1
            self.bytes_to_game += len(payload)
