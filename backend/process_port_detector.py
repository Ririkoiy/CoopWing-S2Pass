# -*- coding: utf-8 -*-
"""Process TCP/UDP port detection via PowerShell — testable command runner.

v0.3-J: Uses Get-NetTCPConnection / Get-NetUDPEndpoint for local process
port enumeration.  No packet capture, no admin, no third-party deps.
Tests inject FakeCommandRunner — no real commands run in tests.
"""

from __future__ import annotations

import dataclasses
import json as _json
import subprocess
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# Command runner (mirrors secondary_ip_manager pattern)
# ═══════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    def run(self, args: list[str], timeout: float = 10.0) -> CommandResult:
        raise NotImplementedError


class SubprocessCommandRunner(CommandRunner):
    def run(self, args: list[str], timeout: float = 10.0) -> CommandResult:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, shell=False,
        )
        return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


class FakeCommandRunner(CommandRunner):
    def __init__(self, responses: dict[str, CommandResult] | None = None) -> None:
        self.responses: dict[str, CommandResult] = dict(responses or {})
        self.calls: list[tuple[list[str], float]] = []

    def set(self, keyword: str, result: CommandResult) -> None:
        self.responses[keyword] = result

    def run(self, args: list[str], timeout: float = 10.0) -> CommandResult:
        self.calls.append((list(args), timeout))
        flat = " ".join(args)
        for keyword, result in self.responses.items():
            if keyword in flat:
                return result
        raise RuntimeError(f"FakeCommandRunner: no response for {args}")


# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass(frozen=True)
class PortCandidate:
    protocol: str          # "tcp" | "udp"
    port: int
    process_id: Optional[int] = None
    process_name: Optional[str] = None
    local_address: Optional[str] = None
    remote_address: Optional[str] = None
    state: Optional[str] = None
    confidence: str = "low"   # "high" | "medium" | "low"
    reason: str = ""
    remote_port: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "port": self.port,
            "process_id": self.process_id,
            "process_name": self.process_name,
            "local_address": self.local_address,
            "remote_address": self.remote_address,
            "remote_port": self.remote_port,
            "state": self.state,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PortCandidate":
        return cls(
            protocol=str(d.get("protocol", "")),
            port=int(d.get("port", 0)),
            process_id=d.get("process_id") if d.get("process_id") is not None else None,
            process_name=str(d.get("process_name", "")) if d.get("process_name") else None,
            local_address=str(d.get("local_address", "")) if d.get("local_address") else None,
            remote_address=str(d.get("remote_address", "")) if d.get("remote_address") else None,
            state=str(d.get("state", "")) if d.get("state") else None,
            confidence=str(d.get("confidence", "low")),
            reason=str(d.get("reason", "")),
            remote_port=int(d["remote_port"]) if d.get("remote_port") is not None else None,
        )


@dataclasses.dataclass(frozen=True)
class ScanResult:
    candidates: list[PortCandidate]
    stage: str
    scanned_at: float
    process_name: Optional[str] = None
    process_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "stage": self.stage,
            "scanned_at": self.scanned_at,
            "process_name": self.process_name,
            "process_id": self.process_id,
        }


@dataclasses.dataclass(frozen=True)
class ProcessPortCandidate:
    pid: int
    protocol: str
    local_address: str
    local_port: int
    remote_address: Optional[str] = None
    remote_port: Optional[int] = None
    state: Optional[str] = None
    confidence: str = "low"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "pid": self.pid,
            "protocol": self.protocol,
            "local_address": self.local_address,
            "local_port": self.local_port,
            "confidence": self.confidence,
            "reason": self.reason,
        }
        if self.remote_address is not None:
            result["remote_address"] = self.remote_address
        if self.remote_port is not None:
            result["remote_port"] = self.remote_port
        if self.state is not None:
            result["state"] = self.state
        return result


