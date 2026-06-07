import ast
import json as _json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from secondary_ip_manager import (
    AdapterBindDecision,
    CommandResult,
    FakeCommandRunner,
    InMemoryLeaseStore,
    JsonLeaseStore,
    LeaseStore,
    NetworkInterface,
    SecondaryIpLease,
    SecondaryIpManager,
    SecondaryIpRequest,
    SecondaryIpSystem,
    SecondaryIpRecommendation,
    SecondaryIpStatus,
    WindowsSecondaryIpSystem,
    filter_eligible_interfaces,
    select_interface,
    validate_secondary_ipv4,
)


def iface(
    index=1,
    alias="Ethernet",
    addresses=("192.168.1.10",),
    metric=None,
    **kwargs,
):
    return NetworkInterface(
        interface_index=index,
        alias=alias,
        description=f"{alias} adapter",
        ipv4_addresses=addresses,
        metric=metric,
        **kwargs,
    )


class FakeSystem(SecondaryIpSystem):
    def __init__(
        self,
        *,
        admin=True,
        interfaces=None,
        addresses=None,
        add_error=None,
        remove_error=None,
        add_partially_succeeds=False,
        add_does_not_take_effect=False,
        displace_original_on_add=False,
    ):
        self.admin = admin
        self.interfaces = list(interfaces or [iface()])
        self.addresses = {
            key: list(value)
            for key, value in (addresses or {1: ["192.168.1.10"]}).items()
        }
        self.add_error = add_error
        self.remove_error = remove_error
        self.add_partially_succeeds = add_partially_succeeds
        self.add_does_not_take_effect = add_does_not_take_effect
        self.displace_original_on_add = displace_original_on_add
        self.add_calls = []
        self.remove_calls = []
        self.list_address_calls = []

    def has_ip_mutation_permission(self):
        return self.admin

    def list_interfaces(self):
        return list(self.interfaces)

    def list_interface_ipv4(self, interface_index):
        self.list_address_calls.append(interface_index)
        return list(self.addresses.get(interface_index, []))

    def add_ip(self, lease):
        self.add_calls.append(lease)
        if self.displace_original_on_add:
            self.addresses[lease.interface_index] = [lease.ip_address]
            return
        if self.add_partially_succeeds:
            self.addresses.setdefault(lease.interface_index, []).append(lease.ip_address)
        if self.add_error is not None:
            raise self.add_error
        if self.add_does_not_take_effect:
            return
        self.addresses.setdefault(lease.interface_index, []).append(lease.ip_address)

    def remove_ip(self, lease):
        self.remove_calls.append(lease)
        if self.remove_error is not None:
            raise self.remove_error
        current = self.addresses.setdefault(lease.interface_index, [])
        if lease.ip_address in current:
            current.remove(lease.ip_address)


def manager(system=None, store=None, now=lambda: 123.0):
    return SecondaryIpManager(system or FakeSystem(), store or InMemoryLeaseStore(), now)


class TestAdminPermission(unittest.TestCase):
    def test_non_admin_fallback_does_not_call_add(self):
        system = FakeSystem(admin=False)
        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.bind_host, "127.0.0.1")
        self.assertEqual(result.reason, "not_admin")
        self.assertIn("administrator", result.warning)
        self.assertEqual(system.add_calls, [])

    def test_permission_error_fallback(self):
        system = FakeSystem(add_error=PermissionError("denied"))
        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.reason, "permission_denied")
        self.assertEqual(result.bind_host, "127.0.0.1")


class TestInterfaceSelection(unittest.TestCase):
    def test_filter_excludes_loopback_down_virtual_tunnel_no_ipv4(self):
        interfaces = [
            iface(index=1, alias="down", is_up=False),
            iface(index=2, alias="loopback", is_loopback=True),
            iface(index=3, alias="virtual", is_virtual=True),
            iface(index=4, alias="tunnel", is_tunnel=True),
            iface(index=5, alias="no-ip", addresses=()),
            iface(index=6, alias="apipa", addresses=("169.254.1.10",)),
            iface(index=7, alias="wifi"),
        ]

        eligible = filter_eligible_interfaces(interfaces)

        self.assertEqual([item.interface_index for item in eligible], [7])

    def test_select_interface_by_index(self):
        selected = select_interface([iface(index=7, alias="Ethernet")], "7")

        self.assertEqual(selected.interface_index, 7)

    def test_select_interface_by_alias_case_insensitive(self):
        selected = select_interface([iface(index=7, alias="Ethernet 2")], "ethernet 2")

        self.assertEqual(selected.interface_index, 7)

    def test_unavailable_interface_hint_returns_none(self):
        selected = select_interface(
            [iface(index=7, alias="Ethernet", is_virtual=True)],
            "7",
        )

        self.assertIsNone(selected)

    def test_metric_sort_prefers_lower_metric(self):
        selected = select_interface(
            [
                iface(index=1, alias="slow", metric=50),
                iface(index=2, alias="fast", metric=10),
                iface(index=3, alias="unknown", metric=None),
            ],
            None,
        )

        self.assertEqual(selected.interface_index, 2)

    def test_default_route_is_preferred_over_lower_metric(self):
        selected = select_interface(
            [
                iface(index=1, alias="fast", metric=10),
                iface(index=2, alias="default", metric=50, default_route=True),
            ],
            None,
        )

        self.assertEqual(selected.interface_index, 2)

    def test_filter_excludes_vmware_bluetooth_teredo_and_deprecated(self):
        interfaces = [
            iface(index=1, alias="VMware Network Adapter VMnet8"),
            iface(index=2, alias="Bluetooth Network Connection"),
            iface(index=3, alias="Teredo Tunneling Pseudo-Interface"),
            iface(
                index=4,
                alias="Deprecated Ethernet",
                ipv4_address_states=("Deprecated",),
            ),
            iface(index=5, alias="Ethernet"),
        ]

        eligible = filter_eligible_interfaces(interfaces)

        self.assertEqual([item.interface_index for item in eligible], [5])


