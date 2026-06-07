# -*- coding: utf-8 -*-
"""Tests for backend.process_port_detector — all using FakeCommandRunner."""

import json as _json
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.process_port_detector import (
    CommandResult,
    FakeCommandRunner,
    PortCandidate,
    ProcessPortDetectionError,
    ProcessPortDetector,
    ProcessPortScanResult,
    ScanResult,
    _is_noise_process,
)


def _ok_result(stdout: str = "") -> CommandResult:
    return CommandResult(0, stdout, "")


def _err_result(rc: int = 1, stderr: str = "error") -> CommandResult:
    return CommandResult(rc, "", stderr)


_TCP_LISTEN_GAME = _json.dumps([{
    "local_address": "0.0.0.0",
    "local_port": 27015,
    "remote_address": "0.0.0.0",
    "remote_port": 0,
    "state": "Listen",
    "process_id": 12345,
    "process_name": "hl2",
}])

_TCP_LISTEN_LOOPBACK = _json.dumps([{
    "local_address": "127.0.0.1",
    "local_port": 27016,
    "remote_address": "0.0.0.0",
    "remote_port": 0,
    "state": "Listen",
    "process_id": 12346,
    "process_name": "mygame",
}])

_TCP_NOISE_STEAM = _json.dumps([{
    "local_address": "0.0.0.0",
    "local_port": 27036,
    "remote_address": "0.0.0.0",
    "remote_port": 0,
    "state": "Listen",
    "process_id": 5555,
    "process_name": "Steam",
}])

_TCP_ESTABLISHED = _json.dumps([{
    "local_address": "192.168.1.10",
    "local_port": 52000,
    "remote_address": "120.27.210.184",
    "remote_port": 9000,
    "state": "Established",
    "process_id": 12345,
    "process_name": "hl2",
}])

_TCP_HTTPS_NOISE = _json.dumps([{
    "local_address": "0.0.0.0",
    "local_port": 443,
    "remote_address": "0.0.0.0",
    "remote_port": 0,
    "state": "Listen",
    "process_id": 8888,
    "process_name": "webserver",
}])

_UDP_GAME = _json.dumps([{
    "local_address": "0.0.0.0",
    "local_port": 27015,
    "process_id": 12345,
    "process_name": "hl2",
}])

_UDP_LOOPBACK = _json.dumps([{
    "local_address": "127.0.0.1",
    "local_port": 12345,
    "process_id": 12346,
    "process_name": "mygame",
}])

_UDP_NOISE_DISCORD = _json.dumps([{
    "local_address": "0.0.0.0",
    "local_port": 50000,
    "process_id": 9999,
    "process_name": "Discord",
}])

_NETSTAT_TCP = """\
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:27015          0.0.0.0:0              LISTENING       12345
  TCP    [::1]:27016            [::1]:51000            ESTABLISHED     12345
  TCP    0.0.0.0:27017          0.0.0.0:0              LISTENING       54321
"""

_NETSTAT_UDP = """\
Active Connections

  Proto  Local Address          Foreign Address        PID
  UDP    0.0.0.0:27015          *:*                    12345
  UDP    [::]:27016             *:*                    12345
  UDP    0.0.0.0:27017          *:*                    54321
"""


def _detector(responses: dict[str, CommandResult] | None = None) -> ProcessPortDetector:
    defaults = {
        "Get-NetTCPConnection": _ok_result("[]"),
        "Get-NetUDPEndpoint": _ok_result("[]"),
        "Get-Process": _ok_result(
            _json.dumps({"Id": 12345, "ProcessName": "hl2"})
        ),
    }
    if responses:
        defaults.update(responses)
    runner = FakeCommandRunner(defaults)
    return ProcessPortDetector(runner)


class TestNoiseFilter(unittest.TestCase):
    def test_steam_is_noise(self) -> None:
        self.assertTrue(_is_noise_process("Steam"))
        self.assertTrue(_is_noise_process("steamwebhelper"))
        self.assertTrue(_is_noise_process("SteamService"))

    def test_chrome_is_noise(self) -> None:
        self.assertTrue(_is_noise_process("chrome"))
        self.assertTrue(_is_noise_process("msedge"))

    def test_game_not_noise(self) -> None:
        self.assertFalse(_is_noise_process("hl2"))
        self.assertFalse(_is_noise_process("mygame"))


