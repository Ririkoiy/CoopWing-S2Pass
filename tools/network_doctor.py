import sys
import os
import subprocess
import argparse
import datetime
import re
import socket
import time
import zipfile
import platform
import csv
import json

# Ensure Windows event loop policy or Windows-first compatibility (non-async, standard socket block)
# The tool operates with standard library blocking sockets with timeouts for reliability.

def decode_bytes(data):
    for enc in ['utf-8', 'gbk', 'cp936', 'latin-1']:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')

def safe_int(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

def run_command(name, cmd_str, is_powershell=False, timeout=10):
    """
    Executes a shell command.
    Returns (stdout, stderr, returncode, error_msg)
    """
    if is_powershell:
        cmd_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd_str]
    else:
        cmd_args = ["cmd", "/c", cmd_str]
        
    stdout, stderr, returncode, error_msg = "", "", 0, ""
    try:
        res = subprocess.run(cmd_args, capture_output=True, timeout=timeout)
        stdout = decode_bytes(res.stdout)
        stderr = decode_bytes(res.stderr)
        returncode = res.returncode
    except subprocess.TimeoutExpired as e:
        returncode = -1
        error_msg = f"Command timed out after {timeout} seconds."
        stdout = decode_bytes(e.stdout) if e.stdout else ""
        stderr = decode_bytes(e.stderr) if e.stderr else ""
    except Exception as e:
        returncode = -2
        error_msg = f"Command execution failed: {str(e)}"
        
    return stdout, stderr, returncode, error_msg

def get_all_processes():
    """
    Runs tasklist /FO CSV /NH and returns all processes.
    Returns list of dicts: {"name": ..., "pid": ..., "session_name": ..., "session_num": ..., "mem_usage": ...}
    """
    processes = []
    stdout, stderr, code, err = run_command("tasklist", "tasklist /FO CSV /NH", is_powershell=False, timeout=10)
    if code != 0 or err:
        return processes
        
    # tasklist /FO CSV /NH output is CSV without headers
    reader = csv.reader(stdout.splitlines())
    for row in reader:
        if len(row) >= 2:
            img_name = row[0]
            try:
                pid = int(row[1])
            except ValueError:
                continue
            session_name = row[2] if len(row) > 2 else ""
            session_num = row[3] if len(row) > 3 else ""
            mem_usage = row[4] if len(row) > 4 else ""
            
            processes.append({
                "name": img_name,
                "pid": pid,
                "session_name": session_name,
                "session_num": session_num,
                "mem_usage": mem_usage
            })
    return processes

def parse_netsh_subinterfaces(netsh_output):
    """
    Parses 'netsh interface ipv4 show subinterfaces' output.
    Returns map {alias_name: mtu}
    """
    mtu_map = {}
    for line in netsh_output.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            try:
                mtu = int(parts[0])
                alias = " ".join(parts[4:])
                mtu_map[alias] = mtu
            except ValueError:
                continue
    return mtu_map

def parse_netsh_interfaces(netsh_output):
    """
    Parses 'netsh interface ipv4 show interfaces' output.
    Returns map {alias_name: metric}
    """
    metric_map = {}
    for line in netsh_output.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            try:
                metric = int(parts[1])
                alias = " ".join(parts[4:])
                metric_map[alias] = metric
            except ValueError:
                continue
    return metric_map

def parse_ipconfig_fallback(ipconfig_output):
    """
    Fallback parser for ipconfig /all to detect adapters.
    Returns list of dicts.
    """
    adapters = []
    current_adapter = None
    
    for line in ipconfig_output.splitlines():
        if not line:
            continue
        # Section headers in ipconfig /all start at col 0 and end with colon
        m_head = re.match(r"^([^\s].*):$", line)
        if m_head:
            if current_adapter:
                adapters.append(current_adapter)
            current_adapter = {
                "Alias": m_head.group(1).strip(),
                "IP": None,
                "MTU": "Unknown",
                "Metric": "Unknown",
                "Description": ""
            }
            continue
        
        if current_adapter:
            # Indented property lines
            if "ipv4 address" in line.lower() or "ipv4 地址" in line.lower():
                m_ip = re.search(r":\s*([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)", line)
                if m_ip:
                    current_adapter["IP"] = m_ip.group(1)
            elif "description" in line.lower() or "描述" in line.lower():
                parts = line.split(":", 1)
                if len(parts) > 1:
                    current_adapter["Description"] = parts[1].strip()
                    
    if current_adapter:
        adapters.append(current_adapter)
        
    return adapters