class TestSecondaryIpRecommendation(unittest.TestCase):
    def test_recommends_same_subnet_ip_on_default_route_interface(self):
        system = FakeSystem(
            interfaces=[
                iface(index=1, alias="Ethernet", addresses=("10.0.0.8",), metric=10),
                iface(
                    index=18,
                    alias="Wi-Fi",
                    addresses=("192.168.5.42",),
                    ipv4_prefix_lengths=(24,),
                    metric=50,
                    default_route=True,
                ),
            ],
            addresses={1: ["10.0.0.8"], 18: ["192.168.5.42"]},
        )

        recommendation = manager(system).recommend_secondary_ip()

        self.assertTrue(recommendation.available)
        self.assertTrue(recommendation.backend_admin)
        self.assertEqual(recommendation.interface_index, 18)
        self.assertEqual(recommendation.interface_alias, "Wi-Fi")
        self.assertEqual(recommendation.interface_ip, "192.168.5.42")
        self.assertEqual(recommendation.prefix_length, 24)
        self.assertEqual(recommendation.recommended_ip, "192.168.5.233")

    def test_recommendation_avoids_known_existing_ip(self):
        system = FakeSystem(
            interfaces=[
                iface(
                    index=18,
                    alias="Ethernet",
                    addresses=("192.168.5.42", "192.168.5.233"),
                    ipv4_prefix_lengths=(24, 24),
                ),
            ],
            addresses={18: ["192.168.5.42", "192.168.5.233"]},
        )

        recommendation = manager(system).recommend_secondary_ip()

        self.assertTrue(recommendation.available)
        self.assertEqual(recommendation.recommended_ip, "192.168.5.250")

    def test_recommendation_returns_no_eligible_interface_for_apipa_only(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Wi-Fi", addresses=("169.254.7.9",))],
            addresses={7: ["169.254.7.9"]},
        )

        recommendation = manager(system).recommend_secondary_ip()

        self.assertFalse(recommendation.available)
        self.assertEqual(recommendation.reason, "no_eligible_interface")