class TestTcpClassification(unittest.TestCase):
    def test_tcp_listen_high_confidence(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_LISTEN_GAME)})
        result = detector.scan(process_name="hl2")
        self.assertEqual(len(result.candidates), 1)
        c = result.candidates[0]
        self.assertEqual(c.protocol, "tcp")
        self.assertEqual(c.port, 27015)
        self.assertEqual(c.confidence, "high")
        self.assertIn("LISTEN", c.reason)

    def test_tcp_listen_loopback_medium(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_LISTEN_LOOPBACK)})
        result = detector.scan(process_name="mygame")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].confidence, "medium")
        self.assertIn("loopback", result.candidates[0].reason)

    def test_steam_noise_is_low(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_NOISE_STEAM)})
        result = detector.scan(process_name="Steam")
        self.assertEqual(result.candidates, [])  # low excluded by default

    def test_steam_noise_included_when_include_low(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_NOISE_STEAM)})
        result = detector.scan(process_name="Steam", include_low_confidence=True)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].confidence, "low")

    def test_https_noise_port_is_low(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_HTTPS_NOISE)})
        result = detector.scan(process_name="webserver")
        self.assertEqual(result.candidates, [])

    def test_tcp_established_non_loopback_is_low(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_ESTABLISHED)})
        result = detector.scan(process_name="hl2", include_low_confidence=True)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].confidence, "low")


class TestUdpClassification(unittest.TestCase):
    def test_udp_bound_high_confidence(self) -> None:
        detector = _detector({"Get-NetUDPEndpoint": _ok_result(_UDP_GAME)})
        result = detector.scan(process_name="hl2")
        self.assertEqual(len(result.candidates), 1)
        c = result.candidates[0]
        self.assertEqual(c.protocol, "udp")
        self.assertEqual(c.port, 27015)
        self.assertEqual(c.confidence, "high")

    def test_udp_loopback_medium(self) -> None:
        detector = _detector({"Get-NetUDPEndpoint": _ok_result(_UDP_LOOPBACK)})
        result = detector.scan(process_name="mygame")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].confidence, "medium")

    def test_discord_noise_low_excluded(self) -> None:
        detector = _detector({"Get-NetUDPEndpoint": _ok_result(_UDP_NOISE_DISCORD)})
        result = detector.scan(process_name="Discord")
        self.assertEqual(result.candidates, [])


