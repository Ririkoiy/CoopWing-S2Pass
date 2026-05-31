import asyncio
import threading
from typing import Callable, Optional

from adapters.transport import Transport
from network_core import S2PassClientCore

class CoreTransportAdapter(Transport):
    """
    A Transport-compatible wrapper that connects the Transport interface
    to S2PassClientCore payload APIs.
    """
    def __init__(self, core: S2PassClientCore, loop: asyncio.AbstractEventLoop):
        self._core = core
        self._loop = loop
        self._lock = threading.RLock()
        self._callback: Optional[Callable[[bytes], None]] = None
        self._is_closed = False

        # Register self as the core payload callback
        self._core.set_payload_callback(self._on_core_payload)

    def send(self, payload: bytes) -> None:
        """
        Send a byte payload to the remote peer via the S2PassClientCore relay path.
        Safe to call from a non-asyncio thread.
        """
        with self._lock:
            if self._is_closed:
                raise RuntimeError("Adapter is closed")

        self._loop.call_soon_threadsafe(self._core.send_payload, payload)

    def set_receive_callback(self, callback: Callable[[bytes], None]) -> None:
        """
        Set the callback to be invoked when a byte payload is received from the remote peer.
        """
        with self._lock:
            self._callback = callback

    def _on_core_payload(self, payload: bytes) -> None:
        """
        Invoked by S2PassClientCore when raw relay payload arrives.
        """
        with self._lock:
            if self._is_closed:
                return
            callback = self._callback

        if callback:
            callback(payload)

    def close(self) -> None:
        """
        Mark adapter closed and neutralize the local receive callback.
        Does not close the core.
        """
        with self._lock:
            self._is_closed = True
            self._callback = None