def detect_easytier_interfaces(target_interface=None, raw_outputs=None):
    """
    Best-effort identification of EasyTier / Virtual network adapter.
    Returns (selected_adapter_dict or None, candidate_adapters_list, log_lines_list)
    """
    log_lines = []
    log_lines.append(f"EasyTier interface detection started. Target interface: {target_interface}")
    
    # Try powershell JSON first
    ps_cmd = (
        "$interfaces = Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, InterfaceIndex, NlMtu, InterfaceMetric; "
        "$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, IPAddress; "
        "$res = @(); "
        "foreach ($i in $interfaces) { "
        "  $ip = ($addresses | Where-Object { $_.InterfaceAlias -eq $i.InterfaceAlias } | Select-Object -First 1).IPAddress; "
        "  $res += [PSCustomObject]@{Alias=$i.InterfaceAlias; Index=$i.InterfaceIndex; MTU=$i.NlMtu; Metric=$i.InterfaceMetric; IP=$ip} "
        "}; "
        "if ($res) { $res | ConvertTo-Json -Compress }"
    )
    
    adapters = []
    log_lines.append("Attempting adapter query via PowerShell JSON...")
    stdout, stderr, code, err = run_command("powershell_adapters", ps_cmd, is_powershell=True, timeout=10)
    
    if code == 0 and not err and stdout.strip():
        try:
            data = json.loads(stdout.strip())
            if isinstance(data, dict):
                adapters = [data]
            elif isinstance(data, list):
                adapters = [item for item in data if isinstance(item, dict)]
            log_lines.append(f"PowerShell returned {len(adapters)} adapters.")
        except Exception as e:
            log_lines.append(f"PowerShell JSON decoding failed: {e}")
            
    # Fallback to ipconfig /all
    if not adapters:
        if raw_outputs is None:
            raw_outputs = {}
        log_lines.append("Falling back to parsing ipconfig /all...")
        ipconfig_out = raw_outputs.get("ipconfig_all", ("", "", -1, ""))
        adapters_fallback = parse_ipconfig_fallback(ipconfig_out[0])
        
        # Populate MTU and Metric from netsh if possible
        netsh_sub_out = raw_outputs.get("netsh_subinterfaces", ("", "", -1, ""))
        netsh_int_out = raw_outputs.get("netsh_interfaces", ("", "", -1, ""))
        
        mtu_map = parse_netsh_subinterfaces(netsh_sub_out[0])
        metric_map = parse_netsh_interfaces(netsh_int_out[0])
        
        for a in adapters_fallback:
            alias = a.get("Alias")
            alias_str = str(alias) if alias is not None else ""
            if alias_str:
                # Look up MTU
                for k, v in mtu_map.items():
                    if k.lower() in alias_str.lower() or alias_str.lower() in k.lower():
                        a["MTU"] = v
                        break
                # Look up Metric
                for k, v in metric_map.items():
                    if k.lower() in alias_str.lower() or alias_str.lower() in k.lower():
                        a["Metric"] = v
                        break
            adapters.append(a)
            
    # Normalize fields to allow missing/empty values
    normalized_adapters = []
    for a in adapters:
        normalized_adapters.append({
            "Alias": a.get("Alias") if a.get("Alias") is not None else None,
            "IP": a.get("IP") if a.get("IP") is not None else None,
            "MTU": safe_int(a.get("MTU")),
            "Metric": safe_int(a.get("Metric")),
            "Description": a.get("Description") if a.get("Description") is not None else ""
        })

    log_lines.append("All detected adapters (normalized):")
    for a in normalized_adapters:
        log_lines.append(f"  - Alias: {a['Alias']}, IP: {a['IP']}, MTU: {a['MTU']}, Metric: {a['Metric']}, Description: {a['Description']}")
        
    candidate_adapters = []
    selected = None
    
    if target_interface:
        log_lines.append(f"Searching candidates matching target interface '{target_interface}' (ignoring default keywords)...")
        # First pass: Exact match (case insensitive)
        for a in normalized_adapters:
            alias = str(a.get("Alias") or "")
            if alias.lower() == target_interface.lower():
                candidate_adapters.append(a)
                log_lines.append(f"Exact match candidate found: {a}")
        # Second pass: Substring match if no exact matches found
        if not candidate_adapters:
            for a in normalized_adapters:
                alias = str(a.get("Alias") or "")
                desc = str(a.get("Description") or "")
                if target_interface.lower() in alias.lower() or target_interface.lower() in desc.lower():
                    candidate_adapters.append(a)
                    log_lines.append(f"Substring match candidate found: {a}")
                    
        if candidate_adapters:
            selected = candidate_adapters[0]
            log_lines.append(f"Selected interface: {selected}")
    else:
        # Auto detect based on keywords
        keywords = ["easytier", "et_", "wintun", "tun", "tap"]
        log_lines.append(f"Auto-detecting virtual/EasyTier interfaces matching keywords {keywords}...")
        for a in normalized_adapters:
            alias = str(a.get("Alias") or "")
            desc = str(a.get("Description") or "")
            if any(kw in alias.lower() or kw in desc.lower() for kw in keywords):
                candidate_adapters.append(a)
                log_lines.append(f"Auto-detect match candidate found: {a}")
                
        if candidate_adapters:
            selected = candidate_adapters[0]
            log_lines.append(f"Selected auto-detected interface: {selected}")
            
    if not selected:
        log_lines.append("No EasyTier/Virtual interface detected.")
        
    return selected, candidate_adapters, log_lines