class TestIpValidation(unittest.TestCase):
    def test_validate_accepts_normal_ipv4(self):
        for value in ("192.168.1.250", "10.0.0.250", "172.16.1.250"):
            with self.subTest(value=value):
                self.assertEqual(validate_secondary_ipv4(value), value)

    def test_validate_rejects_invalid_loopback_multicast_link_local_unspecified_broadcast_ipv6_auto(self):
        invalid = [
            "",
            "not-an-ip",
            "127.0.0.2",
            "224.0.0.1",
            "169.254.1.1",
            "0.0.0.0",
            "255.255.255.255",
            "::1",
            "auto",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_secondary_ipv4(value)


class TestAddSecondaryIp(unittest.TestCase):
    def test_add_success_records_lease(self):
        store = InMemoryLeaseStore()
        result = manager(store=store).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.bind_host, "192.168.1.250")
        self.assertEqual(len(store.load()), 1)
        self.assertEqual(store.load()[0].ip_address, "192.168.1.250")
        self.assertEqual(store.load()[0].created_at, 123.0)

    def test_add_verifies_ip_is_present_before_recording_lease(self):
        system = FakeSystem(add_does_not_take_effect=True)
        store = InMemoryLeaseStore()

        result = manager(system, store).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "add_failed")
        self.assertIn("verification failed", result.warning)
        self.assertEqual(store.load(), [])
        self.assertGreaterEqual(system.list_address_calls.count(1), 2)

    def test_add_existing_ip_returns_error_and_does_not_record_lease(self):
        system = FakeSystem(addresses={1: ["192.168.1.10", "192.168.1.250"]})
        store = InMemoryLeaseStore()

        result = manager(system, store).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "ip_already_exists")
        self.assertIn("IP already exists", result.warning)
        self.assertEqual(system.add_calls, [])
        self.assertEqual(store.load(), [])

    def test_add_ip_existing_on_other_interface_is_rejected(self):
        system = FakeSystem(
            interfaces=[
                iface(index=1, alias="Ethernet", addresses=("192.168.1.10",)),
                iface(index=2, alias="Wi-Fi", addresses=("192.168.1.250",)),
            ],
            addresses={
                1: ["192.168.1.10"],
                2: ["192.168.1.250"],
            },
        )
        store = InMemoryLeaseStore()

        result = manager(system, store).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250", interface_hint="1")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "ip_already_exists")
        self.assertEqual(result.target_interface_index, 2)
        self.assertEqual(system.add_calls, [])
        self.assertEqual(store.load(), [])

    def test_add_no_eligible_interface_fallback(self):
        system = FakeSystem(interfaces=[iface(is_virtual=True)])
        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "no_eligible_interface")
        self.assertEqual(result.bind_host, "127.0.0.1")

    def test_apipa_only_interface_is_not_eligible(self):
        system = FakeSystem(
            interfaces=[iface(addresses=("169.254.4.20",))],
            addresses={1: ["169.254.4.20"]},
        )

        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "no_eligible_interface")
        self.assertEqual(system.add_calls, [])

    def test_subnet_mismatch_falls_back_without_add(self):
        system = FakeSystem(
            interfaces=[iface(addresses=("192.168.5.9",))],
            addresses={1: ["192.168.5.9"]},
        )

        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="10.0.0.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "subnet_mismatch")
        self.assertIn("not in the same subnet", result.warning)
        self.assertEqual(system.add_calls, [])

    def test_add_failure_returns_fallback_and_no_lease(self):
        system = FakeSystem(add_error=RuntimeError("boom"))
        store = InMemoryLeaseStore()
        result = manager(system, store).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "add_failed")
        self.assertIn("boom", result.warning)
        self.assertEqual(store.load(), [])

    def test_add_failure_best_effort_removes_partial_ip(self):
        system = FakeSystem(
            add_error=RuntimeError("partial"),
            add_partially_succeeds=True,
        )
        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "add_failed")
        self.assertEqual(len(system.remove_calls), 1)
        self.assertNotIn("192.168.1.250", system.addresses[1])

    def test_prefix_length_default_and_override(self):
        store = InMemoryLeaseStore()
        mgr = manager(store=store)

        default = mgr.ensure_secondary_ip(SecondaryIpRequest(ip_address="192.168.1.250"))
        override = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.12", prefix_length=28)
        )

        self.assertEqual(default.lease.prefix_length, 24)
        self.assertEqual(override.lease.prefix_length, 28)

    def test_prefix_length_invalid_fallback_or_error(self):
        system = FakeSystem()
        result = manager(system).ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250", prefix_length=33)
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_prefix_length")
        self.assertEqual(system.add_calls, [])

    def test_auto_returns_fallback_without_add(self):
        system = FakeSystem()
        result = manager(system).ensure_secondary_ip(SecondaryIpRequest(ip_address="auto"))

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "auto_not_implemented")
        self.assertEqual(system.add_calls, [])


class TestCleanup(unittest.TestCase):
    def lease(self, ip="192.168.1.250"):
        return SecondaryIpLease(
            interface_index=1,
            interface_alias="Ethernet",
            ip_address=ip,
            prefix_length=24,
            created_at=123.0,
        )

    def test_remove_success_removes_ip_and_lease(self):
        lease = self.lease()
        system = FakeSystem(addresses={1: ["192.168.1.10", "192.168.1.250"]})
        store = InMemoryLeaseStore([lease])

        result = manager(system, store).remove_secondary_ip()

        self.assertTrue(result.ok)
        self.assertEqual(result.items[0].status, "removed")
        self.assertEqual(store.load(), [])
        self.assertNotIn("192.168.1.250", system.addresses[1])

    def test_remove_absent_ip_is_idempotent_success(self):
        lease = self.lease()
        system = FakeSystem(addresses={1: ["192.168.1.10"]})
        store = InMemoryLeaseStore([lease])

        result = manager(system, store).remove_secondary_ip()

        self.assertTrue(result.ok)
        self.assertEqual(result.items[0].status, "already_absent")
        self.assertEqual(system.remove_calls, [])
        self.assertEqual(store.load(), [])

    def test_remove_unrecorded_ip_does_nothing(self):
        system = FakeSystem(addresses={1: ["192.168.1.10", "192.168.1.250"]})
        store = InMemoryLeaseStore()

        result = manager(system, store).remove_secondary_ip("192.168.1.250")

        self.assertTrue(result.ok)
        self.assertEqual(result.items, [])
        self.assertEqual(system.remove_calls, [])
        self.assertIn("192.168.1.250", system.addresses[1])

    def test_remove_failure_keeps_lease_and_reports_error(self):
        lease = self.lease()
        system = FakeSystem(
            addresses={1: ["192.168.1.10", "192.168.1.250"]},
            remove_error=RuntimeError("cannot remove"),
        )
        store = InMemoryLeaseStore([lease])

        result = manager(system, store).remove_secondary_ip()

        self.assertFalse(result.ok)
        self.assertEqual(result.items[0].status, "error")
        self.assertIn("cannot remove", result.items[0].error)
        self.assertEqual(store.load(), [lease])

    def test_repeated_add_remove_cycles_no_duplicate_leases(self):
        system = FakeSystem()
        store = InMemoryLeaseStore()
        mgr = manager(system, store)

        for _ in range(3):
            first = mgr.ensure_secondary_ip(
                SecondaryIpRequest(ip_address="192.168.1.250")
            )
            second = mgr.ensure_secondary_ip(
                SecondaryIpRequest(ip_address="192.168.1.250")
            )
            self.assertTrue(first.ok)
            self.assertFalse(second.ok)
            self.assertEqual(second.reason, "ip_already_exists")
            self.assertEqual(len(store.load()), 1)
            cleanup = mgr.remove_secondary_ip("192.168.1.250")
            self.assertTrue(cleanup.ok)
            self.assertEqual(store.load(), [])


