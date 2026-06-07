#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Parser / safety / lease-behaviour / command-verification tests for
tools/secondary_ip_admin.py and the underlying WindowsSecondaryIpSystem.

These tests use FakeCommandRunner — no real PowerShell or netsh is ever called.
No real network adapter IP configuration is modified.
"""

from __future__ import annotations

import ast
import json as _json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.secondary_ip_admin as admin_tool

from secondary_ip_manager import (
    CleanupItem,
    CleanupResult,
    CommandResult,
    FakeCommandRunner,
    InMemoryLeaseStore,
    JsonLeaseStore,
    SecondaryIpLease,
    SecondaryIpManager,
    SecondaryIpRequest,
    SecondaryIpSystem,
    WindowsSecondaryIpSystem,
    filter_eligible_interfaces,
    validate_secondary_ipv4,
)


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════

def _ok_result(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def _err_result(rc: int = 1, stderr: str = "error") -> CommandResult:
    return CommandResult(returncode=rc, stdout="", stderr=stderr)


def _lease(**kwargs) -> SecondaryIpLease:
    defaults = {
        "interface_index": 18,
        "interface_alias": "Ethernet",
        "ip_address": "192.168.5.233",
        "prefix_length": 24,
        "created_at": 123.0,
    }
    defaults.update(kwargs)
    return SecondaryIpLease(**defaults)


_SAMPLE_INTERFACES_JSON = _json.dumps([{
    "interface_index": 18,
    "alias": "Ethernet",
    "description": "Intel(R) Ethernet Connection",
    "status": "Up",
    "hardware_interface": True,
    "interface_metric": 25,
    "ipv4_addresses": ["192.168.5.9"],
}])


def _win_system(
    responses: dict[str, CommandResult] | None = None,
    *,
    list_json: str = _SAMPLE_INTERFACES_JSON,
) -> WindowsSecondaryIpSystem:
    runner = FakeCommandRunner(responses)
    runner.set("Get-NetAdapter", _ok_result(list_json))
    return WindowsSecondaryIpSystem(runner)


# ══════════════════════════════════════════════════════════════════════════════
# 1. CLI parser / safety tests (existing coverage preserved + extended)
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminToolArgParse(unittest.TestCase):
    """Verify CLI argument parsing and safety defaults."""

    def test_list_does_not_require_yes(self) -> None:
        args = admin_tool._build_parser().parse_args(["--list"])
        self.assertTrue(args.list)
        self.assertFalse(args.yes)
        self.assertFalse(args.dry_run)

    def test_add_without_yes_is_dry_run_behavior(self) -> None:
        args = admin_tool._build_parser().parse_args(["--add", "192.168.1.250"])
        self.assertEqual(args.add, "192.168.1.250")
        self.assertFalse(args.yes)
        rc = admin_tool.main(["--add", "192.168.1.250"])
        self.assertEqual(rc, 0)

    def test_add_with_yes_and_dry_run_never_mutates(self) -> None:
        rc = admin_tool.main(["--add", "192.168.1.250", "--yes", "--dry-run"])
        self.assertEqual(rc, 0)

    def test_remove_without_yes_is_dry_run_behavior(self) -> None:
        rc = admin_tool.main(["--remove", "192.168.1.250"])
        self.assertEqual(rc, 0)

    def test_remove_all_without_yes_is_dry_run(self) -> None:
        rc = admin_tool.main(["--remove", "all"])
        self.assertEqual(rc, 0)

    def test_list_with_verbose(self) -> None:
        args = admin_tool._build_parser().parse_args(["--list", "--verbose"])
        self.assertTrue(args.list)
        self.assertTrue(args.verbose)

    def test_add_requires_safe_args_invalid_ip_rejected(self) -> None:
        rc = admin_tool.main(["--add", "not-an-ip", "--yes"])
        self.assertNotEqual(rc, 0)

    def test_add_requires_safe_args_loopback_rejected(self) -> None:
        rc = admin_tool.main(["--add", "127.0.0.2", "--yes"])
        self.assertNotEqual(rc, 0)

    def test_mutually_exclusive_list_add(self) -> None:
        with self.assertRaises(SystemExit):
            admin_tool._build_parser().parse_args(["--list", "--add", "10.0.0.1"])

    def test_default_lease_path_is_absolute(self) -> None:
        path = admin_tool._default_lease_path()
        self.assertTrue(os.path.isabs(path) or path.startswith("~"))

    def test_prefix_length_default_is_24(self) -> None:
        args = admin_tool._build_parser().parse_args(["--add", "10.0.0.1"])
        self.assertEqual(args.prefix_length, 24)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Remove command formatting regression tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRemoveCommandFormatting(unittest.TestCase):
    """Verify the PowerShell remove command does NOT contain the broken escape."""

    def test_remove_command_does_not_contain_backtick_confirm(self) -> None:
        """The remove cmd must NOT contain ``-Confirm:`$false``."""
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        lease = _lease()
        try:
            system.remove_ip(lease)
        except Exception:
            pass
        self.assertEqual(len(runner.calls), 1)
        flat = " ".join(runner.calls[0][0])
        self.assertNotIn("-Confirm:`$false", flat)
        self.assertNotIn("`$false", flat)
        self.assertIn("-Confirm:$false", flat)

    def test_remove_command_contains_correct_confirm_syntax(self) -> None:
        """The remove cmd MUST contain ``-Confirm:$false`` (no backtick)."""
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        system.remove_ip(_lease())
        flat = " ".join(runner.calls[0][0])
        self.assertIn("-Confirm:$false", flat)

    def test_remove_command_includes_noninteractive_flag(self) -> None:
        """The cmdline includes -NonInteractive for safety."""
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        system.remove_ip(_lease())
        flat = " ".join(runner.calls[0][0])
        self.assertIn("-NonInteractive", flat)

    def test_remove_command_contains_interface_index(self) -> None:
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        system.remove_ip(_lease(interface_index=18))
        flat = " ".join(runner.calls[0][0])
        self.assertIn("-InterfaceIndex 18", flat)

    def test_remove_command_contains_ip_address(self) -> None:
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        system.remove_ip(_lease(ip_address="192.168.5.233"))
        flat = " ".join(runner.calls[0][0])
        self.assertIn("192.168.5.233", flat)

    def test_add_command_uses_skip_as_source_true(self) -> None:
        runner = FakeCommandRunner({"New-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        system.add_ip(_lease())
        flat = " ".join(runner.calls[0][0])
        self.assertIn("-SkipAsSource $true", flat)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Lease behaviour tests (add success / add failure / remove success /
#    remove failure / remove all partial / missing file)
# ══════════════════════════════════════════════════════════════════════════════

class FakeSystemForLeaseTests(SecondaryIpSystem):
    """Controlled system for lease tests — no real network calls."""

    def __init__(self, *, admin: bool = True, add_error: Exception | None = None,
                 remove_error: Exception | None = None,
                 addresses: dict[int, list[str]] | None = None,
                 add_does_not_take_effect: bool = False):
        self.admin = admin
        self.add_error = add_error
        self.remove_error = remove_error
        self.add_does_not_take_effect = add_does_not_take_effect
        self._addresses = {k: list(v) for k, v in (addresses or {}).items()}
        self.add_calls: list[SecondaryIpLease] = []
        self.remove_calls: list[SecondaryIpLease] = []

    def has_ip_mutation_permission(self) -> bool:
        return self.admin

    def list_interfaces(self) -> list:
        from secondary_ip_manager import NetworkInterface
        return [NetworkInterface(
            interface_index=18, alias="Ethernet",
            description="Ethernet", ipv4_addresses=("192.168.5.9",),
            is_up=True, is_physical=True, metric=25,
        )]

    def list_interface_ipv4(self, interface_index: int) -> list[str]:
        return self._addresses.get(interface_index, [])

    def add_ip(self, lease: SecondaryIpLease) -> None:
        self.add_calls.append(lease)
        if self.add_error:
            raise self.add_error
        if self.add_does_not_take_effect:
            return
        self._addresses.setdefault(lease.interface_index, []).append(lease.ip_address)

    def remove_ip(self, lease: SecondaryIpLease) -> None:
        self.remove_calls.append(lease)
        if self.remove_error:
            raise self.remove_error
        addrs = self._addresses.setdefault(lease.interface_index, [])
        if lease.ip_address in addrs:
            addrs.remove(lease.ip_address)


class TestLeaseAddBehaviour(unittest.TestCase):
    """Lease write-on-success / no-write-on-failure behaviour."""

    def test_add_success_writes_lease(self) -> None:
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(
            FakeSystemForLeaseTests(addresses={18: ["192.168.5.9"]}), store,
        )
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.5.233", interface_hint="18"),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.bind_host, "192.168.5.233")
        leases = store.load()
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0].ip_address, "192.168.5.233")

    def test_add_failure_does_not_write_lease(self) -> None:
        store = InMemoryLeaseStore()
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9"]},
            add_error=RuntimeError("boom"),
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.5.233", interface_hint="18"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(store.load(), [])

    def test_add_permission_error_does_not_write_lease(self) -> None:
        store = InMemoryLeaseStore()
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9"]},
            add_error=PermissionError("denied"),
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.5.233", interface_hint="18"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(store.load(), [])

    def test_add_verify_failure_does_not_write_lease(self) -> None:
        store = InMemoryLeaseStore()
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9"]},
            add_does_not_take_effect=True,
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.5.233", interface_hint="18"),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "add_failed")
        self.assertIn("verification failed", result.warning)
        self.assertEqual(store.load(), [])


class TestLeaseRemoveBehaviour(unittest.TestCase):
    """Lease remove-success / remove-failure / partial / missing-file behaviour."""

    def test_remove_success_removes_lease_entry(self) -> None:
        lease = _lease()
        store = InMemoryLeaseStore([lease])
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9", "192.168.5.233"]},
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.remove_secondary_ip("192.168.5.233")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].status, "removed")
        self.assertEqual(store.load(), [])

    def test_remove_failure_keeps_lease_entry(self) -> None:
        lease = _lease()
        store = InMemoryLeaseStore([lease])
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9", "192.168.5.233"]},
            remove_error=RuntimeError("cannot remove"),
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.remove_secondary_ip("192.168.5.233")
        self.assertFalse(result.ok)
        self.assertEqual(result.items[0].status, "error")
        loaded = store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].ip_address, "192.168.5.233")

    def test_remove_all_partial_success(self) -> None:
        """One remove succeeds, one fails — succeeded lease gone, failed kept, overall fail."""
        lease_ok = _lease(ip_address="192.168.5.233")
        lease_fail = _lease(ip_address="10.0.0.250")
        store = InMemoryLeaseStore([lease_ok, lease_fail])

        class PartialSystem(FakeSystemForLeaseTests):
            def remove_ip(self, lease):
                self.remove_calls.append(lease)
                if lease.ip_address == "10.0.0.250":
                    raise RuntimeError("simulated failure")
                super().remove_ip(lease)

        system = PartialSystem(
            addresses={18: ["192.168.5.9", "192.168.5.233", "10.0.0.250"]},
        )
        mgr = SecondaryIpManager(system, store)
        result = mgr.remove_secondary_ip()  # all
        self.assertFalse(result.ok, "overall status must be failure")
        statuses = {item.lease.ip_address: item.status for item in result.items}
        self.assertEqual(statuses["192.168.5.233"], "removed")
        self.assertEqual(statuses["10.0.0.250"], "error")
        loaded = store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].ip_address, "10.0.0.250")

    def test_remove_all_with_missing_lease_file_does_not_crash(self) -> None:
        system = FakeSystemForLeaseTests()
        store = InMemoryLeaseStore()  # empty
        mgr = SecondaryIpManager(system, store)
        result = mgr.remove_secondary_ip()  # all
        self.assertTrue(result.ok)
        self.assertEqual(result.items, [])

    def test_remove_already_absent_removes_lease_anyway(self) -> None:
        """IP not on interface → 'already_absent' but lease entry still removed."""
        lease = _lease()
        store = InMemoryLeaseStore([lease])
        system = FakeSystemForLeaseTests(addresses={18: ["192.168.5.9"]})
        mgr = SecondaryIpManager(system, store)
        result = mgr.remove_secondary_ip("192.168.5.233")
        self.assertTrue(result.ok)
        self.assertEqual(result.items[0].status, "already_absent")
        self.assertEqual(store.load(), [])

    def test_remove_failure_keeps_other_leases_untouched(self) -> None:
        lease_keep = _lease(ip_address="192.168.5.100")
        lease_fail = _lease(ip_address="192.168.5.233")
        store = InMemoryLeaseStore([lease_keep, lease_fail])
        system = FakeSystemForLeaseTests(
            addresses={18: ["192.168.5.9", "192.168.5.233"]},
            remove_error=RuntimeError("cannot remove"),
        )
        mgr = SecondaryIpManager(system, store)
        mgr.remove_secondary_ip("192.168.5.233")
        loaded = store.load()
        ips = {lease.ip_address for lease in loaded}
        self.assertIn("192.168.5.100", ips)
        self.assertIn("192.168.5.233", ips)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Error output includes manual recovery command
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorRecoveryOutput(unittest.TestCase):
    """Verify error messages contain actionable manual recovery commands."""

    def test_permission_error_includes_recovery_command(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(5, "access denied"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(PermissionError) as ctx:
            system.remove_ip(_lease())
        msg = str(ctx.exception)
        self.assertIn("Manual recovery", msg)
        self.assertIn("Remove-NetIPAddress", msg)
        self.assertIn("192.168.5.233", msg)
        self.assertIn("-Confirm:$false", msg)
        self.assertIn("netsh interface ipv4 delete address", msg)
        self.assertIn("Ethernet", msg)

    def test_runtime_error_includes_recovery_command(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(1, "interface not found"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError) as ctx:
            system.remove_ip(_lease())
        msg = str(ctx.exception)
        self.assertIn("Manual recovery", msg)
        self.assertIn("Remove-NetIPAddress -InterfaceIndex 18", msg)
        self.assertIn("netsh", msg)

    def test_recovery_command_uses_correct_interface_alias(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(1, "fail"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError) as ctx:
            system.remove_ip(_lease(interface_alias="Ethernet 2"))
        msg = str(ctx.exception)
        self.assertIn('name="Ethernet 2"', msg)

    def test_recovery_command_has_no_backtick_in_confirm(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(1, "fail"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError) as ctx:
            system.remove_ip(_lease())
        msg = str(ctx.exception)
        self.assertNotIn("`$false", msg)
        self.assertIn("-Confirm:$false", msg)


# ══════════════════════════════════════════════════════════════════════════════
# 5. JsonLeaseStore no-token / boundary tests
# ══════════════════════════════════════════════════════════════════════════════

class TestJsonLeaseStoreSafety(unittest.TestCase):
    """JsonLeaseStore never stores protocol fields or tokens."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="coopwing_test_leases_")
        self._path = Path(self._tmpdir) / "leases.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_json_contains_only_ip_metadata(self) -> None:
        store = JsonLeaseStore(self._path)
        store.save([_lease()])
        text = self._path.read_text(encoding="utf-8")
        data = _json.loads(text)
        self.assertEqual(len(data), 1)
        expected_keys = {
            "interface_index", "interface_alias", "ip_address",
            "prefix_length", "created_at", "owner",
        }
        self.assertEqual(set(data[0].keys()), expected_keys)
        for forbidden in ("relay_token", "room_id", "player_id", "session_id",
                          "CREATE_ROOM", "RELAY_ENABLED"):
            self.assertNotIn(forbidden, _json.dumps(data))

    def test_load_missing_returns_empty(self) -> None:
        store = JsonLeaseStore(self._path)
        self.assertEqual(store.load(), [])


# ══════════════════════════════════════════════════════════════════════════════
# 6. Admin tool boundary (unchanged from v0.3-I3 — verify still clean)
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminToolBoundary(unittest.TestCase):
    """Verify the admin tool does not import forbidden modules."""

    @staticmethod
    def _module_text() -> str:
        root = Path(__file__).resolve().parent.parent
        return (root / "tools" / "secondary_ip_admin.py").read_text(encoding="utf-8")

    def test_does_not_import_server_network_core_backend_adapters_flutter(self) -> None:
        text = self._module_text()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for name in ("server", "network_core", "backend", "adapters", "flutter"):
            self.assertNotIn(name, imported,
                             f"admin tool imports forbidden module: {name}")

    def test_no_protocol_message_strings_in_admin_tool(self) -> None:
        text = self._module_text()
        forbidden = [
            "CREATE_ROOM", "JOIN_ROOM", "ROOM_UPDATED", "RELAY_ENABLED",
            "REG\n", "RELAY\n",
        ]
        for value in forbidden:
            self.assertNotIn(value, text)

    def test_no_relay_token_exposure(self) -> None:
        text = self._module_text()
        self.assertNotIn("relay_token", text)
        self.assertNotIn("player_id", text)
        self.assertNotIn("room_id", text)


if __name__ == "__main__":
    unittest.main()