def parse_local_port(addr_str):
    """
    Parses local port from an IPv4 or IPv6 address string.
    Examples: 
      - '0.0.0.0:47584' -> 47584
      - '[::]:47584' -> 47584
      - '[2001:db8::1]:47584' -> 47584
      - '127.0.0.1:47584' -> 47584
    """
    _, _, port_str = addr_str.rpartition(':')
    try:
        return int(port_str)
    except ValueError:
        return None

def check_port_occupancy(pid_to_name, raw_netstat_output):
    """
    Parses netstat -ano to find listeners/connections on specified ports.
    Checks both local and remote/foreign address ports.
    Returns findings dict.
    """
    port_findings = {
        (47584, "UDP"): [],
        (47584, "TCP"): [],
        (47585, "UDP"): [],
        (27036, "TCP"): [],
        (27036, "UDP"): []
    }
    
    for line in raw_netstat_output.splitlines():
        parts = [p.strip() for p in line.split() if p.strip()]
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ["TCP", "UDP"]:
            continue
            
        local_addr = parts[1]
        remote_addr = parts[2]
        
        local_port = parse_local_port(local_addr)
        remote_port = parse_local_port(remote_addr)
        
        for target_port, target_proto in port_findings.keys():
            if proto == target_proto:
                is_local_match = (local_port == target_port)
                is_remote_match = (remote_port == target_port)
                
                if is_local_match or is_remote_match:
                    if is_local_match and is_remote_match:
                        match_type = "Local & Remote"
                    elif is_local_match:
                        match_type = "Local"
                    else:
                        match_type = "Remote"
                        
                    try:
                        pid = int(parts[-1])
                    except ValueError:
                        pid = None
                    process_name = pid_to_name.get(pid, "Unknown") if pid is not None else "Unknown"
                    
                    entry = {
                        "pid": pid,
                        "process": process_name,
                        "local_addr": local_addr,
                        "remote_addr": remote_addr,
                        "local_port_match": is_local_match,
                        "remote_port_match": is_remote_match,
                        "match_type": match_type,
                        "state": parts[3] if proto == "TCP" and len(parts) >= 5 else ""
                    }
                    if entry not in port_findings[(target_port, proto)]:
                        port_findings[(target_port, proto)].append(entry)
                        
    return port_findings

def evaluate_port_warnings(port_findings):
    """
    Checks if ports are occupied by unexpected processes.
    """
    warnings = []
    
    # 47584 UDP
    for entry in port_findings[(47584, "UDP")]:
        proc = entry["process"].lower()
        if not any(k in proc for k in ["ck3", "python", "s2pass"]):
            warnings.append(f"UDP Port 47584 is occupied by an unexpected process: {entry['process']} (PID: {entry['pid']}).")
            
    # 47584 TCP
    for entry in port_findings[(47584, "TCP")]:
        proc = entry["process"].lower()
        if not any(k in proc for k in ["python", "s2pass", "ck3"]):
            warnings.append(f"TCP Port 47584 is occupied by an unexpected process: {entry['process']} (PID: {entry['pid']}).")
            
    # 47585 UDP
    for entry in port_findings[(47585, "UDP")]:
        proc = entry["process"].lower()
        if not any(k in proc for k in ["easytier", "python", "s2pass", "ck3"]):
            warnings.append(f"UDP Port 47585 is occupied by an unexpected process: {entry['process']} (PID: {entry['pid']}).")
            
    # 27036 TCP
    for entry in port_findings[(27036, "TCP")]:
        proc = entry["process"].lower()
        if not any(k in proc for k in ["steam"]):
            warnings.append(f"TCP Port 27036 is occupied by an unexpected process: {entry['process']} (PID: {entry['pid']}).")
            
    # 27036 UDP
    for entry in port_findings[(27036, "UDP")]:
        proc = entry["process"].lower()
        if not any(k in proc for k in ["steam"]):
            warnings.append(f"UDP Port 27036 is occupied by an unexpected process: {entry['process']} (PID: {entry['pid']}).")
            
    return warnings

