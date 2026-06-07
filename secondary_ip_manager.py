"""Local secondary IPv4 lease helper for the v0.3 implementation.

v0.3-I3 adds WindowsSecondaryIpSystem (PowerShell-backed), CommandRunner
abstraction, SubprocessCommandRunner, FakeCommandRunner, and JsonLeaseStore.
Real IP mutation is behind explicit opt-in; the default backend path remains Noop.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json as _json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


FALLBACK_BIND_HOST = "127.0.0.1"
LEASE_OWNER = "coopwing_secondary_ip_v0_3"


@dataclass(frozen=True)
class NetworkInterface:
    interface_index: int
    alias: str
    description: str
    ipv4_addresses: tuple[str, ...]
    is_up: bool = True
    is_loopback: bool = False
    is_virtual: bool = False
    is_tunnel: bool = False
    is_physical: bool = True
    metric: Optional[int] = None
    ipv4_prefix_lengths: tuple[int, ...] = ()
    ipv4_address_states: tuple[str, ...] = ()
    default_route: bool = False


@dataclass(frozen=True)
class SecondaryIpLease:
    interface_index: int
    interface_alias: str
    ip_address: str
    prefix_length: int
    created_at: float
    owner: str = LEASE_OWNER


@dataclass(frozen=True)
class SecondaryIpRequest:
    ip_address: str
    interface_hint: Optional[str] = None
    prefix_length: Optional[int] = None


@dataclass(frozen=True)
class SecondaryIpResult:
    ok: bool
    bind_host: str
    lease: Optional[SecondaryIpLease] = None
    fallback_used: bool = False
    reason: Optional[str] = None
    warning: Optional[str] = None
    already_present: bool = False
    backend_admin: bool = False
    target_interface_index: Optional[int] = None
    target_interface_alias: Optional[str] = None
    bind_mode: str = "loopback"


@dataclass(frozen=True)
class CleanupItem:
    lease: SecondaryIpLease
    status: str
    error: Optional[str] = None


@dataclass(frozen=True)
class CleanupResult:
    items: list[CleanupItem]
    ok: bool


@dataclass(frozen=True)
class AdapterBindDecision:
    bind_host: str
    secondary_ip_enabled: bool
    fallback_used: bool
    warning: Optional[str] = None
    backend_admin: bool = False
    target_interface_index: Optional[int] = None
    target_interface_alias: Optional[str] = None
    bind_mode: str = "loopback"


@dataclass(frozen=True)
class SecondaryIpRecommendation:
    available: bool
    backend_admin: bool
    interface_index: Optional[int] = None
    interface_alias: Optional[str] = None
    interface_description: Optional[str] = None
    interface_ip: Optional[str] = None
    prefix_length: Optional[int] = None
    recommended_ip: Optional[str] = None
    reason: Optional[str] = None
    warning: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "backend_admin": self.backend_admin,
            "interface_index": self.interface_index,
            "interface_alias": self.interface_alias,
            "interface_description": self.interface_description,
            "interface_ip": self.interface_ip,
            "prefix_length": self.prefix_length,
            "recommended_ip": self.recommended_ip,
            "reason": self.reason,
            "warning": self.warning,
        }


class SecondaryIpSystem:
    def has_ip_mutation_permission(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def list_interfaces(self) -> list[NetworkInterface]:
        raise NotImplementedError("Interface enumeration is not wired")

    def list_interface_ipv4(self, interface_index: int) -> list[str]:
        raise NotImplementedError("Address enumeration is not wired")

    def add_ip(self, lease: SecondaryIpLease) -> None:
        raise NotImplementedError("Address mutation is not wired")

    def remove_ip(self, lease: SecondaryIpLease) -> None:
        raise NotImplementedError("Address mutation is not wired")


# ══════════════════════════════════════════════════════════════════════════════
# Command runner abstraction (v0.3-I3)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Abstract command runner. Tests inject FakeCommandRunner."""

    def run(self, args: list[str], timeout: float = 10.0) -> CommandResult:
        raise NotImplementedError


class SubprocessCommandRunner(CommandRunner):
    """Real command runner using subprocess.run.  shell=False, text=True."""

    def run(self, args: list[str], timeout: float = 10.0) -> CommandResult:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


