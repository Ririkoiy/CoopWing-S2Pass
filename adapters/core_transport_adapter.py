import asyncio
import threading
import traceback
from typing import Any, Callable, Dict, Optional

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

        # ── Send-path diagnostics (v0.2 live-debug) ──
        self.send_attempts: int = 0
        self.send_scheduled: int = 0
        self.send_exceptions: int = 0
        self.last_send_error: Optional[str] = None

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
            self.send_attempts += 1

        def _safe_send() -> None:
            self.send_scheduled += 1
            try:
                self._core.send_payload(payload)
            except RuntimeError as exc:
                self.send_exceptions += 1
                self.last_send_error = f"{exc.__class__.__name__}: {exc}"
            except Exception as exc:
                self.send_exceptions += 1
                self.last_send_error = (
                    f"{exc.__class__.__name__}: {exc}\n"
                    f"{traceback.format_exc()}"
                )

        self._loop.call_soon_threadsafe(_safe_send)

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

    def get_payload_diagnostics(self) -> Dict[str, Any]:
        """Return transport-layer diagnostics for live debugging."""
        core = self._core
        return {
            # -- CoreTransportAdapter send diagnostics
            "cta_send_attempts": self.send_attempts,
            "cta_send_scheduled": self.send_scheduled,
            "cta_send_exceptions": self.send_exceptions,
            "cta_last_send_error": self.last_send_error,
            # -- Core send-path diagnostics
            "core_payload_send_attempts": core.payload_send_attempts,
            "core_payload_send_bytes": core.payload_send_bytes,
            "core_udp_relay_send_attempts": core.udp_relay_send_attempts,
            "core_udp_relay_send_bytes": core.udp_relay_send_bytes,
            "core_udp_relay_send_noop_no_transport":
                core.udp_relay_send_noop_no_transport,
            "core_udp_relay_send_noop_no_target":
                core.udp_relay_send_noop_no_target,
            "core_udp_relay_send_exceptions": core.udp_relay_send_exceptions,
            "core_last_payload_send_error": core.last_payload_send_error,
            # -- Core receive-path diagnostics
            "core_relay_packets_received": core.relay_packets_received,
            "core_relay_payload_callback_calls": core.relay_payload_callback_calls,
            "core_relay_payload_callback_bytes": core.relay_payload_callback_bytes,
            "core_relay_drop_not_relay_prefix": core.relay_drop_not_relay_prefix,
            "core_relay_drop_invalid_header": core.relay_drop_invalid_header,
            "core_relay_drop_token_mismatch": core.relay_drop_token_mismatch,
            "core_relay_drop_no_callback": core.relay_drop_no_callback,
            "core_last_relay_receive_error": core.last_relay_receive_error,
        }