def run_mtu_test(peer_ip, count, log_path):
    """
    Runs fixed size ICMP DF ping tests.
    Returns (results_dict, has_jitter_warning)
    """
    sizes = [1350, 1300, 1252, 1200, 1072, 1000]
    results = {}
    log_lines = []
    log_lines.append(f"Target peer IP: {peer_ip}")
    log_lines.append(f"Ping count: {count}")
    
    any_jitter_warning = False
    
    for size in sizes:
        # ping <ip> -f -l <size> -n <count>
        cmd_list = ["ping", peer_ip, "-f", "-l", str(size), "-n", str(count)]
        cmd_str = " ".join(cmd_list)
        log_lines.append(f"\n============================\nSize: {size}\nCommand: {cmd_str}")
        
        try:
            # Execute ping command without shell=True
            res = subprocess.run(cmd_list, capture_output=True, timeout=15)
            stdout = decode_bytes(res.stdout)
            stderr = decode_bytes(res.stderr)
            log_lines.append(f"Return code: {res.returncode}")
            log_lines.append("--- stdout ---")
            log_lines.append(stdout)
            if stderr:
                log_lines.append("--- stderr ---")
                log_lines.append(stderr)
        except Exception as e:
            stdout = ""
            stderr = str(e)
            log_lines.append(f"Failed to run command: {e}")
            results[size] = {
                "status": "UNKNOWN",
                "rtts": [],
                "jitter_warning": False,
                "msg": f"Execution error: {e}"
            }
            continue
            
        # Parse results
        too_large_patterns = ["需要拆分", "进行分片", "需要分片", "packet needs to be fragmented", "df set"]
        is_too_large = any(p in stdout.lower() or p in stderr.lower() for p in too_large_patterns)
        
        rtts = []
        for line in stdout.splitlines():
            m = re.search(r"(?:time|时间)\s*([=<])\s*([0-9]+)\s*ms", line, re.IGNORECASE)
            if m:
                val = int(m.group(2))
                rtts.append(val)
                
        if is_too_large:
            status = "TOO_LARGE"
            msg = "Packet needs to be fragmented but DF set."
            jitter_warn = False
        elif len(rtts) > 0:
            max_rtt = max(rtts)
            avg_rtt = sum(rtts) / len(rtts)
            jitter_warn = (max_rtt > 200) or (max_rtt > avg_rtt * 2)
            if jitter_warn:
                any_jitter_warning = True
                
            if len(rtts) < count or jitter_warn:
                status = "PASS_UNSTABLE"
                msg = f"Passed with packet loss ({len(rtts)}/{count}) or high jitter (max={max_rtt}ms, avg={avg_rtt:.1f}ms)."
            else:
                status = "PASS_STABLE"
                msg = f"Passed stably. RTT: min={min(rtts)}ms, max={max_rtt}ms, avg={avg_rtt:.1f}ms."
        else:
            status = "UNKNOWN"
            msg = "No replies received or request timed out / destination unreachable."
            jitter_warn = False
            
        results[size] = {
            "status": status,
            "rtts": rtts,
            "jitter_warning": jitter_warn,
            "msg": msg
        }
        log_lines.append(f"Result Status: {status}")
        log_lines.append(f"Result Msg: {msg}")
        
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(log_lines))
        
    return results, any_jitter_warning

def run_tcp_test(host, port, log_path):
    """
    Connects to server TCP port 5 times.
    """
    log_lines = []
    log_lines.append(f"Target: {host}:{port}")
    log_lines.append("Running TCP connection test 5 times...")
    
    success_count = 0
    fail_count = 0
    latencies = []
    
    for i in range(5):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        t0 = time.perf_counter()
        try:
            log_lines.append(f"[{datetime.datetime.now()}] Attempt {i+1}: connecting...")
            sock.connect((host, port))
            t1 = time.perf_counter()
            duration_ms = (t1 - t0) * 1000
            latencies.append(duration_ms)
            success_count += 1
            log_lines.append(f"Attempt {i+1}: Success in {duration_ms:.2f} ms")
        except Exception as e:
            fail_count += 1
            log_lines.append(f"Attempt {i+1}: Failed ({str(e)})")
        finally:
            sock.close()
            
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(log_lines))
        
    return success_count, fail_count, latencies

def run_udp_test(host, port, log_path):
    """
    Sends one UDP probe packet.
    """
    log_lines = []
    log_lines.append(f"Target: {host}:{port}")
    log_lines.append("Payload: b'S2PASS_NETWORK_DOCTOR_UDP_PROBE'")
    
    status = "INCONCLUSIVE"
    response_data = None
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3.0)
    
    try:
        t0 = time.perf_counter()
        log_lines.append(f"[{datetime.datetime.now()}] Sending probe...")
        sock.sendto(b"S2PASS_NETWORK_DOCTOR_UDP_PROBE", (host, port))
        log_lines.append(f"[{datetime.datetime.now()}] Probe sent successfully. Waiting for response (timeout 3.0s)...")
        
        data, addr = sock.recvfrom(1024)
        t1 = time.perf_counter()
        duration_ms = (t1 - t0) * 1000
        response_data = data
        status = "CONFIRMED"
        log_lines.append(f"[{datetime.datetime.now()}] Response received from {addr} in {duration_ms:.2f} ms")
        log_lines.append(f"Response data: {data!r}")
    except socket.timeout:
        log_lines.append(f"[{datetime.datetime.now()}] Timeout: no response received.")
    except ConnectionResetError as e:
        log_lines.append(f"[{datetime.datetime.now()}] Port unreachable / Connection reset: {str(e)}")
        status = "INCONCLUSIVE"
    except Exception as e:
        log_lines.append(f"[{datetime.datetime.now()}] Error: {str(e)}")
        status = "ERROR"
    finally:
        sock.close()
        
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(log_lines))
        
    return status, response_data