class TestEdgeCases(unittest.TestCase):
    def test_empty_output_returns_empty(self) -> None:
        detector = _detector()
        result = detector.scan(process_name="any")
        self.assertEqual(result.candidates, [])

    def test_command_failure_raises(self) -> None:
        detector = _detector({
            "Get-NetTCPConnection": _err_result(1, "access denied"),
            "Get-NetUDPEndpoint": _err_result(1, "access denied"),
        })
        with self.assertRaises(RuntimeError):
            detector.scan(process_name="any")

    def test_malformed_json_raises(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result("not json")})
        with self.assertRaises(RuntimeError):
            detector.scan(process_name="any")

    def test_netstat_fallback_parses_tcp_for_requested_pid(self) -> None:
        detector = _detector({
            "Get-NetTCPConnection": _err_result(1, "access denied"),
            "netstat -ano -p tcp": _ok_result(_NETSTAT_TCP),
        })

        result = detector.scan(
            process_name="hl2",
            process_id=12345,
            include_low_confidence=True,
        )

        self.assertEqual(
            [(candidate.port, candidate.state) for candidate in result.candidates],
            [(27015, "Listen"), (27016, "Established")],
        )
        self.assertEqual(result.candidates[1].local_address, "::1")
        self.assertEqual(result.candidates[1].remote_port, 51000)

    def test_netstat_fallback_parses_udp_for_requested_pid(self) -> None:
        detector = _detector({
            "Get-NetUDPEndpoint": _err_result(1, "access denied"),
            "netstat -ano -p udp": _ok_result(_NETSTAT_UDP),
        })

        result = detector.scan(process_name="hl2", process_id=12345)

        self.assertEqual(
            [(candidate.protocol, candidate.port) for candidate in result.candidates],
            [("udp", 27015), ("udp", 27016)],
        )
        self.assertEqual(result.candidates[1].local_address, "::")

    def test_single_object_parsed(self) -> None:
        single = _json.dumps({"local_address": "0.0.0.0", "local_port": 1234,
                              "remote_address": "0.0.0.0", "remote_port": 0,
                              "state": "Listen", "process_id": 1, "process_name": "game"})
        detector = _detector({"Get-NetTCPConnection": _ok_result(single)})
        result = detector.scan(process_name="game")
        self.assertEqual(len(result.candidates), 1)

    def test_filter_by_process_id(self) -> None:
        mixed = _json.dumps([
            {"local_address": "0.0.0.0", "local_port": 1000, "remote_address": "0.0.0.0",
             "remote_port": 0, "state": "Listen", "process_id": 100, "process_name": "a"},
            {"local_address": "0.0.0.0", "local_port": 2000, "remote_address": "0.0.0.0",
             "remote_port": 0, "state": "Listen", "process_id": 200, "process_name": "b"},
        ])
        detector = _detector({"Get-NetTCPConnection": _ok_result(mixed)})
        result = detector.scan(process_id=100)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].port, 1000)

    def test_udp_single_object_parsed(self) -> None:
        single = _json.dumps({"local_address": "0.0.0.0", "local_port": 5555,
                              "process_id": 1, "process_name": "game"})
        detector = _detector({"Get-NetUDPEndpoint": _ok_result(single)})
        result = detector.scan(process_name="game")
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].port, 5555)

    def test_scan_result_to_dict(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_LISTEN_GAME)})
        result = detector.scan(process_name="hl2", stage="lobby")
        d = result.to_dict()
        self.assertEqual(d["stage"], "lobby")
        self.assertEqual(len(d["candidates"]), 1)
        self.assertIn("port", d["candidates"][0])

    def test_no_process_name_includes_all_filtered_tcp(self) -> None:
        detector = _detector({"Get-NetTCPConnection": _ok_result(_TCP_LISTEN_GAME)})
        result = detector.scan()  # no filter
        self.assertEqual(len(result.candidates), 1)  # game port, not noise

    def test_port_candidate_from_dict_roundtrip(self) -> None:
        c = PortCandidate("tcp", 1234, 100, "game", "0.0.0.0", None, "Listen", "high", "test")
        d = c.to_dict()
        c2 = PortCandidate.from_dict(d)
        self.assertEqual(c2.protocol, "tcp")
        self.assertEqual(c2.port, 1234)
        self.assertEqual(c2.confidence, "high")


class TestPidScan(unittest.TestCase):
    def test_scan_pid_returns_structured_tcp_and_udp_candidates(self) -> None:
        detector = _detector({
            "Get-NetTCPConnection": _ok_result(_TCP_LISTEN_GAME),
            "Get-NetUDPEndpoint": _ok_result(_UDP_GAME),
        })

        result = detector.scan_pid(12345)

        self.assertIsInstance(result, ProcessPortScanResult)
        self.assertEqual(result.pid, 12345)
        self.assertEqual(
            [(candidate.protocol, candidate.local_port) for candidate in result.candidates],
            [("tcp", 27015), ("udp", 27015)],
        )
        payload = result.to_dict()
        self.assertEqual(payload["pid"], 12345)
        self.assertEqual(payload["candidates"][0]["local_address"], "0.0.0.0")
        self.assertTrue(payload["candidates"][0]["reason"])

    def test_scan_pid_rejects_non_positive_pid_cleanly(self) -> None:
        detector = _detector()

        with self.assertRaises(ProcessPortDetectionError) as ctx:
            detector.scan_pid(0)

        self.assertEqual(ctx.exception.code, "INVALID_PID")

    def test_scan_pid_rejects_missing_process_cleanly(self) -> None:
        detector = _detector({"Get-Process": _ok_result("")})

        with self.assertRaises(ProcessPortDetectionError) as ctx:
            detector.scan_pid(99999)

        self.assertEqual(ctx.exception.code, "INVALID_PID")


if __name__ == "__main__":
    unittest.main()
