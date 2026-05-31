#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S2Pass Local Relay Smoke Test Automation Script.
Strictly standard library only, compatible with Windows.
"""

import sys
import os
import subprocess
import threading
import queue
import re
import time

# Verify we do not import any disallowed packages
# Standard library only

class ProcessManager:
    def __init__(self):
        self.processes = []
        self.queue = queue.Queue()
        self.threads = []

    def start(self, name, args):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(script_dir)
        
        cmd = [sys.executable, "-u"] + args
        creationflags = 0
        if sys.platform == 'win32':
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=root_dir,
            creationflags=creationflags
        )
        self.processes.append((proc, name))
        
        # Start reader thread
        t = threading.Thread(target=self._reader, args=(proc.stdout, name), daemon=True)
        t.start()
        self.threads.append(t)
        return proc

    def _reader(self, stdout, name):
        try:
            for line in iter(stdout.readline, ''):
                self.queue.put((name, line))
        except Exception as e:
            self.queue.put((name, f"READER_EXCEPTION: {e}\n"))
        finally:
            try:
                stdout.close()
            except Exception:
                pass

    def cleanup_process(self, proc, name):
        if proc is None or proc.poll() is not None:
            return
        
        try:
            proc.terminate()
        except Exception:
            pass
        
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
            
        try:
            proc.kill()
        except Exception:
            pass
            
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    def cleanup_all(self):
        # Clean in reverse order of startup (Bob, Alice, Server)
        for proc, name in reversed(self.processes):
            self.cleanup_process(proc, name)


def main():
    print("S2Pass local relay smoke test")
    
    pm = ProcessManager()
    q = pm.queue
    
    server_logs = []
    alice_logs = []
    bob_logs = []
    
    server_proc = None
    alice_proc = None
    bob_proc = None
    
    phase = "Init"
    reason = ""
    
    try:
        # 1. Start Server
        phase = "Start Server"
        server_proc = pm.start('server', ["server.py"])
        
        server_started = False
        seen_port = False
        seen_server_log = False
        port_occupied = False
        deadline = time.monotonic() + 5.0
        
        while time.monotonic() < deadline and not server_started:
            timeout_left = deadline - time.monotonic()
            try:
                source, line = q.get(timeout=timeout_left)
            except queue.Empty:
                break
            
            if source == 'server':
                server_logs.append(line.rstrip('\r\n'))
                # Detect port occupancy
                if "10048" in line or "already in use" in line or "OSError" in line:
                    port_occupied = True
                
                # Check for port references
                if "9000" in line or "9001" in line:
                    seen_port = True
                
                # Check for key phrases
                if any(kw in line for kw in ["启动完成", "P2P Server", "TCP 信令端口", "UDP Relay端口", "TCP", "UDP"]):
                    seen_server_log = True
                
                if seen_port and seen_server_log:
                    server_started = True
            elif source == 'alice':
                alice_logs.append(line.rstrip('\r\n'))
            elif source == 'bob':
                bob_logs.append(line.rstrip('\r\n'))

        if not server_started:
            if port_occupied or (server_proc.poll() is not None):
                all_server_output = "\n".join(server_logs)
                if any(k in all_server_output for k in ["10048", "already in use", "Address"]):
                    raise RuntimeError("Server port (9000 or 9001) is already in use by another process.")
                raise RuntimeError(f"Server exited with code {server_proc.poll()} before starting up.")
            raise RuntimeError("Server failed to start within 5 seconds timeout.")
        
        print("Server: started")

        # 2. Start Alice
        phase = "Start Alice"
        alice_proc = pm.start('alice', [
            "cli_client.py", "create", 
            "--host", "127.0.0.1", 
            "--name", "Alice", 
            "--force-relay", 
            "--send-test", 
            "--start-delay", "2.0"
        ])
        
        room_id = None
        deadline = time.monotonic() + 10.0
        
        while time.monotonic() < deadline and not room_id:
            timeout_left = deadline - time.monotonic()
            try:
                source, line = q.get(timeout=timeout_left)
            except queue.Empty:
                break
            
            if source == 'server':
                server_logs.append(line.rstrip('\r\n'))
            elif source == 'alice':
                alice_logs.append(line.rstrip('\r\n'))
                # Regex match for room_id from Alice only
                match = re.search(r"room_id:\s*([A-Z0-9]{6})", line)
                if match:
                    room_id = match.group(1)
            elif source == 'bob':
                bob_logs.append(line.rstrip('\r\n'))

        if not room_id:
            if alice_proc.poll() is not None:
                raise RuntimeError(f"Alice exited with code {alice_proc.poll()} before creating room.")
            raise RuntimeError("Failed to parse room_id from Alice's output within 10 seconds.")
            
        print(f"Alice: room_id {room_id}")

        # 3. Start Bob
        phase = "Start Bob"
        bob_proc = pm.start('bob', [
            "cli_client.py", "join", 
            "--host", "127.0.0.1", 
            "--name", "Bob", 
            "--room", room_id, 
            "--force-relay"
        ])
        
        bob_confirmed = False
        deadline = time.monotonic() + 10.0
        
        while time.monotonic() < deadline and not bob_confirmed:
            timeout_left = deadline - time.monotonic()
            try:
                source, line = q.get(timeout=timeout_left)
            except queue.Empty:
                break
            
            if source == 'server':
                server_logs.append(line.rstrip('\r\n'))
            elif source == 'alice':
                alice_logs.append(line.rstrip('\r\n'))
            elif source == 'bob':
                bob_logs.append(line.rstrip('\r\n'))
                # Confirm Bob has joined/initialized
                if any(kw in line for kw in ["player_id:", "RELAY_ENABLED received", "Active echo responder mode"]):
                    bob_confirmed = True

        if not bob_confirmed:
            if bob_proc.poll() is not None:
                raise RuntimeError(f"Bob exited with code {bob_proc.poll()} before joining.")
            raise RuntimeError("Bob failed to join or initialize within 10 seconds.")
            
        print("Bob: joined")

        # 4. Wait for Alice test completion
        phase = "Wait Alice Test"
        packets_sent = None
        echo_packets_received = None
        loss_rate = None
        avg_rtt = None
        test_completed = False
        
        deadline = time.monotonic() + 30.0
        
        while time.monotonic() < deadline:
            timeout_left = deadline - time.monotonic()
            if timeout_left <= 0:
                break
            try:
                source, line = q.get(timeout=timeout_left)
            except queue.Empty:
                break
            
            if source == 'server':
                server_logs.append(line.rstrip('\r\n'))
            elif source == 'alice':
                alice_logs.append(line.rstrip('\r\n'))
                if "Test completed" in line or "Test completed." in line:
                    test_completed = True
                
                # Parse stats (taking the last occurrences)
                m_sent = re.search(r"packets sent:\s*(\d+)", line, re.IGNORECASE)
                if m_sent:
                    packets_sent = int(m_sent.group(1))
                
                m_recv = re.search(r"echo packets received:\s*(\d+)", line, re.IGNORECASE)
                if m_recv:
                    echo_packets_received = int(m_recv.group(1))
                    
                m_loss = re.search(r"loss rate:\s*([\d.]+)%", line, re.IGNORECASE)
                if m_loss:
                    loss_rate = float(m_loss.group(1))
                    
                m_rtt = re.search(r"avg RTT:\s*([\d.]+)\s*ms", line, re.IGNORECASE)
                if m_rtt:
                    avg_rtt = float(m_rtt.group(1))
                    
            elif source == 'bob':
                bob_logs.append(line.rstrip('\r\n'))

        # Check if test completed or stats captured
        if not test_completed and (packets_sent is None or echo_packets_received is None):
            raise RuntimeError("Alice relay test failed to complete within 30 seconds.")
            
        print("Relay test: completed")

        # 5. Parse and Validate Stats
        phase = "Validate Stats"
        if packets_sent is None or echo_packets_received is None or loss_rate is None:
            raise RuntimeError(f"Failed to capture complete stats from Alice's output: packets_sent={packets_sent}, echo_packets_received={echo_packets_received}, loss_rate={loss_rate}")

        print(f"packets sent: {packets_sent}")
        print(f"echo packets received: {echo_packets_received}")
        print(f"loss rate: {loss_rate:.2f}%")
        if avg_rtt is not None:
            print(f"avg RTT: {avg_rtt:.2f} ms")
        else:
            print("avg RTT: N/A")

        if packets_sent < 90:
            raise RuntimeError(f"Too few packets sent: {packets_sent} < 90")
        if echo_packets_received < 90:
            raise RuntimeError(f"Too few echo packets received: {echo_packets_received} < 90")
        if loss_rate > 5.0:
            raise RuntimeError(f"Loss rate too high: {loss_rate}% > 5.0%")

        print("RESULT: PASS")
        sys.exit(0)

    except Exception as e:
        print("RESULT: FAIL")
        print(f"Failed Phase: {phase}")
        print(f"Reason: {e}")
        
        # Print diagnostic summaries (last 30 lines)
        print("\n=== Server Logs (Last 30 Lines) ===")
        for line in server_logs[-30:]:
            print(line)
            
        print("\n=== Alice Logs (Last 30 Lines) ===")
        for line in alice_logs[-30:]:
            print(line)
            
        print("\n=== Bob Logs (Last 30 Lines) ===")
        for line in bob_logs[-30:]:
            print(line)
            
        sys.exit(1)

    finally:
        pm.cleanup_all()


if __name__ == "__main__":
    main()
