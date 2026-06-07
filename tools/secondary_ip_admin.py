#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v0.3-I3 Secondary IP Admin Tool — manual, explicit opt-in only.

Safety:
  - --list is always read-only (no --yes required).
  - --add / --remove require --yes to mutate system IP configuration.
  - --dry-run prints the planned action but never mutates.
  - Default lease path: %LOCALAPPDATA%\\Co-opWinG\\secondary_ip_leases.json

This tool does NOT import server, network_core, backend, adapters, or Flutter.
It does NOT construct or expose any S2Pass protocol payloads or tokens.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secondary_ip_manager import (
    FALLBACK_BIND_HOST,
    LEASE_OWNER,
    JsonLeaseStore,
    SecondaryIpLease,
    SecondaryIpManager,
    SubprocessCommandRunner,
    WindowsSecondaryIpSystem,
    filter_eligible_interfaces,
    validate_secondary_ipv4,
)


def _default_lease_path() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "Co-opWinG", "secondary_ip_leases.json")
    return os.path.join(os.path.expanduser("~"), ".coopwing", "secondary_ip_leases.json")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="v0.3-I3 Secondary IP Admin Tool. "
        "This tool modifies local Windows network adapter IP configuration. "
        "Use --verbose to inspect the plan. "
        "If removal fails, use the printed manual recovery command.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List network interfaces (read-only)")
    group.add_argument("--add", metavar="IP", help="Add a secondary IP address")
    group.add_argument("--remove", metavar="IP|all", help="Remove a secondary IP address")

    parser.add_argument(
        "--interface", dest="interface_hint",
        help="Interface index or alias for --add/--remove",
    )
    parser.add_argument(
        "--prefix-length", type=int, default=24,
        help="Prefix length for --add (1-32, default: 24)",
    )
    parser.add_argument(
        "--lease-file",
        default=_default_lease_path(),
        help="Path to lease JSON file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print plan, never mutate")
    parser.add_argument("--yes", action="store_true", help="Confirm mutation")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    return parser


def _print_interfaces(system: WindowsSecondaryIpSystem, verbose: bool) -> None:
    print("Network interfaces:")
    try:
        interfaces = system.list_interfaces()
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return

    if not interfaces:
        print("  (none)")
        return

    eligible = filter_eligible_interfaces(interfaces)
    eligible_indices = {i.interface_index for i in eligible}

    for iface in interfaces:
        tag = " [ELIGIBLE]" if iface.interface_index in eligible_indices else ""
        status = "Up" if iface.is_up else "Down"
        addrs = ", ".join(iface.ipv4_addresses) if iface.ipv4_addresses else "(no IPv4)"
        print(f"  [{iface.interface_index}] {iface.alias}  {status}  {addrs}{tag}")
        if verbose:
            print(f"      desc={iface.description}  physical={iface.is_physical}"
                  f"  virtual={iface.is_virtual}  metric={iface.metric}")


def _print_leases(store: JsonLeaseStore) -> None:
    try:
        leases = store.load()
    except Exception as exc:
        print(f"  ERROR loading leases: {exc}")
        return
    if not leases:
        print("  (no leases recorded)")
        return
    print("Recorded leases:")
    for lease in leases:
        print(f"  [{lease.interface_index}] {lease.ip_address}/{lease.prefix_length}"
              f"  alias={lease.interface_alias}  owner={lease.owner}")


def _run_list(args: argparse.Namespace) -> int:
    runner = SubprocessCommandRunner()
    system = WindowsSecondaryIpSystem(runner)
    store = JsonLeaseStore(args.lease_file)
    if args.verbose:
        print(f"lease file: {store.path}")
    _print_interfaces(system, args.verbose)
    print()
    _print_leases(store)
    return 0


def _run_mutation(args: argparse.Namespace) -> int:
    runner = SubprocessCommandRunner()
    system = WindowsSecondaryIpSystem(runner)
    store = JsonLeaseStore(args.lease_file)
    manager = SecondaryIpManager(system, store)

    if args.verbose:
        print(f"lease file: {store.path}")
        print(f"admin: {system.has_ip_mutation_permission()}")

    if args.add:
        ip = args.add
        try:
            validate_secondary_ipv4(ip)
        except ValueError as exc:
            print(f"ERROR: invalid IP: {exc}")
            return 1

        print(f"Plan: ADD {ip}/{args.prefix_length}")
        if args.interface_hint:
            print(f"      interface hint: {args.interface_hint}")

        if args.dry_run:
            print("DRY RUN: add not executed; pass --yes to mutate")
            return 0
        if not args.yes:
            print("DRY RUN: add not executed (--yes not provided)")
            return 0

        from secondary_ip_manager import SecondaryIpRequest
        result = manager.ensure_secondary_ip(
            SecondaryIpRequest(
                ip_address=ip,
                interface_hint=args.interface_hint,
                prefix_length=args.prefix_length,
            )
        )
        if result.ok:
            print(f"OK: bound {result.bind_host}")
            if result.already_present:
                print("    (IP was already present)")
        else:
            print(f"FALLBACK: {result.bind_host}  reason={result.reason}")
            if result.warning:
                print(f"         warning: {result.warning}")
            return 1
        return 0

    if args.remove:
        target = args.remove
        is_all = target.lower() == "all"

        if is_all:
            print("Plan: REMOVE all recorded leases")
        else:
            print(f"Plan: REMOVE {target}")

        if args.dry_run:
            print("DRY RUN: remove not executed; pass --yes to mutate")
            return 0
        if not args.yes:
            print("DRY RUN: remove not executed (--yes not provided)")
            return 0

        ip = None if is_all else target
        result = manager.remove_secondary_ip(ip)
        if not result.items:
            print("  (no leases recorded — nothing to remove)")
            return 0
        for item in result.items:
            status_icon = "OK" if item.status != "error" else "ERR"
            print(f"  [{status_icon}] {item.lease.ip_address} -> {item.status}")
            if item.error:
                print(f"        error: {item.error}")
                lease = item.lease
                print(f"        Manual recovery (Administrator PowerShell):")
                print(f"          Remove-NetIPAddress -InterfaceIndex {lease.interface_index} -IPAddress {lease.ip_address} -Confirm:$false -ErrorAction Stop")
                print(f"          # or via netsh:")
                print(f'          netsh interface ipv4 delete address name="{lease.interface_alias}" addr={lease.ip_address}')
        if result.ok:
            print(f"Done: {len(result.items)} lease(s) processed")
            return 0
        print("ERROR: some leases could not be removed — see recovery commands above")
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return _run_list(args)
    return _run_mutation(args)


if __name__ == "__main__":
    sys.exit(main())