class TestAdapterBindDecision(unittest.TestCase):
    def test_adapter_default_bind_host_preserved_without_secondary(self):
        decision = manager().choose_adapter_bind_host(
            None,
            default_bind_host="192.168.1.10",
        )

        self.assertEqual(
            decision,
            AdapterBindDecision(
                bind_host="192.168.1.10",
                secondary_ip_enabled=False,
                fallback_used=False,
                backend_admin=True,
            ),
        )

    def test_adapter_successful_secondary_ip_returns_ip(self):
        decision = manager().choose_adapter_bind_host("192.168.1.250")

        self.assertEqual(decision.bind_host, "192.168.1.250")
        self.assertTrue(decision.secondary_ip_enabled)
        self.assertFalse(decision.fallback_used)

    def test_adapter_secondary_ip_failure_falls_back_127001(self):
        decision = manager(FakeSystem(admin=False)).choose_adapter_bind_host(
            "192.168.1.250"
        )

        self.assertEqual(decision.bind_host, "127.0.0.1")
        self.assertFalse(decision.secondary_ip_enabled)
        self.assertTrue(decision.fallback_used)
        self.assertIn("administrator", decision.warning)


class TestSecurityBoundary(unittest.TestCase):
    def test_lease_data_contains_no_protocol_or_token_fields(self):
        lease = SecondaryIpLease(
            interface_index=1,
            interface_alias="Ethernet",
            ip_address="192.168.1.250",
            prefix_length=24,
            created_at=123.0,
        )

        data = asdict(lease)

        for key in ("relay_token", "room_id", "player_id", "session_id"):
            self.assertNotIn(key, data)

    def test_module_does_not_import_server_network_core_backend_flutter(self):
        text = self._module_text()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        for name in ("server", "network_core", "backend", "adapters", "flutter"):
            self.assertNotIn(name, imported)

    def test_no_protocol_message_strings_in_module(self):
        text = self._module_text()
        forbidden = [
            "CREATE_ROOM",
            "JOIN_ROOM",
            "ROOM_UPDATED",
            "RELAY_ENABLED",
            "relay_token",
            "player_id",
            "room_id",
            "REG\n",
            "RELAY\n",
        ]

        for value in forbidden:
            self.assertNotIn(value, text)

    @staticmethod
    def _module_text():
        root = Path(__file__).resolve().parent.parent
        return (root / "secondary_ip_manager.py").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# v0.3-I3: WindowsSecondaryIpSystem + FakeCommandRunner tests
# ══════════════════════════════════════════════════════════════════════════════