def generate_recommendations(easytier_info, mtu_results, tcp_results, warnings, has_interface_or_peer=False):
    recs = []
    
    # 1. MTU size
    if easytier_info or has_interface_or_peer:
        mtu_val = safe_int(easytier_info.get("MTU")) if easytier_info else None
        if mtu_val is not None:
            recs.append(f"- **EasyTier MTU**: The detected EasyTier MTU is {mtu_val}. For CK3/EasyTier troubleshooting, MTU 1200 is a recommended starting point based on current observations. Test before applying permanently.")
        else:
            recs.append("- **EasyTier MTU**: For CK3/EasyTier troubleshooting, MTU 1200 is a recommended starting point based on current observations. Test before applying permanently.")
    else:
        recs.append("- **EasyTier MTU**: 未检测到 EasyTier，跳过虚拟网卡 MTU 建议。")
        
    # 2. Jitter / Jitter Warning / Hotspot
    has_jitter = False
    has_packet_loss = False
    if mtu_results:
        for size, res in mtu_results.items():
            if res.get("jitter_warning"):
                has_jitter = True
            if res.get("status") == "PASS_UNSTABLE":
                has_packet_loss = True
                
    if has_jitter or has_packet_loss:
        recs.append("- **Network Quality**: High network jitter or packet loss was detected during the MTU ping probe. Avoid using mobile phone hotspots or unstable Wi-Fi for multiplayer gaming. Use a wired connection or stable broadband.")
        recs.append("- **Broadband Retest**: If you are currently on a wireless network, re-run tests using fixed broadband to see if the jitter/packet loss resolves.")
    else:
        recs.append("- **Network Connection**: Avoid using mobile phone hotspots for long-term gaming, as they are prone to sudden latency spikes.")
        
    # 3. Multiple VPNs
    recs.append("- **VPN/TUN Conflict**: If you use other VPNs or TUN adapters (like Tailscale, ZeroTier, or OpenVPN), consider temporarily disabling them to prevent routing conflicts.")
    
    # 4. Developer Help
    recs.append("- **Diagnostic Archive**: Send the generated diagnostic ZIP file to the S2Pass community or developers for deeper troubleshooting.")
    
    return "\n".join(recs)

def zip_directory(src_dir, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, os.path.dirname(src_dir))
                zipf.write(file_path, arcname)

