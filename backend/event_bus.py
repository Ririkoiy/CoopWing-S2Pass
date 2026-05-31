from datetime import datetime, timezone
from typing import List, Dict, Any, Callable

class EventBus:
    """Simple in-memory event bus and event log."""
    
    def __init__(self):
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._event_log: List[Dict[str, Any]] = []

    def register_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback to receive all published events."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unregister_listener(self, listener: Callable[[Dict[str, Any]], None]) -> None:
        """Unregister a callback."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def publish(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Publish an event to all listeners and log it in memory."""
        # Use ISO format for timestamp with 'Z' for UTC
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        event_obj = {
            "event": event_type,
            "timestamp": timestamp,
            "payload": payload
        }

        self._event_log.append(event_obj)
        
        # Notify all listeners
        for listener in self._listeners:
            try:
                listener(event_obj)
            except Exception:
                # Event listeners should not crash the publisher
                pass
                
        return event_obj

    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the latest recorded events up to the specified limit."""
        return self._event_log[-limit:]

    def clear(self) -> None:
        """Clear the event log."""
        self._event_log.clear()