@dataclasses.dataclass(frozen=True)
class ProcessPortScanResult:
    pid: int
    candidates: list[ProcessPortCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


class ProcessPortDetectionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ═══════════════════════════════════════════════════════════════════════════
# Noise filter keywords
# ═══════════════════════════════════════════════════════════════════════════

_NOISE_PROCESS_NAMES: tuple[str, ...] = (
    "steam", "steamwebhelper", "steamservice",
    "discord", "discordcanary", "discordptb",
    "chrome", "msedge", "firefox", "brave", "opera",
    "msedgewebview2", "explorer", "svchost", "lsass",
    "csrss", "smss", "wininit", "services", "winlogon",
    "system", "idle", "registry", "spoolsv", "dwm",
    "taskhostw", "sihost", "runtimebroker", "searchindexer",
    "yourphone", "phoneexperiencehost", "textinputhost",
    "onedrive", "sharex", "obs64", "obs", "slack",
    "teams", "spotify", "spotifyhelper",
)

_NOISE_PORT_RANGES: tuple[tuple[int, int], ...] = (
    (80, 80), (443, 443), (8080, 8080), (8443, 8443),
)

_NOISE_STATES: tuple[str, ...] = (
    "TimeWait", "CloseWait", "Close", "SynSent",
)


def _is_noise_process(name: str) -> bool:
    lowered = name.lower()
    return any(kw in lowered for kw in _NOISE_PROCESS_NAMES)


def _is_noise_port(port: int) -> bool:
    return any(lo <= port <= hi for lo, hi in _NOISE_PORT_RANGES)


# ═══════════════════════════════════════════════════════════════════════════
# Detector
# ═══════════════════════════════════════════════════════════════════════════

class ProcessPortDetector:
    """Scans local TCP/UDP ports associated with a process via PowerShell.

    Inject FakeCommandRunner for tests.  Real scanning uses SubprocessCommandRunner.
    """

    def __init__(self, runner: CommandRunner, timeout: float = 10.0) -> None:
        self._runner = runner
        self._timeout = timeout

    def scan(
        self,
        *,
        process_name: Optional[str] = None,
        process_id: Optional[int] = None,
        stage: str = "manual",
        include_low_confidence: bool = False,
    ) -> ScanResult:
        import time as _time
        scanned_at = _time.time()

        tcp_rows = self._fetch_tcp(process_id)
        udp_rows = self._fetch_udp(process_id)

        candidates: list[PortCandidate] = []
        for row in tcp_rows:
            if process_id is not None and row.get("process_id") != process_id:
                continue
            row_process_name = str(row.get("process_name") or "")
            if process_name is not None:
                if row_process_name and row_process_name.lower() != process_name.lower():
                    continue
                if not row_process_name:
                    row = {**row, "process_name": process_name}
            try:
                candidate = self._classify_tcp(row)
            except Exception:
                continue
            if candidate.confidence != "low" or include_low_confidence:
                candidates.append(candidate)

        for row in udp_rows:
            if process_id is not None and row.get("process_id") != process_id:
                continue
            row_process_name = str(row.get("process_name") or "")
            if process_name is not None:
                if row_process_name and row_process_name.lower() != process_name.lower():
                    continue
                if not row_process_name:
                    row = {**row, "process_name": process_name}
            try:
                candidate = self._classify_udp(row)
            except Exception:
                continue
            if candidate.confidence != "low" or include_low_confidence:
                candidates.append(candidate)

        candidates.sort(key=lambda c: ({"high": 0, "medium": 1, "low": 2}[c.confidence], c.protocol, c.port))

        return ScanResult(
            candidates=candidates,
            stage=stage,
            scanned_at=scanned_at,
            process_name=process_name,
            process_id=process_id,
        )

    def scan_pid(self, pid: int) -> ProcessPortScanResult:
        if type(pid) is not int or pid <= 0:
            raise ProcessPortDetectionError(
                "INVALID_PID",
                "PID must be a positive integer",
            )

        try:
            process_name = self._lookup_process_name(pid)
            result = self.scan(
                process_name=process_name or None,
                process_id=pid,
                include_low_confidence=True,
            )
        except ProcessPortDetectionError:
            raise
        except RuntimeError as exc:
            raise ProcessPortDetectionError(
                "PROCESS_PORT_SCAN_FAILED",
                str(exc),
            ) from exc

        candidates = [
            ProcessPortCandidate(
                pid=pid,
                protocol=candidate.protocol,
                local_address=candidate.local_address or "",
                local_port=candidate.port,
                remote_address=candidate.remote_address,
                remote_port=candidate.remote_port,
                state=candidate.state,
                confidence=candidate.confidence,
                reason=candidate.reason,
            )
            for candidate in result.candidates
        ]
        return ProcessPortScanResult(pid=pid, candidates=candidates)

    # ── PowerShell queries ──────────────────────────────────────────────

    def _lookup_process_name(self, pid: int) -> str:
        script = (
            f"Get-Process -Id {pid} -ErrorAction SilentlyContinue "
            "| Select-Object Id,ProcessName | ConvertTo-Json"
        )
        rows = self._run_ps(script, "Get-Process")
        if not rows:
            raise ProcessPortDetectionError(
                "INVALID_PID",
                f"No running process found for PID {pid}",
            )
        process_name = rows[0].get("ProcessName", rows[0].get("process_name"))
        return str(process_name or "")

    def _fetch_tcp(self, process_id: Optional[int] = None) -> list[dict[str, Any]]:
        owner_filter = (
            f"| Where-Object {{ $_.OwningProcess -eq {process_id} }} "
            if process_id is not None
            else ""
        )
        script = (
            "Get-NetTCPConnection -ErrorAction SilentlyContinue "
            "| Where-Object { "
            "$_.State -eq 'Listen' -or $_.State -eq 'Established' "
            "} "
            f"{owner_filter}"
            "| Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,"
            "State,OwningProcess "
            "| ForEach-Object { "
            "$p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
            "[PSCustomObject]@{"
            "local_address=$_.LocalAddress;local_port=$_.LocalPort;"
            "remote_address=$_.RemoteAddress;remote_port=$_.RemotePort;"
            "state=$_.State;process_id=$_.OwningProcess;"
            "process_name=if($p){$p.ProcessName}else{$null}"
            "}} | ConvertTo-Json"
        )
        try:
            return self._run_ps(script, "Get-NetTCPConnection")
        except RuntimeError as exc:
            if "failed (rc=" not in str(exc):
                raise
            return self._run_netstat("tcp", process_id, exc)

    def _fetch_udp(self, process_id: Optional[int] = None) -> list[dict[str, Any]]:
        owner_filter = (
            f"| Where-Object {{ $_.OwningProcess -eq {process_id} }} "
            if process_id is not None
            else ""
        )
        script = (
            "Get-NetUDPEndpoint -ErrorAction SilentlyContinue "
            f"{owner_filter}"
            "| Select-Object LocalAddress,LocalPort,OwningProcess "
            "| ForEach-Object { "
            "$p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
            "[PSCustomObject]@{"
            "local_address=$_.LocalAddress;local_port=$_.LocalPort;"
            "process_id=$_.OwningProcess;"
            "process_name=if($p){$p.ProcessName}else{$null}"
            "}} | ConvertTo-Json"
        )
        try:
            return self._run_ps(script, "Get-NetUDPEndpoint")
        except RuntimeError as exc:
            if "failed (rc=" not in str(exc):
                raise
            return self._run_netstat("udp", process_id, exc)

    def _run_netstat(
        self,
        protocol: str,
        process_id: Optional[int],
        original_error: RuntimeError,
    ) -> list[dict[str, Any]]:
        result = self._runner.run(
            ["netstat", "-ano", "-p", protocol],
            timeout=self._timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.strip()[:200]
            raise RuntimeError(
                f"{original_error}; netstat {protocol} fallback failed "
                f"(rc={result.returncode}): {detail}"
            ) from original_error
        return self._parse_netstat(result.stdout, protocol, process_id)

    @staticmethod
    def _parse_netstat(
        output: str,
        protocol: str,
        process_id: Optional[int],
    ) -> list[dict[str, Any]]:
        expected_protocol = protocol.upper()
        rows: list[dict[str, Any]] = []
        for line in output.splitlines():
            columns = line.split()
            if not columns or columns[0].upper() != expected_protocol:
                continue

            if expected_protocol == "TCP":
                if len(columns) < 5:
                    continue
                local_endpoint, remote_endpoint, state, pid_text = columns[1:5]
                normalized_state = {
                    "LISTENING": "Listen",
                    "ESTABLISHED": "Established",
                }.get(state.upper())
                if normalized_state is None:
                    continue
            else:
                if len(columns) < 4:
                    continue
                local_endpoint, remote_endpoint, pid_text = columns[1:4]
                normalized_state = None

            try:
                owner_pid = int(pid_text)
                local_address, local_port = ProcessPortDetector._split_endpoint(
                    local_endpoint
                )
            except (TypeError, ValueError):
                continue
            if process_id is not None and owner_pid != process_id:
                continue
            if local_port is None:
                continue

            row: dict[str, Any] = {
                "local_address": local_address,
                "local_port": local_port,
                "process_id": owner_pid,
            }
            if expected_protocol == "TCP":
                remote_address, remote_port = ProcessPortDetector._split_endpoint(
                    remote_endpoint
                )
                row.update({
                    "remote_address": remote_address,
                    "remote_port": remote_port,
                    "state": normalized_state,
                })
            rows.append(row)
        return rows

    @staticmethod
    def _split_endpoint(endpoint: str) -> tuple[str, Optional[int]]:
        address, separator, port_text = endpoint.rpartition(":")
        if not separator or port_text == "*":
            return endpoint.strip("[]"), None
        address = address.strip("[]")
        return address, int(port_text)

    def _run_ps(self, script: str, keyword: str) -> list[dict[str, Any]]:
        result = self._runner.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            timeout=self._timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{keyword} failed (rc={result.returncode}): {result.stderr.strip()[:200]}"
            )
        raw = result.stdout.strip()
        if not raw:
            return []
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse {keyword} JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
        return []

    # ── Classification ──────────────────────────────────────────────────

    def _classify_tcp(self, row: dict[str, Any]) -> PortCandidate:
        port = int(row.get("local_port", 0))
        state = str(row.get("state", ""))
        process_name = str(row.get("process_name", "")) if row.get("process_name") else None
        process_id = row.get("process_id") if row.get("process_id") is not None else None
        local_addr = str(row.get("local_address", "")) if row.get("local_address") else None
        remote_addr = str(row.get("remote_address", "")) if row.get("remote_address") else None
        remote_port = (
            int(row["remote_port"])
            if row.get("remote_port") is not None
            else None
        )

        if _is_noise_port(port):
            return PortCandidate("tcp", port, process_id, process_name,
                                local_addr, remote_addr, state, "low",
                                "noise: well-known port", remote_port)

        if state == "Listen":
            if _is_noise_process(process_name or ""):
                return PortCandidate("tcp", port, process_id, process_name,
                                    local_addr, remote_addr, state, "low",
                                    f"noise: {process_name} is not a game",
                                    remote_port)
            if local_addr and local_addr.startswith("127."):
                return PortCandidate("tcp", port, process_id, process_name,
                                    local_addr, remote_addr, state, "medium",
                                    f"TCP LISTEN on loopback, port {port}",
                                    remote_port)
            return PortCandidate("tcp", port, process_id, process_name,
                                local_addr, remote_addr, state, "high",
                                f"TCP LISTEN on {local_addr}:{port}",
                                remote_port)

        if state == "Established":
            if _is_noise_process(process_name or ""):
                return PortCandidate("tcp", port, process_id, process_name,
                                    local_addr, remote_addr, state, "low",
                                    f"noise: {process_name}", remote_port)
            if remote_addr and remote_addr.startswith("127."):
                return PortCandidate("tcp", port, process_id, process_name,
                                    local_addr, remote_addr, state, "medium",
                                    f"TCP ESTABLISHED loopback, port {port}",
                                    remote_port)
            return PortCandidate("tcp", port, process_id, process_name,
                                local_addr, remote_addr, state, "low",
                                f"TCP ESTABLISHED {remote_addr}:{row.get('remote_port', '?')}",
                                remote_port)

        if state in _NOISE_STATES:
            return PortCandidate("tcp", port, process_id, process_name,
                                local_addr, remote_addr, state, "low",
                                f"idle state: {state}", remote_port)

        return PortCandidate("tcp", port, process_id, process_name,
                            local_addr, remote_addr, state, "low",
                            f"state={state}", remote_port)

    def _classify_udp(self, row: dict[str, Any]) -> PortCandidate:
        port = int(row.get("local_port", 0))
        process_name = str(row.get("process_name", "")) if row.get("process_name") else None
        process_id = row.get("process_id") if row.get("process_id") is not None else None
        local_addr = str(row.get("local_address", "")) if row.get("local_address") else None

        if _is_noise_port(port):
            return PortCandidate("udp", port, process_id, process_name,
                                local_addr, None, None, "low", "noise: well-known port")

        if _is_noise_process(process_name or ""):
            return PortCandidate("udp", port, process_id, process_name,
                                local_addr, None, None, "low", f"noise: {process_name}")

        if port < 1024 and process_name is None:
            return PortCandidate("udp", port, process_id, process_name,
                                local_addr, None, None, "low", "system port, unknown process")

        if local_addr and local_addr.startswith("127."):
            return PortCandidate("udp", port, process_id, process_name,
                                local_addr, None, None, "medium",
                                f"UDP bound {local_addr}:{port}")

        return PortCandidate("udp", port, process_id, process_name,
                            local_addr, None, None, "high",
                            f"UDP bound {local_addr}:{port}")