def main():
    parser = argparse.ArgumentParser(description="S2Pass Network Doctor v0.1 - Read-only Network Diagnostics")
    parser.add_argument("--output-dir", default="diagnostics", help="Diagnostic output directory (default: diagnostics)")
    parser.add_argument("--peer-ip", help="Peer virtual network IP for MTU / ping test")
    parser.add_argument("--interface", help="Target virtual interface alias (e.g. et_12_2ula)")
    parser.add_argument("--server-host", help="S2Pass server host to test (if not provided, TCP/UDP server tests are skipped)")
    parser.add_argument("--server-tcp-port", type=int, default=9000, help="S2Pass server TCP port (default: 9000)")
    parser.add_argument("--server-udp-port", type=int, default=9001, help="S2Pass server UDP port (default: 9001)")
    parser.add_argument("--ping-count", type=int, default=4, help="Ping count for MTU probe (default: 4)")
    parser.add_argument("--no-zip", action="store_true", help="Do not pack outputs into a ZIP archive")
    args = parser.parse_args()
    
    # Generate timestamp and directory paths
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    diag_dir_name = f"s2pass_diag_{timestamp}"
    diag_dir = os.path.join(args.output_dir, diag_dir_name)
    raw_dir = os.path.join(diag_dir, "raw_commands")
    tests_dir = os.path.join(diag_dir, "tests")
    
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(tests_dir, exist_ok=True)
    
    warnings = []
    
    # 1. Gather baseline commands
    commands = [
        ("ipconfig_all", "ipconfig /all", False),
        ("route_print", "route print", False),
        ("netstat_ano", "netstat -ano", False),
        ("netsh_subinterfaces", "netsh interface ipv4 show subinterfaces", False),
        ("netsh_interfaces", "netsh interface ipv4 show interfaces", False),
        ("get_netconnectionprofile", "Get-NetConnectionProfile | Format-List *", True),
        ("get_netipinterface", "Get-NetIPInterface | Sort-Object InterfaceMetric | Format-Table ifIndex,InterfaceAlias,AddressFamily,InterfaceMetric,NlMtu -AutoSize", True),
        ("get_netipconfiguration", "Get-NetIPConfiguration | Format-List *", True),
        ("get_udp_47584", "Get-NetUDPEndpoint -LocalPort 47584 -ErrorAction SilentlyContinue", True),
        ("get_udp_47585", "Get-NetUDPEndpoint -LocalPort 47585 -ErrorAction SilentlyContinue", True),
        ("get_udp_27036", "Get-NetUDPEndpoint -LocalPort 27036 -ErrorAction SilentlyContinue", True),
        ("get_tcp_47584", "Get-NetTCPConnection -LocalPort 47584 -ErrorAction SilentlyContinue", True),
        ("get_tcp_27036", "Get-NetTCPConnection -LocalPort 27036 -ErrorAction SilentlyContinue", True)
    ]
    
    print("Collecting system network commands...")
    cmd_outputs = {}
    for filename, cmd_str, is_powershell in commands:
        stdout, stderr, code, err = run_command(filename, cmd_str, is_powershell)
        cmd_outputs[filename] = (stdout, stderr, code, err)
        
        # Write raw command file
        filepath = os.path.join(raw_dir, f"{filename}.txt")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"=== Command: {cmd_str} ===\n")
            f.write(f"=== Exit Code: {code} ===\n")
            if err:
                f.write(f"=== Error: {err} ===\n")
            f.write("=== Stdout ===\n")
            f.write(stdout)
            f.write("\n=== Stderr ===\n")
            f.write(stderr)
            
        if code != 0 or err:
            is_port_query = "get_udp_" in filename or "get_tcp_" in filename
            if not (is_port_query and code == 1 and not err):
                warnings.append(f"Command '{cmd_str}' returned exit code {code}. Error: {err or 'None'}")
            
    # 2. Filter process list
    print("Collecting process list...")
    keywords = ["ck3", "steam", "easytier", "sse", "smartsteam", "python", "s2pass"]
    all_procs = get_all_processes()
    
    # Build full PID-to-name cache
    pid_to_name = {p["pid"]: p["name"] for p in all_procs}
    
    # Filter processes for writing to process_list.txt to avoid privacy leak
    filtered_procs = [p for p in all_procs if any(kw in p["name"].lower() for kw in keywords)]
    proc_filepath = os.path.join(diag_dir, "process_list.txt")
    with open(proc_filepath, "w", encoding="utf-8") as f:
        f.write("Image Name,PID,Session Name,Session#,Mem Usage\n")
        for p in filtered_procs:
            f.write(f'"{p["name"]}","{p["pid"]}","{p["session_name"]}","{p["session_num"]}","{p["mem_usage"]}"\n')
    
    # 3. EasyTier Interface Recognition
    print("Identifying EasyTier / Virtual network interfaces...")
    easytier_info, candidate_adapters, et_log = detect_easytier_interfaces(args.interface, cmd_outputs)
    et_log_path = os.path.join(tests_dir, "easytier_check.log")
    with open(et_log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(et_log))
        
    if not easytier_info:
        warnings.append("EasyTier virtual network interface not detected.")
        easytier_status = "Not detected"
        easytier_alias = "Unknown"
        easytier_ip = "Unknown"
        easytier_mtu = "Unknown"
        easytier_metric = "Unknown"
        
        easytier_section = (
            "### 1. EasyTier / Virtual Interface Detection\n"
            "- **EasyTier / Virtual Interface**: Not detected\n"
            "- **Note**: Use `--interface <name>` to specify manually."
        )
    else:
        easytier_status = "Detected"
        easytier_alias = easytier_info.get("Alias") or "Unknown"
        easytier_ip = easytier_info.get("IP") or "Unknown"
        easytier_mtu = easytier_info.get("MTU") if easytier_info.get("MTU") is not None else "Unknown"
        easytier_metric = easytier_info.get("Metric") if easytier_info.get("Metric") is not None else "Unknown"
        
        # Check metric conflicts
        metric_val = safe_int(easytier_info.get("Metric"))
        if metric_val is not None and metric_val > 50:
            warnings.append(f"EasyTier interface metric is high ({metric_val}), which might cause routing issues.")
            
        # Build candidates list string
        candidate_lines = []
        for c in candidate_adapters:
            c_alias = c.get("Alias") or "Unknown"
            c_ip = c.get("IP") or "Unknown"
            c_mtu = c.get("MTU") if c.get("MTU") is not None else "Unknown"
            c_metric = c.get("Metric") if c.get("Metric") is not None else "Unknown"
            candidate_lines.append(f"  - **{c_alias}** (IP: {c_ip}, MTU: {c_mtu}, Metric: {c_metric})")
        candidates_str = "\n" + "\n".join(candidate_lines) if candidate_lines else "  - None"
        
        easytier_section = (
            "### 1. EasyTier / Virtual Interface Detection\n"
            f"- **EasyTier / Virtual Interface**: Detected\n"
            f"- **Selected Alias**: {easytier_alias}\n"
            f"- **IP Address**: {easytier_ip}\n"
            f"- **MTU**: {easytier_mtu}\n"
            f"- **Metric**: {easytier_metric}\n"
            f"- **All Candidate Interfaces**:{candidates_str}"
        )
                
    # 4. Port Occupation Read-Only Detection
    print("Checking critical port bindings...")
    netstat_out = cmd_outputs.get("netstat_ano", ("", "", -1, ""))[0]
    port_findings = check_port_occupancy(pid_to_name, netstat_out)
    port_warnings = evaluate_port_warnings(port_findings)
    warnings.extend(port_warnings)
    
    # Format port strings for findings
    def format_port_finding(port, proto):
        entries = port_findings.get((port, proto), [])
        if not entries:
            return "Free"
        res_strs = []
        for e in entries:
            state_str = f" ({e['state']})" if e['state'] else ""
            match_str = ""
            details = []
            if e.get("local_port_match"):
                details.append(f"local_port_match: {e['local_addr']}")
            if e.get("remote_port_match"):
                details.append(f"remote_port_match: {e['remote_addr']}")
            if details:
                match_str = f" [{', '.join(details)}]"
            res_strs.append(f"Bound by {e['process']} (PID: {e['pid']}){match_str}{state_str}")
        return ", ".join(res_strs)
        
    port_47584_udp = format_port_finding(47584, "UDP")
    port_47584_tcp = format_port_finding(47584, "TCP")
    port_47585_udp = format_port_finding(47585, "UDP")
    port_27036_tcp = format_port_finding(27036, "TCP")
    port_27036_udp = format_port_finding(27036, "UDP")
    
    # 5. MTU / DF Ping Probe
    mtu_probe_details = ""
    mtu_results = {}
    if args.peer_ip:
        print(f"Running MTU DF ping probe to {args.peer_ip}...")
        mtu_log_path = os.path.join(tests_dir, "mtu_probe.log")
        mtu_results, any_jitter = run_mtu_test(args.peer_ip, args.ping_count, mtu_log_path)
        if any_jitter:
            warnings.append("High network jitter detected during the MTU ping probe.")
            
        mtu_probe_details = "| Payload Size | Status | Details |\n| :--- | :--- | :--- |\n"
        for size in sorted(mtu_results.keys(), reverse=True):
            r = mtu_results[size]
            mtu_probe_details += f"| {size} | {r['status']} | {r['msg']} |\n"
    else:
        print("Skipping MTU DF ping probe (no --peer-ip specified).")
        mtu_log_path = os.path.join(tests_dir, "mtu_probe.log")
        with open(mtu_log_path, "w", encoding="utf-8") as f:
            f.write("MTU Probe skipped: --peer-ip parameter not provided.\n")
        mtu_probe_details = "*MTU ping probe skipped because --peer-ip was not provided.*"
        
    # 6. S2Pass TCP/UDP Connectivity Checks
    tcp_status = "Skipped"
    udp_status = "Skipped"
    tcp_results = None
    
    if args.server_host:
        print(f"Testing S2Pass TCP connect to {args.server_host}:{args.server_tcp_port}...")
        tcp_log_path = os.path.join(tests_dir, "s2pass_tcp_test.log")
        tcp_success, tcp_fail, tcp_lats = run_tcp_test(args.server_host, args.server_tcp_port, tcp_log_path)
        tcp_results = (tcp_success, tcp_fail, tcp_lats)
        
        if tcp_success > 0:
            avg_lat = sum(tcp_lats) / tcp_success
            tcp_status = f"Success: {tcp_success}/5, Latency: min={min(tcp_lats):.1f}ms, max={max(tcp_lats):.1f}ms, avg={avg_lat:.1f}ms"
            if tcp_fail > 0:
                warnings.append(f"S2Pass TCP test had {tcp_fail} failed connection attempts.")
        else:
            tcp_status = "Failed completely (0/5 success)"
            warnings.append("S2Pass TCP signal connection failed completely.")
            
        print(f"Testing S2Pass UDP basic probe to {args.server_host}:{args.server_udp_port}...")
        udp_log_path = os.path.join(tests_dir, "s2pass_udp_test.log")
        udp_res_status, udp_data = run_udp_test(args.server_host, args.server_udp_port, udp_log_path)
        
        if udp_res_status == "CONFIRMED":
            udp_status = f"Response confirmed. Reply length: {len(udp_data)} bytes."
        elif udp_res_status == "INCONCLUSIVE":
            udp_status = "Inconclusive (No response received/Timeout)"
        else:
            udp_status = "Failed / Error"
            warnings.append("S2Pass UDP test encountered socket execution error.")
    else:
        print("Skipping S2Pass TCP/UDP connectivity tests (no --server-host specified).")
        tcp_log_path = os.path.join(tests_dir, "s2pass_tcp_test.log")
        udp_log_path = os.path.join(tests_dir, "s2pass_udp_test.log")
        with open(tcp_log_path, "w", encoding="utf-8") as f:
            f.write("TCP Connection test skipped: --server-host parameter not provided.\n")
        with open(udp_log_path, "w", encoding="utf-8") as f:
            f.write("UDP basic probe test skipped: --server-host parameter not provided.\n")
            
    # 7. Evaluate overall status
    overall_status = "OK"
    if args.server_host and tcp_results and tcp_results[0] == 0:
        overall_status = "High Risk"
    elif warnings:
        overall_status = "Warning"
    elif not args.server_host or not args.peer_ip:
        overall_status = "Unknown"
        
    status_explanation = ""
    if overall_status == "Unknown":
        status_explanation = "\n*(Note: Overall Status is 'Unknown' likely because --peer-ip or --server-host was not provided, and connection tests were skipped. / 注意：整体状态为 Unknown 可能只是因为未提供 --peer-ip 或 --server-host 参数。)*\n"
        
    # 8. Generate summary.md
    print("Generating summary report...")
    recs_str = generate_recommendations(
        easytier_info, 
        mtu_results, 
        tcp_results, 
        warnings, 
        has_interface_or_peer=bool(args.interface or args.peer_ip)
    )
    
    warnings_section = ""
    if warnings:
        warnings_section = "### Warnings and Issues Found:\n" + "\n".join(f"- ⚠️ {w}" for w in warnings)
    else:
        warnings_section = "All basic checks passed. No immediate issues detected."
    
    # Gather output file listing
    raw_files = sorted(os.listdir(raw_dir))
    test_files = sorted(os.listdir(tests_dir))
    
    raw_commands_index = "\n".join(f"- [{rf}](raw_commands/{rf})" for rf in raw_files)
    tests_index = "\n".join(f"- [{tf}](tests/{tf})" for tf in test_files)
    
    summary_content = f"""# S2Pass Network Doctor Report
 
- **Timestamp**: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **System**: {platform.system()} {platform.release()} (Version: {platform.version()})
- **Version**: v0.1 (Read-Only)
 
---
 
## Privacy Notice
 
The diagnostic report may contain:
- Machine name / computer name
- Local LAN IP addresses
- Virtual network card IP (EasyTier)
- MAC addresses
- Local routing tables
- Processes and PIDs containing: {', '.join(keywords)}
 
The tool does **NOT** collect:
- Account passwords
- Security tokens
- Private keys
- Steam login credentials / sessions
 
Please check the contents of the generated ZIP file before sharing it publicly.
 
---
 
## Overall Status: {overall_status}
{status_explanation}
{warnings_section}
 
---
 
## Key Findings
 
{easytier_section}
 
### 2. Port Occupancy
- **UDP Port 47584 (CK3/SSE Discovery/Session Candidate)**: {port_47584_udp}
- **TCP Port 47584 (CK3/SSE Session)**: {port_47584_tcp}
- **UDP Port 47585 (CK3/SSE Runtime Candidate)**: {port_47585_udp}
- **TCP Port 27036 (Steam Local)**: {port_27036_tcp}
- **UDP Port 27036 (Steam Local)**: {port_27036_udp}
 
### 3. S2Pass Server Connectivity
- **Server Host**: {args.server_host or "Not provided"}
- **TCP Connection (Port {args.server_tcp_port})**: {tcp_status}
- **UDP Ping Probe (Port {args.server_udp_port})**: {udp_status}
  *(Note: Lack of response to basic UDP probes is inconclusive and does not imply UDP is blocked, as the server may not respond to unauthenticated/raw UDP packets)*
 
### 4. Path MTU Detection (Ping Test)
- **Target Peer IP**: {args.peer_ip or "Not provided"}
- **Probe Results**:
{mtu_probe_details}

---

## Recommendations

{recs_str}

---

## Raw Logs Index

### Command Outputs (raw_commands/)
{raw_commands_index}

### Diagnostic Logs (tests/)
{tests_index}
"""
    
    summary_path = os.path.join(diag_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_content)
        
    # 9. Pack zip
    zip_path = None
    if not args.no_zip:
        zip_filename = f"{diag_dir_name}.zip"
        zip_path = os.path.join(args.output_dir, zip_filename)
        print(f"Archiving diagnostics folder into {zip_path}...")
        zip_directory(diag_dir, zip_path)
        
    print("\n==================================================")
    print("Network Doctor completed.")
    print(f"Summary Status: {overall_status}")
    print(f"Report directory: {os.path.abspath(diag_dir)}")
    print(f"Summary file: {os.path.abspath(summary_path)}")
    if zip_path:
        print(f"Zip file: {os.path.abspath(zip_path)}")
    else:
        print("Zip file generation skipped.")
        
    if warnings:
        print("\nPrimary Warnings (max 3 shown):")
        for w in warnings[:3]:
            print(f"- [WARNING] {w}")
    print("==================================================")

if __name__ == "__main__":
    main()
