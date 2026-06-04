#!/usr/bin/env python3
"""Live relay path diagnostic dump for v0.2 TCP Relay hand-test debugging.

Usage:
  python tools/live_relay_path_dump.py --backend http://127.0.0.1:21520 --session-id s_xxx
  python tools/live_relay_path_dump.py --backend http://127.0.0.1:21520 --session-id s_xxx --watch 2

Output:
  - adapter_status counters
  - payload_diagnostics (transport-layer send/receive)
  - session logs (event types)
  - session status
  - relay_token is redacted
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def _redact_relay_token(obj: Any) -> Any:
    """Recursively redact relay_token values from nested structures."""
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***" if k == "relay_token" else _redact_relay_token(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_relay_token(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 32:
        # Redact any bare relay_token-like strings that slipped through
        if obj.startswith("rtk_"):
            return "***REDACTED***"
    return obj


def _get_json(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Fetch a JSON response from an HTTP endpoint."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"_error": f"HTTP {exc.code}", "_body": body}
    except urllib.error.URLError as exc:
        return {"_error": f"Connection failed: {exc.reason}"}
    except json.JSONDecodeError as exc:
        return {"_error": f"JSON parse error: {exc}"}


def _safe_get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return d


def dump_session(backend_url: str, session_id: str) -> Dict[str, Any]:
    """Dump all diagnostics for a single session."""
    base = backend_url.rstrip("/")
    result: Dict[str, Any] = {
        "session_id": session_id,
        "backend_url": base,
    }

    # --- Status ---
    status_url = f"{base}/sessions/{session_id}/status"
    status = _get_json(status_url)
    result["status"] = _redact_relay_token(status)

    # --- Logs ---
    logs_url = f"{base}/sessions/{session_id}/logs"
    logs = _get_json(logs_url)
    result["logs"] = _redact_relay_token(logs)
    result["event_types"] = [
        e.get("event_type") for e in _safe_get(logs, "data", "events", default=[])  # noqa: F821
    ]

    # --- Adapter status (may be embedded in status response) ---
    adapter_status = _safe_get(status, "data", "adapter_status")
    if adapter_status is None:
        # Try standalone adapter_status endpoint
        adapter_status = _safe_get(status, "adapter_status")

    if adapter_status:
        result["adapter_status"] = _redact_relay_token(adapter_status)
        counters = adapter_status.get("counters", {})
        result["counters"] = counters

        # --- Payload diagnostics ---
        payload_diag = adapter_status.get("payload_diagnostics")
        if payload_diag:
            result["payload_diagnostics"] = payload_diag
        else:
            result["payload_diagnostics"] = None
    else:
        result["adapter_status"] = None
        result["counters"] = None
        result["payload_diagnostics"] = None

    return result


def interpret_dump(dump: Dict[str, Any]) -> List[str]:
    """Produce a human-readable interpretation of the diagnostic dump."""
    findings: List[str] = []
    status = _safe_get(dump, "status", "data", "status", default="unknown")
    room_id = _safe_get(dump, "status", "data", "room_id", default="?")
    role = _safe_get(dump, "status", "data", "role", default="?")
    findings.append(f"Session: {dump['session_id']} role={role} room={room_id} status={status}")

    counters = dump.get("counters") or {}
    p2t = counters.get("packets_to_transport", 0)
    pft = counters.get("packets_from_transport", 0)
    pfg = counters.get("packets_from_game", 0)
    ptg = counters.get("packets_to_game", 0)
    findings.append(
        f"Adapter counters: from_game={pfg} to_transport={p2t} "
        f"from_transport={pft} to_game={ptg}"
    )

    event_types = dump.get("event_types") or []
    findings.append(f"Event types: {event_types}")

    payload_diag = dump.get("payload_diagnostics")
    if payload_diag:
        findings.append("--- Transport payload diagnostics ---")
        # CoreTransportAdapter send
        cta_sa = payload_diag.get("cta_send_attempts", 0)
        cta_ss = payload_diag.get("cta_send_scheduled", 0)
        cta_se = payload_diag.get("cta_send_exceptions", 0)
        cta_lse = payload_diag.get("cta_last_send_error")
        findings.append(
            f"  CTA send: attempts={cta_sa} scheduled={cta_ss} exceptions={cta_se}"
        )
        if cta_lse:
            findings.append(f"  CTA last_send_error: {cta_lse}")

        # Core send path
        findings.append(f"  Core payload_send_attempts: {payload_diag.get('core_payload_send_attempts', 0)}")
        findings.append(f"  Core payload_send_bytes: {payload_diag.get('core_payload_send_bytes', 0)}")
        findings.append(f"  Core udp_relay_send_attempts: {payload_diag.get('core_udp_relay_send_attempts', 0)}")
        findings.append(f"  Core udp_relay_send_bytes: {payload_diag.get('core_udp_relay_send_bytes', 0)}")
        nnt = payload_diag.get("core_udp_relay_send_noop_no_transport", 0)
        nnh = payload_diag.get("core_udp_relay_send_noop_no_target", 0)
        use = payload_diag.get("core_udp_relay_send_exceptions", 0)
        if nnt or nnh or use:
            findings.append(
                f"  Core send noop/ex: no_transport={nnt} no_target={nnh} exceptions={use}"
            )
        lse = payload_diag.get("core_last_payload_send_error")
        if lse:
            findings.append(f"  Core last_payload_send_error: {lse}")

        # Core receive path
        findings.append(f"  Core relay_packets_received: {payload_diag.get('core_relay_packets_received', 0)}")
        findings.append(f"  Core relay_payload_callback_calls: {payload_diag.get('core_relay_payload_callback_calls', 0)}")
        findings.append(f"  Core relay_payload_callback_bytes: {payload_diag.get('core_relay_payload_callback_bytes', 0)}")
        drp = payload_diag.get("core_relay_drop_not_relay_prefix", 0)
        dih = payload_diag.get("core_relay_drop_invalid_header", 0)
        dtm = payload_diag.get("core_relay_drop_token_mismatch", 0)
        dnc = payload_diag.get("core_relay_drop_no_callback", 0)
        if drp or dih or dtm or dnc:
            findings.append(
                f"  Core recv drops: not_relay={drp} invalid_header={dih} "
                f"token_mismatch={dtm} no_callback={dnc}"
            )
        lrre = payload_diag.get("core_last_relay_receive_error")
        if lrre:
            findings.append(f"  Core last_relay_receive_error: {lrre}")
    else:
        findings.append("--- No payload_diagnostics available ---")

    # --- Classification ---
    findings.append("")
    findings.append("--- Classification ---")
    if p2t == 0 and pfg == 0:
        findings.append("  No game traffic yet. Wait for Minecraft client to connect.")
    elif p2t > 0 and payload_diag is None:
        findings.append("  Adapter is sending but NO payload_diagnostics available.")
        findings.append("  => Transport may not be a CoreTransportAdapter, or payload_diagnostics not wired.")
    elif p2t > 0 and payload_diag:
        cta_se = payload_diag.get("cta_send_exceptions", 0)
        if cta_se > 0:
            findings.append("  !! Joiner send_payload EXCEPTION detected !!")
            findings.append(f"  => last_send_error: {payload_diag.get('cta_last_send_error')}")
        elif payload_diag.get("core_payload_send_attempts", 0) == 0:
            findings.append("  Adapter sent frames but core.send_payload NEVER called.")
            findings.append("  => CoreTransportAdapter.send scheduling gap.")
        elif payload_diag.get("core_udp_relay_send_attempts", 0) == 0:
            findings.append("  send_payload called but _send_udp_to_relay NEVER called.")
            findings.append("  => send_payload check failed silently (is_payload_mode? relay_token?).")
        elif payload_diag.get("core_udp_relay_send_noop_no_transport", 0) > 0:
            findings.append("  _send_udp_to_relay: NO UDP TRANSPORT.")
            findings.append("  => UDP endpoint destroyed or never created.")
        elif payload_diag.get("core_udp_relay_send_noop_no_target", 0) > 0:
            findings.append("  _send_udp_to_relay: NO RELAY TARGET.")
            findings.append("  => _relay_target_host/_relay_target_port not set.")
        elif payload_diag.get("core_udp_relay_send_bytes", 0) > 0:
            findings.append("  UDP sendto executed. Packets left the local machine.")
            if payload_diag.get("core_relay_packets_received", 0) == 0:
                findings.append("  But NO relay packets received back.")
                findings.append("  => VPS not forwarding, or VPS-to-peer blocked by NAT/firewall.")
                findings.append("  => Check VPS server.py RelaySession counters.")
            else:
                findings.append("  Relay packets received from VPS.")
                if payload_diag.get("core_relay_payload_callback_calls", 0) == 0:
                    findings.append("  !! Received but callback NOT invoked !!")
                    findings.append(f"  => no_callback drops: {payload_diag.get('core_relay_drop_no_callback', 0)}")
                else:
                    findings.append("  Payload callback invoked. Data reached TcpRelayAdapter.")
    elif pft > 0:
        findings.append("  Packets received from transport. Relay path is working.")
        if ptg == 0:
            findings.append("  But packets_to_game=0 => data not delivered to local game/client.")
    else:
        findings.append("  Indeterminate. Check VPS server.py RelaySession diagnostics.")

    return findings


def watch_loop(backend_url: str, session_id: str, interval: float) -> None:
    """Continuously poll and print diagnostics."""
    iteration = 0
    try:
        while True:
            iteration += 1
            print(f"\n{'=' * 60}")
            print(f"  Poll #{iteration}  {time.strftime('%H:%M:%S')}")
            print(f"{'=' * 60}")
            dump = dump_session(backend_url, session_id)
            if "_error" in dump.get("status", {}):
                print(f"  Status error: {dump['status']['_error']}")
            diag_text = json.dumps(dump, indent=2, ensure_ascii=False, default=str)
            # Truncate very long output
            if len(diag_text) > 4000:
                diag_text = diag_text[:4000] + "\n... [truncated]"
            print(diag_text)
            print()
            for line in interpret_dump(dump):
                print(f"  {line}")
            print()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live relay path diagnostic dump for v0.2 TCP Relay debugging",
    )
    parser.add_argument(
        "--backend",
        default="http://127.0.0.1:21520",
        help="Backend HTTP base URL (default: http://127.0.0.1:21520)",
    )
    parser.add_argument(
        "--session-id",
        required=True,
        help="Session ID to diagnose (e.g. s_xxx)",
    )
    parser.add_argument(
        "--watch",
        type=float,
        default=0,
        help="Poll interval in seconds (0 = dump once and exit)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON only (no interpretation)",
    )
    args = parser.parse_args()

    if args.watch > 0:
        watch_loop(args.backend, args.session_id, args.watch)
        return

    dump = dump_session(args.backend, args.session_id)
    if args.json:
        print(json.dumps(dump, indent=2, ensure_ascii=False, default=str))
    else:
        for line in interpret_dump(dump):
            print(line)
        print()
        print("Raw counters:")
        print(json.dumps(dump.get("counters", {}), indent=2))
        if dump.get("payload_diagnostics"):
            print()
            print("Payload diagnostics:")
            print(json.dumps(dump["payload_diagnostics"], indent=2))


if __name__ == "__main__":
    main()
