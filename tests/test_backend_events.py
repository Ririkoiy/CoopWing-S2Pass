import unittest
from datetime import datetime
from backend.event_bus import EventBus

class TestBackendEvents(unittest.TestCase):
    def test_event_bus_append_and_timestamp(self):
        bus = EventBus()
        events_received = []
        
        def listener(evt):
            events_received.append(evt)
            
        bus.register_listener(listener)
        
        payload = {"pid": 123, "profile_id": "prof-1"}
        evt = bus.publish("launch_started", payload)
        
        # Verify published structure
        self.assertEqual(evt["event"], "launch_started")
        self.assertEqual(evt["payload"], payload)
        self.assertIn("timestamp", evt)
        
        # Verify timestamp is ISO string (ends with Z or is parseable)
        ts = evt["timestamp"]
        self.assertTrue(ts.endswith("Z"))
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
        self.assertIsNotNone(dt)
        
        # Verify listener called
        self.assertEqual(len(events_received), 1)
        self.assertEqual(events_received[0], evt)

    def test_event_list_latest_entries(self):
        bus = EventBus()
        for i in range(15):
            bus.publish(f"event_{i}", {"index": i})
            
        history = bus.get_events(limit=5)
        self.assertEqual(len(history), 5)
        self.assertEqual(history[0]["event"], "event_10")
        self.assertEqual(history[-1]["event"], "event_14")

    def test_listener_exception_handling(self):
        bus = EventBus()
        
        def bad_listener(evt):
            raise RuntimeError("listener crash")
            
        events_received = []
        def good_listener(evt):
            events_received.append(evt)
            
        bus.register_listener(bad_listener)
        bus.register_listener(good_listener)
        
        # Publish should not raise exception even if bad_listener crashes
        bus.publish("test_event", {})
        self.assertEqual(len(events_received), 1)

if __name__ == "__main__":
    unittest.main()