class FakeCommandRunner(CommandRunner):
    """Test-only runner with a programmable registry of command -> result."""

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
        raise RuntimeError(
            f"FakeCommandRunner: no response registered for command args={args}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Windows PowerShell-backed Secondary IP system (v0.3-I3)
# ══════════════════════════════════════════════════════════════════════════════

_PS_LIST_SCRIPT = (
    "$defaultRoute=(Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
    "-ErrorAction SilentlyContinue "
    "| Sort-Object RouteMetric,InterfaceMetric "
    "| Select-Object -First 1 -ExpandProperty InterfaceIndex);"
    "Get-NetAdapter | ForEach-Object {"
    "$a=$_;"
    "$ipObjs=@(Get-NetIPAddress -InterfaceIndex $a.InterfaceIndex "
    "-AddressFamily IPv4 -ErrorAction SilentlyContinue "
    "| Where-Object {$_.AddressState -ne 'Deprecated'});"
    "[PSCustomObject]@{"
    "interface_index=$a.InterfaceIndex;"
    "alias=$a.Name;"
    "description=$a.InterfaceDescription;"
    "status=$a.Status;"
    "hardware_interface=$a.HardwareInterface;"
    "interface_metric=$a.InterfaceMetric;"
    "default_route=($a.InterfaceIndex -eq $defaultRoute);"
    "ipv4_addresses=@($ipObjs | Select-Object -ExpandProperty IPAddress);"
    "ipv4_prefix_lengths=@($ipObjs | Select-Object -ExpandProperty PrefixLength);"
    "ipv4_address_states=@($ipObjs | Select-Object -ExpandProperty AddressState)"
    "}} | ConvertTo-Json"
)

_LOOPBACK_VIRTUAL_TUNNEL_KEYWORDS: tuple[str, ...] = (
    "loopback", "virtual", "hyper-v", "vmware", "virtualbox", "bluetooth",
    "tap", "tun", "tunnel", "vpn", "wireguard", "tailscale", "zerotier",
    "teredo",
)


class WindowsSecondaryIpSystem(SecondaryIpSystem):
    """PowerShell-backed SecondaryIpSystem for Windows.

    Uses an injected CommandRunner so unit tests can inject FakeCommandRunner.
    Real IP mutation only occurs with SubprocessCommandRunner.
    """

    def __init__(
        self,
        runner: CommandRunner,
        timeout: float = 10.0,
    ) -> None:
        self._runner = runner
        self._timeout = timeout

    def has_ip_mutation_permission(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # ── list_interfaces ─────────────────────────────────────────────────

    def list_interfaces(self) -> list[NetworkInterface]:
        result = self._runner.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-Command", _PS_LIST_SCRIPT,
            ],
            timeout=self._timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PowerShell Get-NetAdapter failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:200]}"
            )

        raw = result.stdout.strip()
        if not raw:
            return []

        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse PowerShell interface list JSON: {exc}"
            ) from exc

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise RuntimeError(
                f"Unexpected PowerShell output type: {type(parsed).__name__}"
            )

        interfaces: list[NetworkInterface] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                interface = self._parse_interface_entry(entry)
                if interface is not None:
                    interfaces.append(interface)
            except Exception:
                continue
        return interfaces

    def _parse_interface_entry(self, entry: dict) -> NetworkInterface | None:
        index = entry.get("interface_index")
        if not isinstance(index, int):
            return None
        alias = entry.get("alias", "")
        description = entry.get("description", "")
        status = str(entry.get("status", "")).lower()
        is_up = status == "up"
        hardware_interface = entry.get("hardware_interface", False)
        is_physical = bool(hardware_interface)

        ipv4_raw = entry.get("ipv4_addresses", [])
        if isinstance(ipv4_raw, str):
            ipv4_addresses: tuple[str, ...] = (ipv4_raw,)
        elif isinstance(ipv4_raw, list):
            ipv4_addresses = tuple(str(a) for a in ipv4_raw if a is not None)
        else:
            ipv4_addresses = ()

        prefix_raw = entry.get("ipv4_prefix_lengths", [])
        if isinstance(prefix_raw, int):
            ipv4_prefix_lengths: tuple[int, ...] = (prefix_raw,)
        elif isinstance(prefix_raw, list):
            ipv4_prefix_lengths = tuple(
                int(p) for p in prefix_raw if isinstance(p, int)
            )
        else:
            ipv4_prefix_lengths = ()

        state_raw = entry.get("ipv4_address_states", [])
        if isinstance(state_raw, str):
            ipv4_address_states: tuple[str, ...] = (state_raw,)
        elif isinstance(state_raw, list):
            ipv4_address_states = tuple(str(s) for s in state_raw if s is not None)
        else:
            ipv4_address_states = ()

        metric_raw = entry.get("interface_metric")
        metric: int | None = None
        if isinstance(metric_raw, int):
            metric = metric_raw

        combined = f"{alias} {description}".lower()
        is_loopback = any(kw in combined for kw in _LOOPBACK_VIRTUAL_TUNNEL_KEYWORDS)
        is_virtual = is_loopback
        is_tunnel = "tunnel" in combined or "tun" in combined or "vpn" in combined

        return NetworkInterface(
            interface_index=index,
            alias=str(alias),
            description=str(description),
            ipv4_addresses=ipv4_addresses,
            is_up=is_up,
            is_loopback=is_loopback,
            is_virtual=is_virtual,
            is_tunnel=is_tunnel,
            is_physical=is_physical and not is_loopback and not is_virtual and not is_tunnel,
            metric=metric,
            ipv4_prefix_lengths=ipv4_prefix_lengths,
            ipv4_address_states=ipv4_address_states,
            default_route=bool(entry.get("default_route", False)),
        )

    # ── list_interface_ipv4 ─────────────────────────────────────────────

    def list_interface_ipv4(self, interface_index: int) -> list[str]:
        script = (
            f"Get-NetIPAddress -InterfaceIndex {interface_index} "
            f"-AddressFamily IPv4 -ErrorAction SilentlyContinue "
            f"| Select-Object -ExpandProperty IPAddress | ConvertTo-Json"
        )
        result = self._runner.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-Command", script,
            ],
            timeout=self._timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"PowerShell Get-NetIPAddress -InterfaceIndex {interface_index} failed "
                f"(rc={result.returncode}): {result.stderr.strip()[:200]}"
            )

        raw = result.stdout.strip()
        if not raw:
            return []

        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse PowerShell address list JSON: {exc}"
            ) from exc

        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, list):
            return [str(a) for a in parsed if a is not None]
        return []

    # ── add_ip ──────────────────────────────────────────────────────────

    def add_ip(self, lease: SecondaryIpLease) -> None:
        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            (
                f"New-NetIPAddress -InterfaceIndex {lease.interface_index} "
                f"-IPAddress {lease.ip_address} "
                f"-PrefixLength {lease.prefix_length} "
                f"-SkipAsSource $true "
                f"-ErrorAction Stop"
            ),
        ]
        result = self._runner.run(cmd, timeout=self._timeout)
        if result.returncode != 0:
            self._raise_mapped_error(result, "add", lease)

    # ── remove_ip ───────────────────────────────────────────────────────

    def remove_ip(self, lease: SecondaryIpLease) -> None:
        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            (
                f"Remove-NetIPAddress -InterfaceIndex {lease.interface_index} "
                f"-IPAddress {lease.ip_address} "
                f"-Confirm:$false "
                f"-ErrorAction Stop"
            ),
        ]
        result = self._runner.run(cmd, timeout=self._timeout)
        if result.returncode != 0:
            self._raise_mapped_error(result, "remove", lease)

    @staticmethod
    def _raise_mapped_error(
        result: CommandResult,
        action: str,
        lease: SecondaryIpLease,
    ) -> None:
        combined = f"{result.stderr} {result.stdout}".lower()
        recovery_ps = (
            f"Remove-NetIPAddress -InterfaceIndex {lease.interface_index} "
            f"-IPAddress {lease.ip_address} -Confirm:$false -ErrorAction Stop"
        )
        recovery_netsh = (
            f'netsh interface ipv4 delete address '
            f'name="{lease.interface_alias}" addr={lease.ip_address}'
        )
        recovery = (
            f"Manual recovery (Administrator PowerShell):\n"
            f"  {recovery_ps}\n"
            f"  # or via netsh:\n"
            f"  {recovery_netsh}"
        )
        if any(
            kw in combined
            for kw in ("access denied", "administrator", "privilege", "denied",
                       "run as administrator", "elevation")
        ):
            raise PermissionError(
                f"Permission denied: cannot {action} IP {lease.ip_address} "
                f"on interface {lease.interface_index}\n"
                f"{recovery}"
            )
        raise RuntimeError(
            f"Failed to {action} IP {lease.ip_address} "
            f"on interface {lease.interface_index} "
            f"(rc={result.returncode}): {result.stderr.strip()[:200]}\n"
            f"{recovery}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# JSON file-backed LeaseStore (v0.3-I3)
# ══════════════════════════════════════════════════════════════════════════════

class LeaseStore:
    def load(self) -> list[SecondaryIpLease]:
        raise NotImplementedError

    def save(self, leases: list[SecondaryIpLease]) -> None:
        raise NotImplementedError


class InMemoryLeaseStore(LeaseStore):
    def __init__(self, leases: Optional[list[SecondaryIpLease]] = None):
        self._leases = list(leases or [])

    def load(self) -> list[SecondaryIpLease]:
        return list(self._leases)

    def save(self, leases: list[SecondaryIpLease]) -> None:
        self._leases = list(leases)


# ══════════════════════════════════════════════════════════════════════════════
# JSON file-backed LeaseStore (v0.3-I3)
# ══════════════════════════════════════════════════════════════════════════════

class JsonLeaseStore(LeaseStore):
    """Persists SecondaryIpLease records to a JSON file.

    The path must be explicitly passed.  The JSON store contains only lease
    metadata (interface index/alias, IP, prefix, timestamp, owner).
    No S2Pass protocol or identity fields are stored.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            path = Path(path)
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[SecondaryIpLease]:
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read lease file {self._path}: {exc}"
            ) from exc
        try:
            raw = _json.loads(text)
        except _json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Malformed lease JSON in {self._path}: {exc}"
            ) from exc
        if not isinstance(raw, list):
            raise RuntimeError(
                f"Lease file {self._path} must contain a JSON array"
            )
        leases: list[SecondaryIpLease] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                leases.append(
                    SecondaryIpLease(
                        interface_index=int(entry["interface_index"]),
                        interface_alias=str(entry["interface_alias"]),
                        ip_address=str(entry["ip_address"]),
                        prefix_length=int(entry["prefix_length"]),
                        created_at=float(entry["created_at"]),
                        owner=str(entry.get("owner", LEASE_OWNER)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return leases

    def save(self, leases: list[SecondaryIpLease]) -> None:
        raw: list[dict] = []
        for lease in leases:
            raw.append({
                "interface_index": lease.interface_index,
                "interface_alias": lease.interface_alias,
                "ip_address": lease.ip_address,
                "prefix_length": lease.prefix_length,
                "created_at": lease.created_at,
                "owner": lease.owner,
            })
        text = _json.dumps(raw, indent=2, ensure_ascii=False, sort_keys=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".secondary_ip_leases_",
            dir=str(self._path.parent),
        )
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(self._path))


class LeaseStore:
    def load(self) -> list[SecondaryIpLease]:
        raise NotImplementedError

    def save(self, leases: list[SecondaryIpLease]) -> None:
        raise NotImplementedError


class InMemoryLeaseStore(LeaseStore):
    def __init__(self, leases: Optional[list[SecondaryIpLease]] = None):
        self._leases = list(leases or [])

    def load(self) -> list[SecondaryIpLease]:
        return list(self._leases)

    def save(self, leases: list[SecondaryIpLease]) -> None:
        self._leases = list(leases)


def validate_secondary_ipv4(ip: str) -> str:
    value = (ip or "").strip()
    if value == "auto":
        raise ValueError("auto secondary IP allocation is not implemented")
    if not value:
        raise ValueError("IP address is required")
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"Invalid IPv4 address: {value}") from exc
    if parsed.version != 4:
        raise ValueError("IPv6 is not supported for secondary IP binding")
    if parsed.is_loopback:
        raise ValueError("Loopback addresses are not valid secondary interface IPs")
    if parsed.is_multicast:
        raise ValueError("Multicast addresses are not valid secondary interface IPs")
    if parsed.is_link_local:
        raise ValueError("Link-local addresses are not valid secondary interface IPs")
    if parsed.is_unspecified:
        raise ValueError("Unspecified address is not valid")
    if str(parsed) == "255.255.255.255":
        raise ValueError("Broadcast address is not valid")
    return str(parsed)


def filter_eligible_interfaces(
    interfaces: list[NetworkInterface],
) -> list[NetworkInterface]:
    eligible = [
        interface
        for interface in interfaces
        if interface.is_up
        and not interface.is_loopback
        and not interface.is_virtual
        and not interface.is_tunnel
        and not _has_blocked_interface_keyword(interface)
        and interface.is_physical
        and any(
            _is_usable_interface_ipv4(addr, state)
            for addr, _, state in _interface_ipv4_entries(interface)
        )
    ]
    indexed = list(enumerate(eligible))
    indexed.sort(
        key=lambda item: (
            not item[1].default_route,
            item[1].metric is None,
            item[1].metric if item[1].metric is not None else 0,
            item[0],
        )
    )
    return [interface for _, interface in indexed]


def _has_blocked_interface_keyword(interface: NetworkInterface) -> bool:
    combined = f"{interface.alias} {interface.description}".casefold()
    return any(keyword in combined for keyword in _LOOPBACK_VIRTUAL_TUNNEL_KEYWORDS)


def _interface_ipv4_entries(
    interface: NetworkInterface,
) -> list[tuple[str, int, str]]:
    entries: list[tuple[str, int, str]] = []
    for index, address in enumerate(interface.ipv4_addresses):
        prefix = (
            interface.ipv4_prefix_lengths[index]
            if index < len(interface.ipv4_prefix_lengths)
            else 24
        )
        state = (
            interface.ipv4_address_states[index]
            if index < len(interface.ipv4_address_states)
            else ""
        )
        entries.append((address, prefix, state))
    return entries


def _is_usable_interface_ipv4(value: str, state: str = "") -> bool:
    if state.casefold() == "deprecated":
        return False
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        parsed.version == 4
        and not parsed.is_loopback
        and not parsed.is_link_local
        and not parsed.is_multicast
        and not parsed.is_unspecified
    )


def _target_matches_interface_subnet(
    target_ip: str,
    prefix_length: int,
    interface: NetworkInterface,
) -> bool:
    target = ipaddress.ip_address(target_ip)
    for address, _, state in _interface_ipv4_entries(interface):
        if not _is_usable_interface_ipv4(address, state):
            continue
        network = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
        if target in network:
            return True
    return False


def _primary_usable_ipv4_entry(
    interface: NetworkInterface,
) -> Optional[tuple[str, int]]:
    for address, prefix, state in _interface_ipv4_entries(interface):
        if _is_usable_interface_ipv4(address, state):
            return (str(ipaddress.ip_address(address)), prefix)
    return None


def _all_known_ipv4_addresses(interfaces: list[NetworkInterface]) -> set[str]:
    addresses: set[str] = set()
    for interface in interfaces:
        for address in interface.ipv4_addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if parsed.version == 4:
                addresses.add(str(parsed))
    return addresses


def _recommend_ip_in_network(
    network: ipaddress.IPv4Network,
    existing_ips: set[str],
) -> Optional[str]:
    start = int(network.network_address) + 1
    end = int(network.broadcast_address) - 1
    if network.prefixlen >= 31:
        start = int(network.network_address)
        end = int(network.broadcast_address)
    if start > end:
        return None

    preferred_last_octets = (233, 250, 240, 222, 200)
    for last_octet in preferred_last_octets:
        parts = str(network.network_address).split(".")
        candidate = ipaddress.ip_address(
            ".".join([parts[0], parts[1], parts[2], str(last_octet)])
        )
        candidate_text = str(candidate)
        if candidate in network and start <= int(candidate) <= end:
            if candidate_text not in existing_ips:
                return candidate_text

    for value in range(end, max(start - 1, end - 256), -1):
        candidate_text = str(ipaddress.ip_address(value))
        if candidate_text not in existing_ips:
            return candidate_text
    return None


def select_interface(
    interfaces: list[NetworkInterface],
    interface_hint: Optional[str],
) -> Optional[NetworkInterface]:
    eligible = filter_eligible_interfaces(interfaces)
    if not interface_hint:
        return eligible[0] if eligible else None

    hint = interface_hint.strip()
    if hint.isdecimal():
        target_index = int(hint)
        for interface in eligible:
            if interface.interface_index == target_index:
                return interface
        return None

    lowered_hint = hint.casefold()
    for interface in eligible:
        if interface.alias.casefold() == lowered_hint:
            return interface
    return None


def _fallback(
    reason: str,
    warning: Optional[str] = None,
    *,
    backend_admin: bool = False,
    interface: Optional[NetworkInterface] = None,
) -> SecondaryIpResult:
    return SecondaryIpResult(
        ok=False,
        bind_host=FALLBACK_BIND_HOST,
        fallback_used=True,
        reason=reason,
        warning=warning or reason,
        backend_admin=backend_admin,
        target_interface_index=(
            interface.interface_index if interface is not None else None
        ),
        target_interface_alias=interface.alias if interface is not None else None,
    )


def _lease_key(lease: SecondaryIpLease) -> tuple[int, str]:
    return (lease.interface_index, lease.ip_address)


def _with_lease(
    leases: list[SecondaryIpLease],
    lease: SecondaryIpLease,
) -> list[SecondaryIpLease]:
    if any(_lease_key(existing) == _lease_key(lease) for existing in leases):
        return list(leases)
    return [*leases, lease]


@dataclass(frozen=True)
class SecondaryIpStatus:
    allocated: bool
    backend_admin: bool
    interface_index: Optional[int] = None
    interface_alias: Optional[str] = None
    allocated_ip: Optional[str] = None
    prefix_length: Optional[int] = None
    bind_mode: str = "loopback"
    source: str = "none"
    last_error: Optional[str] = None
    original_dhcp_ips: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "allocated": self.allocated,
            "backend_admin": self.backend_admin,
            "interface_index": self.interface_index,
            "interface_alias": self.interface_alias,
            "allocated_ip": self.allocated_ip,
            "prefix_length": self.prefix_length,
            "bind_mode": self.bind_mode,
            "source": self.source,
            "last_error": self.last_error,
            "original_dhcp_ips": list(self.original_dhcp_ips),
        }


_NOOP_STATUS = SecondaryIpStatus(
    allocated=False,
    backend_admin=False,
)


class SecondaryIpManager:
    def __init__(
        self,
        system: SecondaryIpSystem,
        lease_store: LeaseStore,
        now: Callable[[], float] = time.time,
    ):
        self.system = system
        self.lease_store = lease_store
        self.now = now
        self._current_allocation: Optional[SecondaryIpLease] = None
        self._original_dhcp_ips: tuple[str, ...] = ()
        self._last_error: Optional[str] = None
        self._allocation_source: str = "none"

    def has_ip_mutation_permission(self) -> bool:
        return self.system.has_ip_mutation_permission()

    def recommend_secondary_ip(
        self,
        interface_hint: Optional[str] = None,
    ) -> SecondaryIpRecommendation:
        backend_admin = self.has_ip_mutation_permission()
        try:
            interfaces = self.system.list_interfaces()
        except Exception as exc:
            return SecondaryIpRecommendation(
                available=False,
                backend_admin=backend_admin,
                reason="interface_list_failed",
                warning=f"failed to list network interfaces: {exc}",
            )

        interface = select_interface(interfaces, interface_hint)
        if interface is None:
            return SecondaryIpRecommendation(
                available=False,
                backend_admin=backend_admin,
                reason="no_eligible_interface",
                warning="no eligible physical IPv4 interface is available",
            )

        primary = _primary_usable_ipv4_entry(interface)
        if primary is None:
            return SecondaryIpRecommendation(
                available=False,
                backend_admin=backend_admin,
                interface_index=interface.interface_index,
                interface_alias=interface.alias,
                interface_description=interface.description,
                reason="no_usable_ipv4",
                warning="selected interface has no usable IPv4 address",
            )

        interface_ip, prefix_length = primary
        network = ipaddress.ip_network(
            f"{interface_ip}/{prefix_length}",
            strict=False,
        )
        existing_ips = _all_known_ipv4_addresses(interfaces)
        recommended_ip = _recommend_ip_in_network(network, existing_ips)
        if recommended_ip is None:
            return SecondaryIpRecommendation(
                available=False,
                backend_admin=backend_admin,
                interface_index=interface.interface_index,
                interface_alias=interface.alias,
                interface_description=interface.description,
                interface_ip=interface_ip,
                prefix_length=prefix_length,
                reason="no_free_candidate",
                warning="no obvious unused IPv4 candidate is available",
            )

        return SecondaryIpRecommendation(
            available=True,
            backend_admin=backend_admin,
            interface_index=interface.interface_index,
            interface_alias=interface.alias,
            interface_description=interface.description,
            interface_ip=interface_ip,
            prefix_length=prefix_length,
            recommended_ip=recommended_ip,
        )

    def ensure_secondary_ip(self, request: SecondaryIpRequest) -> SecondaryIpResult:
        try:
            target_ip = validate_secondary_ipv4(request.ip_address)
        except ValueError as exc:
            reason = (
                "auto_not_implemented"
                if (request.ip_address or "").strip() == "auto"
                else "invalid_ip"
            )
            return _fallback(reason, str(exc))

        if request.prefix_length is None:
            prefix_length = 24
        elif 1 <= request.prefix_length <= 32:
            prefix_length = request.prefix_length
        else:
            return _fallback(
                "invalid_prefix_length",
                "prefix_length must be between 1 and 32",
            )

        backend_admin = self.has_ip_mutation_permission()
        if not backend_admin:
            return _fallback(
                "not_admin",
                (
                    "backend process is not elevated; please restart as "
                    "administrator and make sure the old backend process is stopped"
                ),
                backend_admin=False,
            )

        interfaces = self.system.list_interfaces()
        interface = select_interface(
            interfaces,
            request.interface_hint,
        )
        if interface is None:
            return _fallback(
                "no_eligible_interface",
                "no eligible physical IPv4 interface is available",
                backend_admin=backend_admin,
            )
        existing_interface = _find_interface_with_ip(interfaces, target_ip)
        if existing_interface is not None:
            return _fallback(
                "ip_already_exists",
                (
                    f"IP already exists on interface "
                    f"{existing_interface.interface_index} "
                    f"({existing_interface.alias})"
                ),
                backend_admin=backend_admin,
                interface=existing_interface,
            )
        if not _target_matches_interface_subnet(target_ip, prefix_length, interface):
            return _fallback(
                "subnet_mismatch",
                (
                    f"secondary IP {target_ip}/{prefix_length} is not in the same "
                    f"subnet as interface {interface.interface_index} "
                    f"({interface.alias})"
                ),
                backend_admin=backend_admin,
                interface=interface,
            )

        lease = SecondaryIpLease(
            interface_index=interface.interface_index,
            interface_alias=interface.alias,
            ip_address=target_ip,
            prefix_length=prefix_length,
            created_at=self.now(),
        )

        try:
            current_addresses = self.system.list_interface_ipv4(interface.interface_index)
            if target_ip in current_addresses:
                return _fallback(
                    "ip_already_exists",
                    (
                        f"IP already exists on interface "
                        f"{interface.interface_index} ({interface.alias})"
                    ),
                    backend_admin=backend_admin,
                    interface=interface,
                )

            original_ips = tuple(sorted(current_addresses))

            self.system.add_ip(lease)
            verified_addresses = self.system.list_interface_ipv4(
                interface.interface_index
            )
            if target_ip not in verified_addresses:
                self._best_effort_remove_if_present(lease)
                raise RuntimeError(
                    f"verification failed after New-NetIPAddress: "
                    f"{target_ip} is not present on interface "
                    f"{interface.interface_index} ({interface.alias})"
                )

            missing_original = [
                ip for ip in original_ips if ip not in verified_addresses
            ]
            if missing_original:
                self._best_effort_remove_if_present(lease)
                return _fallback(
                    "dhcp_displaced",
                    (
                        f"secondary IP {target_ip} displaced existing IPv4 "
                        f"addresses {missing_original} on interface "
                        f"{interface.interface_index} ({interface.alias}). "
                        f"secondary IP has been removed to protect DHCP. "
                        f"Please check your network configuration."
                    ),
                    backend_admin=backend_admin,
                    interface=interface,
                )

            self._record_lease(lease)
            self._current_allocation = lease
            self._original_dhcp_ips = original_ips
            self._last_error = None
            return SecondaryIpResult(
                ok=True,
                bind_host=target_ip,
                lease=lease,
                backend_admin=backend_admin,
                target_interface_index=interface.interface_index,
                target_interface_alias=interface.alias,
                bind_mode="secondary_ip",
            )
        except PermissionError as exc:
            self._last_error = str(exc)
            return _fallback(
                "permission_denied",
                str(exc),
                backend_admin=backend_admin,
                interface=interface,
            )
        except Exception as exc:
            self._best_effort_remove_if_present(lease)
            self._last_error = str(exc)
            return _fallback(
                "add_failed",
                f"failed to add secondary IP: {exc}",
                backend_admin=backend_admin,
                interface=interface,
            )

    def remove_secondary_ip(self, ip_address: Optional[str] = None) -> CleanupResult:
        leases = self.lease_store.load()
        selected = [
            lease for lease in leases if ip_address is None or lease.ip_address == ip_address
        ]
        if not selected:
            return CleanupResult(items=[], ok=True)

        remaining = list(leases)
        items: list[CleanupItem] = []
        for lease in selected:
            try:
                current_addresses = self.system.list_interface_ipv4(lease.interface_index)
                if lease.ip_address in current_addresses:
                    self.system.remove_ip(lease)
                    status = "removed"
                else:
                    status = "already_absent"
                remaining = [
                    existing
                    for existing in remaining
                    if _lease_key(existing) != _lease_key(lease)
                ]
                items.append(CleanupItem(lease=lease, status=status))
            except Exception as exc:
                items.append(CleanupItem(lease=lease, status="error", error=str(exc)))

        self.lease_store.save(remaining)
        return CleanupResult(
            items=items,
            ok=all(item.status != "error" for item in items),
        )

    def choose_adapter_bind_host(
        self,
        requested_ip: Optional[str],
        default_bind_host: str = FALLBACK_BIND_HOST,
        interface_hint: Optional[str] = None,
        prefix_length: Optional[int] = None,
    ) -> AdapterBindDecision:
        if not requested_ip:
            return AdapterBindDecision(
                bind_host=default_bind_host or FALLBACK_BIND_HOST,
                secondary_ip_enabled=False,
                fallback_used=False,
                backend_admin=self.has_ip_mutation_permission(),
            )

        result = self.ensure_secondary_ip(
            SecondaryIpRequest(
                ip_address=requested_ip,
                interface_hint=interface_hint,
                prefix_length=prefix_length,
            )
        )
        if result.ok:
            return AdapterBindDecision(
                bind_host=result.bind_host,
                secondary_ip_enabled=True,
                fallback_used=False,
                backend_admin=result.backend_admin,
                target_interface_index=result.target_interface_index,
                target_interface_alias=result.target_interface_alias,
                bind_mode="secondary_ip",
            )
        return AdapterBindDecision(
            bind_host=FALLBACK_BIND_HOST,
            secondary_ip_enabled=False,
            fallback_used=True,
            warning=result.warning or result.reason,
            backend_admin=result.backend_admin,
            target_interface_index=result.target_interface_index,
            target_interface_alias=result.target_interface_alias,
        )

    def startup_cleanup_stale_leases(self) -> CleanupResult:
        """Remove stale lease IPs found on any interface at startup."""
        try:
            leases = self.lease_store.load()
        except Exception:
            return CleanupResult(items=[], ok=True)
        if not leases:
            return CleanupResult(items=[], ok=True)

        remaining = list(leases)
        items: list[CleanupItem] = []
        for lease in leases:
            try:
                current = self.system.list_interface_ipv4(lease.interface_index)
            except Exception:
                continue
            if lease.ip_address in current:
                try:
                    self.system.remove_ip(lease)
                    items.append(CleanupItem(lease=lease, status="removed"))
                except Exception as exc:
                    items.append(CleanupItem(lease=lease, status="error", error=str(exc)))
            else:
                remaining = [
                    ex for ex in remaining
                    if _lease_key(ex) != _lease_key(lease)
                ]
                items.append(CleanupItem(lease=lease, status="already_absent"))
        self.lease_store.save(remaining)
        return CleanupResult(
            items=items,
            ok=all(item.status != "error" for item in items),
        )

    def auto_allocate_on_admin_startup(self) -> SecondaryIpStatus:
        """Attempt safe automatic secondary IP allocation for an admin backend.

        Only proceeds when backend is elevated, a recommendation is available,
        and DHCP preservation succeeds.  Stores the allocation so
        :meth:`release_allocated_secondary_ip` can clean it up later.
        """
        self._allocation_source = "auto"
        if not self.has_ip_mutation_permission():
            self._last_error = "backend is not running as administrator"
            return self.get_secondary_ip_status()

        try:
            recommendation = self.recommend_secondary_ip()
        except Exception as exc:
            self._last_error = f"recommendation failed: {exc}"
            return self.get_secondary_ip_status()

        if not recommendation.available or not recommendation.recommended_ip:
            self._last_error = recommendation.warning or recommendation.reason or "no recommendation available"
            return self.get_secondary_ip_status()

        result = self.ensure_secondary_ip(
            SecondaryIpRequest(
                ip_address=recommendation.recommended_ip,
                interface_hint=(
                    str(recommendation.interface_index)
                    if recommendation.interface_index is not None
                    else None
                ),
                prefix_length=recommendation.prefix_length,
            )
        )
        if not result.ok:
            self._last_error = result.warning or result.reason
        return self.get_secondary_ip_status()

    def release_allocated_secondary_ip(self) -> CleanupResult:
        """Release the secondary IP allocated during this session.

        Safe to call when no allocation exists — returns an empty success result.
        """
        if self._current_allocation is None:
            return CleanupResult(items=[], ok=True)
        lease = self._current_allocation
        result = self.remove_secondary_ip(lease.ip_address)
        if result.ok:
            self._current_allocation = None
            self._original_dhcp_ips = ()
            self._allocation_source = "none"
        return result

    def get_secondary_ip_status(self) -> SecondaryIpStatus:
        """Return a snapshot of the current secondary IP state."""
        allocated = self._current_allocation is not None
        return SecondaryIpStatus(
            allocated=allocated,
            backend_admin=self.has_ip_mutation_permission(),
            interface_index=(
                self._current_allocation.interface_index
                if allocated else None
            ),
            interface_alias=(
                self._current_allocation.interface_alias
                if allocated else None
            ),
            allocated_ip=(
                self._current_allocation.ip_address if allocated else None
            ),
            prefix_length=(
                self._current_allocation.prefix_length if allocated else None
            ),
            bind_mode="secondary_ip" if allocated else "loopback",
            source=self._allocation_source,
            last_error=self._last_error,
            original_dhcp_ips=self._original_dhcp_ips,
        )

    def _record_lease(self, lease: SecondaryIpLease) -> None:
        self.lease_store.save(_with_lease(self.lease_store.load(), lease))

    def _best_effort_remove_if_present(self, lease: SecondaryIpLease) -> None:
        try:
            current_addresses = self.system.list_interface_ipv4(lease.interface_index)
            if lease.ip_address in current_addresses:
                self.system.remove_ip(lease)
        except Exception:
            return


def _find_interface_with_ip(
    interfaces: list[NetworkInterface],
    target_ip: str,
) -> Optional[NetworkInterface]:
    for interface in interfaces:
        if target_ip in interface.ipv4_addresses:
            return interface
    return None
