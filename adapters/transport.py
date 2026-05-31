from abc import ABC, abstractmethod
from typing import Callable, Optional, Tuple

import threading

class Transport(ABC):
    @abstractmethod
    def send(self, payload: bytes) -> None:
        """Send a byte payload to the remote peer."""
        pass

    @abstractmethod
    def set_receive_callback(self, callback: Callable[[bytes], None]) -> None:
        """
        Set the callback to be invoked when a byte payload is received from the remote peer.
        The callback must be thread-safe.
        """
        pass

class FakePairTransport(Transport):
    """
    In-memory mock transport pair that directly connects two local adapters.
    Adapter A's send goes directly to Adapter B's receive callback, and vice versa.
    Does not swallow exceptions silently.
    """
    def __init__(self):
        self.peer: Optional[FakePairTransport] = None
        self.callback: Optional[Callable[[bytes], None]] = None
        self._lock = threading.RLock()

    def set_peer(self, peer: 'FakePairTransport') -> None:
        with self._lock:
            self.peer = peer

    def send(self, payload: bytes) -> None:
        with self._lock:
            peer = self.peer

        if not peer:
            raise RuntimeError("FakePairTransport: peer is not set")

        with peer._lock:
            callback = peer.callback

        if callback:
            # Propagate exceptions directly, do not swallow. Executed outside of locks.
            callback(payload)

    def set_receive_callback(self, callback: Callable[[bytes], None]) -> None:
        """
        Set the receive callback. The callback must be thread-safe as it
        can be called from the sender's thread.
        """
        with self._lock:
            self.callback = callback

def make_fake_pair() -> Tuple[FakePairTransport, FakePairTransport]:
    """Helper function to create a connected pair of FakePairTransports."""
    t1 = FakePairTransport()
    t2 = FakePairTransport()
    t1.set_peer(t2)
    t2.set_peer(t1)
    return t1, t2