def _ok_result(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def _err_result(rc: int = 1, stderr: str = "error") -> CommandResult:
    return CommandResult(returncode=rc, stdout="", stderr=stderr)


_SAMPLE_INTERFACES_JSON = _json.dumps([
    {
        "interface_index": 12,
        "alias": "Ethernet",
        "description": "Intel(R) Ethernet Connection",
        "status": "Up",
        "hardware_interface": True,
        "interface_metric": 25,
        "ipv4_addresses": ["192.168.1.10"],
    },
    {
        "interface_index": 7,
        "alias": "Wi-Fi",
        "description": "Intel(R) Wi-Fi 6",
        "status": "Up",
        "hardware_interface": True,
        "interface_metric": 50,
        "ipv4_addresses": ["10.0.0.5"],
    },
    {
        "interface_index": 1,
        "alias": "Loopback Pseudo-Interface 1",
        "description": "Software Loopback Interface 1",
        "status": "Up",
        "hardware_interface": False,
        "interface_metric": 75,
        "ipv4_addresses": ["127.0.0.1"],
    },
])


def _win_system(
    responses: dict[str, CommandResult] | None = None,
    *,
    list_json: str = _SAMPLE_INTERFACES_JSON,
) -> WindowsSecondaryIpSystem:
    runner = FakeCommandRunner(responses)
    runner.set("Get-NetAdapter", _ok_result(list_json))
    return WindowsSecondaryIpSystem(runner)


class TestWindowsListInterfaces(unittest.TestCase):
    """PowerShell interface enumeration parsing tests."""

    def test_list_interfaces_parses_array_json(self) -> None:
        system = _win_system()
        interfaces = system.list_interfaces()
        self.assertEqual(len(interfaces), 3)
        eth = interfaces[0]
        self.assertEqual(eth.interface_index, 12)
        self.assertEqual(eth.alias, "Ethernet")
        self.assertEqual(eth.ipv4_addresses, ("192.168.1.10",))
        self.assertTrue(eth.is_up)
        self.assertTrue(eth.is_physical)
        self.assertFalse(eth.is_loopback)
        self.assertEqual(eth.metric, 25)

    def test_list_interfaces_parses_single_object_json(self) -> None:
        single = _json.dumps({
            "interface_index": 3,
            "alias": "Ethernet",
            "description": "eth",
            "status": "Up",
            "hardware_interface": True,
            "interface_metric": 10,
            "ipv4_addresses": ["172.16.0.1"],
        })
        system = _win_system(list_json=single)
        interfaces = system.list_interfaces()
        self.assertEqual(len(interfaces), 1)

    def test_list_interfaces_empty_output_returns_empty(self) -> None:
        system = _win_system(list_json="")
        interfaces = system.list_interfaces()
        self.assertEqual(interfaces, [])

    def test_list_interfaces_malformed_json_raises(self) -> None:
        system = _win_system(list_json="not json")
        with self.assertRaises(RuntimeError):
            system.list_interfaces()

    def test_list_interfaces_command_failure_raises(self) -> None:
        runner = FakeCommandRunner({"Get-NetAdapter": _err_result(1, "access denied")})
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError):
            system.list_interfaces()

    def test_list_interfaces_flags_virtual_tunnel_loopback(self) -> None:
        entries = _json.dumps([
            {
                "interface_index": 1, "alias": "Loopback",
                "description": "loopback", "status": "Up",
                "hardware_interface": False, "interface_metric": 75,
                "ipv4_addresses": [],
            },
            {
                "interface_index": 2, "alias": "Hyper-V Virtual",
                "description": "Hyper-V Virtual Ethernet Adapter", "status": "Up",
                "hardware_interface": True, "interface_metric": 20,
                "ipv4_addresses": ["192.168.1.1"],
            },
            {
                "interface_index": 3, "alias": "Tailscale",
                "description": "Tailscale Tunnel", "status": "Up",
                "hardware_interface": True, "interface_metric": 5,
                "ipv4_addresses": ["100.64.0.1"],
            },
        ])
        system = _win_system(list_json=entries)
        interfaces = system.list_interfaces()
        self.assertEqual(len(interfaces), 3)
        self.assertTrue(interfaces[0].is_loopback)
        self.assertTrue(interfaces[1].is_virtual)
        self.assertTrue(interfaces[2].is_tunnel)

    def test_list_interfaces_single_ipv4_string_becomes_tuple(self) -> None:
        entry = _json.dumps({
            "interface_index": 1, "alias": "eth", "description": "eth",
            "status": "Up", "hardware_interface": True,
            "interface_metric": 10,
            "ipv4_addresses": "10.0.0.1",
        })
        system = _win_system(list_json=entry)
        interfaces = system.list_interfaces()
        self.assertEqual(interfaces[0].ipv4_addresses, ("10.0.0.1",))


