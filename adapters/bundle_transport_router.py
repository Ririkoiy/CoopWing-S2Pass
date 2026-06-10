"""Backend-local transport demux/router for Bundle adapters.

Allows multiple transport-aware adapters (e.g. TcpRelayAdapter,
GenericUdpBroadcastForwardAdapter, and UdpRawBridgeAdapter) to share one
underlying CoreTransportAdapter without overwriting each other's receive
callbacks.

This is purely backend-local plumbing.  No protocol messages, fields, states,
errors, or schema changes.  JSON adapter envelopes route by their existing
adapter-private ``"adapter"`` field; udp_raw_bridge routes by its
adapter-private binary magic prefix.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Dict, Optional

from adapters.transport import Transport
from adapters.udp_raw_bridge_adapter import (
    ADAPTER_NAME as UDP_RAW_BRIDGE_ADAPTER_NAME,
    UDP_RAW_BRIDGE_FRAME_MAGIC,
)


class RoutedTransport(Transport):
    """Logical transport for one adapter namespace.

    * ``send()`` forwards the payload unchanged to the underlying transport.
    * ``set_receive_callback()`` registers a callback that only fires for
      payloads whose ``"adapter"`` field matches this namespace.
    """

    def __init__(self, namespace: str, router: "BundleTransportRouter") -> None:
        self._namespace = namespace
        self._router = router
        self._callback: Optional[Callable[[bytes], None]] = None
        self._lock = threading.RLock()

    @property
    def namespace(self) -> str:
        return self._namespace

    def send(self, payload: bytes) -> None:
        """Forward payload unchanged to the underlying transport."""
        self._router._underlying.send(payload)

    def set_receive_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register the receive callback for this namespace."""
        with self._lock:
            self._callback = callback

    def _deliver(self, payload: bytes) -> None:
        """Deliver a payload to the registered callback (called by router)."""
        with self._lock:
            cb = self._callback
        if cb is not None:
            cb(payload)


def _extract_adapter_namespace(payload: bytes) -> Optional[str]:
    """Extract the ``"adapter"`` field from a JSON payload.

    Returns None if the payload cannot be parsed or lacks an ``"adapter"``
    string field.  This is intentionally lenient — a failed parse simply
    means the payload is unroutable and will be counted as unknown.
    """
    try:
        obj = json.loads(payload)
        if isinstance(obj, dict):
            adapter = obj.get("adapter")
            if isinstance(adapter, str) and adapter:
                return adapter
    except Exception:
        pass
    return None


class BundleTransportRouter:
    """Demux incoming transport payloads to per-namespace callbacks.

    One ``BundleTransportRouter`` owns the single ``set_receive_callback``
    slot on the underlying ``Transport``.  It exposes logical
    ``RoutedTransport`` objects per adapter namespace.  Each
    ``RoutedTransport`` has its own independent ``set_receive_callback``.

    Routing keys: either the ``"adapter"`` field in a JSON envelope, or the
    udp_raw_bridge binary magic prefix.

    * ``"tcp_relay"``             → TcpRelayAdapter callback
    * ``"udp_broadcast_forward"`` → GenericUdpBroadcastForwardAdapter callback
    * ``b"CWG_URB1\\0"``          → UdpRawBridgeAdapter callback
    * Unknown / unparseable       → dropped and counted
    """

    def __init__(self, underlying: Transport) -> None:
        self._underlying = underlying
        self._routes: Dict[str, RoutedTransport] = {}
        self._lock = threading.RLock()
        self.unknown_namespace_count: int = 0
        self.dispatch_count: int = 0
        self.dispatch_errors: int = 0
        # Own the single receive callback
        self._underlying.set_receive_callback(self._dispatch)

    @property
    def underlying(self) -> Transport:
        """The underlying transport this router wraps."""
        return self._underlying

    def get_transport(self, namespace: str) -> RoutedTransport:
        """Get or create a ``RoutedTransport`` for *namespace*."""
        with self._lock:
            route = self._routes.get(namespace)
            if route is None:
                route = RoutedTransport(namespace, self)
                self._routes[namespace] = route
            return route

    def _dispatch(self, payload: bytes) -> None:
        """Route an incoming payload to the correct namespace callback."""
        if payload.startswith(UDP_RAW_BRIDGE_FRAME_MAGIC):
            namespace = UDP_RAW_BRIDGE_ADAPTER_NAME
        else:
            namespace = _extract_adapter_namespace(payload)
            if namespace is None:
                with self._lock:
                    self.unknown_namespace_count += 1
                return

        with self._lock:
            route = self._routes.get(namespace)

        if route is None:
            with self._lock:
                self.unknown_namespace_count += 1
            return

        with self._lock:
            self.dispatch_count += 1
        try:
            route._deliver(payload)
        except Exception:
            with self._lock:
                self.dispatch_errors += 1