class TestWindowsListInterfaceIpv4(unittest.TestCase):
    """PowerShell per-interface IPv4 address enumeration tests."""

    def test_list_interface_ipv4_parses_addresses(self) -> None:
        runner = FakeCommandRunner({
            "Get-NetIPAddress": _ok_result(
                _json.dumps(["192.168.1.10", "192.168.1.250"])
            ),
        })
        system = WindowsSecondaryIpSystem(runner)
        addrs = system.list_interface_ipv4(12)
        self.assertEqual(addrs, ["192.168.1.10", "192.168.1.250"])

    def test_list_interface_ipv4_single_object(self) -> None:
        runner = FakeCommandRunner({
            "Get-NetIPAddress": _ok_result(_json.dumps("192.168.1.10")),
        })
        system = WindowsSecondaryIpSystem(runner)
        addrs = system.list_interface_ipv4(12)
        self.assertEqual(addrs, ["192.168.1.10"])

    def test_list_interface_ipv4_command_failure_raises(self) -> None:
        runner = FakeCommandRunner({
            "Get-NetIPAddress": _err_result(1, "failed"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError):
            system.list_interface_ipv4(12)

    def test_list_interface_ipv4_empty_output_returns_empty(self) -> None:
        runner = FakeCommandRunner({"Get-NetIPAddress": _ok_result("")})
        system = WindowsSecondaryIpSystem(runner)
        addrs = system.list_interface_ipv4(12)
        self.assertEqual(addrs, [])


class TestWindowsAddRemoveIp(unittest.TestCase):
    """WindowsSecondaryIpSystem add/remove IP command tests."""

    def _lease(self, ip: str = "192.168.1.250") -> SecondaryIpLease:
        return SecondaryIpLease(
            interface_index=12, interface_alias="Ethernet",
            ip_address=ip, prefix_length=24, created_at=123.0,
        )

    def test_add_ip_calls_new_netipaddress_command(self) -> None:
        runner = FakeCommandRunner({"New-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        lease = self._lease()
        system.add_ip(lease)
        self.assertEqual(len(runner.calls), 1)
        flat = " ".join(runner.calls[0][0])
        self.assertIn("New-NetIPAddress", flat)
        self.assertIn(str(lease.interface_index), flat)
        self.assertIn(lease.ip_address, flat)
        self.assertIn(str(lease.prefix_length), flat)
        self.assertIn("-SkipAsSource $true", flat)

    def test_remove_ip_calls_remove_netipaddress_command(self) -> None:
        runner = FakeCommandRunner({"Remove-NetIPAddress": _ok_result()})
        system = WindowsSecondaryIpSystem(runner)
        lease = self._lease()
        system.remove_ip(lease)
        self.assertEqual(len(runner.calls), 1)
        flat = " ".join(runner.calls[0][0])
        self.assertIn("Remove-NetIPAddress", flat)
        self.assertIn(str(lease.interface_index), flat)
        self.assertIn(lease.ip_address, flat)

    def test_add_permission_error_maps_permissionerror(self) -> None:
        runner = FakeCommandRunner({
            "New-NetIPAddress": _err_result(1, "access denied"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(PermissionError):
            system.add_ip(self._lease())

    def test_remove_permission_error_maps_permissionerror(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(1, "administrator privileges required"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(PermissionError):
            system.remove_ip(self._lease())

    def test_add_runtime_error_maps_runtimeerror(self) -> None:
        runner = FakeCommandRunner({
            "New-NetIPAddress": _err_result(1, "interface not found"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(RuntimeError):
            system.add_ip(self._lease())

    def test_add_privilege_keyword_maps_permissionerror(self) -> None:
        runner = FakeCommandRunner({
            "New-NetIPAddress": _err_result(5, "elevation required"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(PermissionError):
            system.add_ip(self._lease())

    def test_remove_denied_keyword_maps_permissionerror(self) -> None:
        runner = FakeCommandRunner({
            "Remove-NetIPAddress": _err_result(5, "run as administrator"),
        })
        system = WindowsSecondaryIpSystem(runner)
        with self.assertRaises(PermissionError):
            system.remove_ip(self._lease())


class TestFakeCommandRunner(unittest.TestCase):
    """FakeCommandRunner behavior tests."""

    def test_registered_response_is_returned(self) -> None:
        runner = FakeCommandRunner({"test": _ok_result("hello")})
        result = runner.run(["echo", "test"], timeout=1.0)
        self.assertEqual(result.stdout, "hello")
        self.assertEqual(result.returncode, 0)

    def test_unregistered_command_raises(self) -> None:
        runner = FakeCommandRunner()
        with self.assertRaises(RuntimeError):
            runner.run(["unknown-command"])

    def test_calls_are_recorded(self) -> None:
        runner = FakeCommandRunner({"cmd": _ok_result()})
        runner.run(["cmd", "a"])
        runner.run(["cmd", "b"])
        self.assertEqual(len(runner.calls), 2)


# ══════════════════════════════════════════════════════════════════════════════
# v0.3-I3: JsonLeaseStore tests
# ══════════════════════════════════════════════════════════════════════════════

def _lease(**kwargs) -> SecondaryIpLease:
    defaults = {
        "interface_index": 12, "interface_alias": "Ethernet",
        "ip_address": "192.168.1.250", "prefix_length": 24,
        "created_at": 123.0,
    }
    defaults.update(kwargs)
    return SecondaryIpLease(**defaults)


class TestJsonLeaseStore(unittest.TestCase):
    """JsonLeaseStore persistence tests."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="coopwing_test_leases_")
        self._path = Path(self._tmpdir) / "leases.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_missing_returns_empty(self) -> None:
        store = JsonLeaseStore(self._path)
        self.assertEqual(store.load(), [])

    def test_save_and_load_roundtrip(self) -> None:
        store = JsonLeaseStore(self._path)
        leases = [_lease(), _lease(ip_address="10.0.0.250")]
        store.save(leases)
        loaded = store.load()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].ip_address, "192.168.1.250")
        self.assertEqual(loaded[1].ip_address, "10.0.0.250")
        self.assertEqual(loaded[0].created_at, 123.0)

    def test_malformed_json_raises(self) -> None:
        self._path.write_text("not json", encoding="utf-8")
        store = JsonLeaseStore(self._path)
        with self.assertRaises(RuntimeError):
            store.load()

    def test_contains_no_protocol_or_token_fields(self) -> None:
        store = JsonLeaseStore(self._path)
        store.save([_lease()])
        text = self._path.read_text(encoding="utf-8")
        parsed = _json.loads(text)
        self.assertEqual(len(parsed), 1)
        data = parsed[0]
        expected = {
            "interface_index", "interface_alias", "ip_address",
            "prefix_length", "created_at", "owner",
        }
        self.assertEqual(set(data.keys()), expected)
        for forbidden in ("relay_token", "room_id", "player_id", "session_id"):
            self.assertNotIn(forbidden, data)

    def test_save_preserves_owner(self) -> None:
        store = JsonLeaseStore(self._path)
        store.save([_lease(owner="test_owner")])
        loaded = store.load()
        self.assertEqual(loaded[0].owner, "test_owner")

    def test_save_with_missing_parent_creates_directory(self) -> None:
        path = self._path.parent / "sub" / "leases.json"
        store = JsonLeaseStore(path)
        store.save([_lease()])
        self.assertTrue(path.exists())

    def test_save_atomic_does_not_truncate(self) -> None:
        store = JsonLeaseStore(self._path)
        leases = [_lease(ip_address=f"192.168.1.{i}") for i in range(1, 11)]
        store.save(leases)
        loaded = store.load()
        self.assertEqual(len(loaded), 10)


# ══════════════════════════════════════════════════════════════════════════════
# v0.3-I3: Updated security boundary tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSecondaryIpManagerBoundaryV3(unittest.TestCase):
    """Security boundary tests updated for v0.3-I3 module additions."""

    def test_lease_data_contains_no_protocol_or_token_fields(self) -> None:
        lease = SecondaryIpLease(
            interface_index=1,
            interface_alias="Ethernet",
            ip_address="192.168.1.250",
            prefix_length=24,
            created_at=123.0,
        )
        data = asdict(lease)
        for key in ("relay_token", "room_id", "player_id", "session_id"):
            self.assertNotIn(key, data)

    def test_module_does_not_import_server_network_core_backend_flutter(self) -> None:
        text = self._module_text()
        tree = ast.parse(text)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for name in ("server", "network_core", "backend", "adapters", "flutter"):
            self.assertNotIn(name, imported)

    def test_no_protocol_message_strings_in_module(self) -> None:
        text = self._module_text()
        forbidden = [
            "CREATE_ROOM",
            "JOIN_ROOM",
            "ROOM_UPDATED",
            "RELAY_ENABLED",
            "relay_token",
            "player_id",
            "room_id",
            "REG\n",
            "RELAY\n",
        ]
        for value in forbidden:
            self.assertNotIn(value, text, f"forbidden string '{value}' found in module")

    @staticmethod
    def _module_text() -> str:
        root = Path(__file__).resolve().parent.parent
        return (root / "secondary_ip_manager.py").read_text(encoding="utf-8")

    def test_windows_system_contains_powershell_cmdlets_not_protocol(self) -> None:
        text = self._module_text()
        # PowerShell cmdlets are allowed in the Windows path
        self.assertIn("New-NetIPAddress", text)
        self.assertIn("Remove-NetIPAddress", text)
        self.assertIn("Get-NetAdapter", text)
        # But no S2Pass protocol message strings
        self.assertNotIn("CREATE_ROOM", text)
        self.assertNotIn("RELAY_ENABLED", text)

    def test_fake_command_runner_is_importable(self) -> None:
        runner = FakeCommandRunner({"test": _ok_result()})
        result = runner.run(["test"])
        self.assertEqual(result.returncode, 0)
        self.assertFalse(hasattr(runner, "relay_token"))


class TestDhcpPreservation(unittest.TestCase):
    def test_add_preserves_dhcp_ip_succeeds(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Ethernet")],
            addresses={7: ["192.168.1.10"]},
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.bind_host, "192.168.1.250")
        self.assertIn("192.168.1.10", system.addresses[7])
        self.assertIn("192.168.1.250", system.addresses[7])
        leases = store.load()
        self.assertTrue(any(l.ip_address == "192.168.1.250" for l in leases))

    def test_add_displaces_dhcp_ip_fails_and_removes_secondary(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Ethernet")],
            addresses={7: ["192.168.1.10"]},
            displace_original_on_add=True,
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "dhcp_displaced")
        leases = store.load()
        self.assertFalse(any(l.ip_address == "192.168.1.250" for l in leases))

    def test_add_preserves_multiple_original_ips(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Ethernet")],
            addresses={7: ["192.168.1.10", "10.0.0.5"]},
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        result = mgr.ensure_secondary_ip(
            SecondaryIpRequest(ip_address="192.168.1.250")
        )
        self.assertTrue(result.ok)
        self.assertIn("192.168.1.10", system.addresses[7])
        self.assertIn("10.0.0.5", system.addresses[7])
        self.assertIn("192.168.1.250", system.addresses[7])


class TestAutoAllocateAndRelease(unittest.TestCase):
    def test_non_admin_does_not_allocate(self):
        system = FakeSystem(
            admin=False,
            interfaces=[iface(index=7, alias="Ethernet")],
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        status = mgr.auto_allocate_on_admin_startup()
        self.assertFalse(status.allocated)
        self.assertEqual(mgr.get_secondary_ip_status().source, "auto")

    def test_admin_allocates_when_recommendation_available(self):
        system = FakeSystem(
            admin=True,
            interfaces=[
                iface(
                    index=7,
                    alias="Ethernet",
                    addresses=("192.168.1.10",),
                    ipv4_prefix_lengths=(24,),
                    default_route=True,
                ),
            ],
            addresses={7: ["192.168.1.10"]},
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        status = mgr.auto_allocate_on_admin_startup()
        self.assertTrue(status.allocated)
        self.assertEqual(status.bind_mode, "secondary_ip")
        self.assertIsNotNone(status.allocated_ip)

    def test_auto_allocate_stores_allocation_for_release(self):
        system = FakeSystem(
            admin=True,
            interfaces=[
                iface(
                    index=7,
                    alias="Ethernet",
                    addresses=("192.168.1.10",),
                    ipv4_prefix_lengths=(24,),
                    default_route=True,
                ),
            ],
            addresses={7: ["192.168.1.10"]},
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        status = mgr.auto_allocate_on_admin_startup()
        self.assertTrue(status.allocated)
        allocated_ip = status.allocated_ip
        self.assertIsNotNone(allocated_ip)
        self.assertIn(allocated_ip, system.addresses[7])

        release_result = mgr.release_allocated_secondary_ip()
        self.assertTrue(release_result.ok)
        self.assertNotIn(allocated_ip, system.addresses[7])
        post_status = mgr.get_secondary_ip_status()
        self.assertFalse(post_status.allocated)
        self.assertIsNone(post_status.allocated_ip)

    def test_release_when_nothing_allocated_is_safe(self):
        system = FakeSystem(admin=True)
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        result = mgr.release_allocated_secondary_ip()
        self.assertTrue(result.ok)
        self.assertEqual(result.items, [])

    def test_allocate_failure_captures_last_error(self):
        system = FakeSystem(
            admin=True,
            interfaces=[
                iface(
                    index=7,
                    alias="Ethernet",
                    addresses=("192.168.1.10",),
                    ipv4_prefix_lengths=(24,),
                    default_route=True,
                ),
            ],
            addresses={7: ["192.168.1.10"]},
            add_error=RuntimeError("simulated failure"),
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        status = mgr.auto_allocate_on_admin_startup()
        self.assertFalse(status.allocated)
        self.assertIsNotNone(status.last_error)

    def test_status_shows_allocated_after_success(self):
        system = FakeSystem(
            admin=True,
            interfaces=[
                iface(
                    index=7,
                    alias="Ethernet",
                    addresses=("192.168.1.10",),
                    ipv4_prefix_lengths=(24,),
                    default_route=True,
                ),
            ],
            addresses={7: ["192.168.1.10"]},
        )
        store = InMemoryLeaseStore()
        mgr = SecondaryIpManager(system, store)
        mgr.auto_allocate_on_admin_startup()
        status = mgr.get_secondary_ip_status()
        self.assertTrue(status.allocated)
        self.assertIsNotNone(status.allocated_ip)
        self.assertEqual(status.bind_mode, "secondary_ip")
        self.assertEqual(status.source, "auto")
        self.assertEqual(status.interface_index, 7)


class TestStartupCleanupStaleLeases(unittest.TestCase):
    def test_cleans_leases_still_present_on_interface(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Ethernet")],
            addresses={7: ["192.168.1.10", "192.168.1.250"]},
        )
        store = InMemoryLeaseStore([
            SecondaryIpLease(
                interface_index=7,
                interface_alias="Ethernet",
                ip_address="192.168.1.250",
                prefix_length=24,
                created_at=100.0,
            ),
        ])
        mgr = SecondaryIpManager(system, store)
        result = mgr.startup_cleanup_stale_leases()
        self.assertTrue(result.ok)
        self.assertNotIn("192.168.1.250", system.addresses[7])

    def test_already_absent_lease_removed_from_store(self):
        system = FakeSystem(
            interfaces=[iface(index=7, alias="Ethernet")],
            addresses={7: ["192.168.1.10"]},
        )
        store = InMemoryLeaseStore([
            SecondaryIpLease(
                interface_index=7,
                interface_alias="Ethernet",
                ip_address="192.168.1.250",
                prefix_length=24,
                created_at=100.0,
            ),
        ])
        mgr = SecondaryIpManager(system, store)
        result = mgr.startup_cleanup_stale_leases()
        self.assertTrue(result.ok)
        remaining = store.load()
        self.assertFalse(any(l.ip_address == "192.168.1.250" for l in remaining))


if __name__ == "__main__":
    unittest.main()
