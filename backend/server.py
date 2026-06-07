# -*- coding: utf-8 -*-
"""S2Pass Backend — stdlib HTTP server.

Binds 127.0.0.1 only. Exposes relay smoke session control endpoints.
Does not import network_core or adapters.

Manual real-core backend mode:
  S2PASS_BACKEND_RUNNER=real_core python -m backend.server
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from backend.game_profiles import (
    ConfirmPortsRequest,
    CreateGameRequest,
    GameProfileStore,
)
from backend.lan_discovery import LanDiscovery, LanDiscoveryConfig
from backend.models import (
    BackendError,
    LanDiscoveryPeersResponse,
    LanDiscoveryStatusResponse,
    LanPeerDto,
)
from backend.process_port_detector import (
    ProcessPortDetectionError,
    ProcessPortDetector,
    SubprocessCommandRunner as ProcessPortSubprocessCommandRunner,
)
from backend.session_manager import SessionManager
from secondary_ip_manager import (
    InMemoryLeaseStore,
    JsonLeaseStore,
    SecondaryIpManager,
    SecondaryIpSystem,
    SubprocessCommandRunner,
    WindowsSecondaryIpSystem,
)

_START_TIME = time.time()
_VERSION = "0.4.0"


def _make_error_dict(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        d["details"] = details
    return d


def _default_instance_name() -> str:
    name = socket.gethostname().strip()
    return name or "Co-opWinG"


class _NoopSecondaryIpSystem(SecondaryIpSystem):
    def has_ip_mutation_permission(self) -> bool:
        return False

    def list_interfaces(self):
        return []

    def list_interface_ipv4(self, interface_index: int):
        return []


def _default_secondary_ip_manager(runner_mode: Optional[str]) -> SecondaryIpManager:
    mode = str(runner_mode or "").strip().lower()
    if mode == "real_core" and sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        lease_path = Path(base) / "Co-opWinG" / "secondary_ip_leases.json"
        return SecondaryIpManager(
            WindowsSecondaryIpSystem(SubprocessCommandRunner()),
            JsonLeaseStore(lease_path),
        )
    return SecondaryIpManager(_NoopSecondaryIpSystem(), InMemoryLeaseStore())


class _LanDiscoveryService:
    """HTTP-facing lifecycle wrapper for one LanDiscovery instance."""

    def __init__(
        self,
        config: LanDiscoveryConfig,
        discovery_factory: Callable[[LanDiscoveryConfig], Any] = LanDiscovery,
    ) -> None:
        self._config = config
        self._discovery = discovery_factory(config)
        self._running = False
        self._lock = threading.RLock()

    def status(self) -> LanDiscoveryStatusResponse:
        with self._lock:
            running = self._running
            peer_id = self._discovery.peer_id if running else None
            peer_count = len(self._discovery.get_peers()) if running else 0
            return LanDiscoveryStatusResponse(
                running=running,
                peer_id=peer_id,
                instance_name=self._config.instance_name,
                service_port=self._config.service_port,
                broadcast_port=self._config.broadcast_port,
                peer_count=peer_count,
            )

    def start(self) -> LanDiscoveryStatusResponse:
        with self._lock:
            if self._running:
                return self.status()
            try:
                self._discovery.start()
            except Exception:
                self._running = False
                try:
                    self._discovery.stop()
                except Exception:
                    pass
                raise
            self._running = True
            return self.status()

    def stop(self) -> LanDiscoveryStatusResponse:
        with self._lock:
            if self._running:
                self._discovery.stop()
                self._running = False
            return self.status()

    def peers(self) -> LanDiscoveryPeersResponse:
        with self._lock:
            if not self._running:
                return LanDiscoveryPeersResponse(running=False, peers=[])
            now = time.monotonic()
            peers = [
                LanPeerDto(
                    peer_id=peer.peer_id,
                    name=peer.name,
                    host=peer.host,
                    port=peer.port,
                    version=peer.version,
                    last_seen_age_seconds=max(0.0, now - peer.last_seen),
                )
                for peer in self._discovery.get_peers()
            ]
            return LanDiscoveryPeersResponse(running=True, peers=peers)


class _BackendHandler(BaseHTTPRequestHandler):
    """Request handler — one instance per request (stdlib threading model)."""

    # set by the server factory
    manager: SessionManager = None  # type: ignore[assignment]
    lan_discovery: _LanDiscoveryService = None  # type: ignore[assignment]
    game_profiles: GameProfileStore = None  # type: ignore[assignment]
    process_port_detector: ProcessPortDetector = None  # type: ignore[assignment]

    # suppress stderr logging of every request in tests
    quiet: bool = False

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------

    def _route(self) -> Tuple[str, Optional[str]]:
        """Return (endpoint_key, session_id_or_None)."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        parts = [p for p in path.split("/") if p]

        if self.command == "GET" and path == "/health":
            return ("health", None)
        if path == "/api/lan-discovery/status":
            if self.command == "GET":
                return ("lan_discovery_status", None)
            return ("method_not_allowed", None)
        if path == "/api/lan-discovery/start":
            if self.command == "POST":
                return ("lan_discovery_start", None)
            return ("method_not_allowed", None)
        if path == "/api/lan-discovery/stop":
            if self.command == "POST":
                return ("lan_discovery_stop", None)
            return ("method_not_allowed", None)
        if path == "/api/lan-discovery/peers":
            if self.command == "GET":
                return ("lan_discovery_peers", None)
            return ("method_not_allowed", None)
        if path == "/api/secondary-ip/recommendation":
            if self.command == "GET":
                return ("secondary_ip_recommendation", None)
            return ("method_not_allowed", None)
        if path == "/api/secondary-ip/release":
            if self.command == "POST":
                return ("secondary_ip_release", None)
            return ("method_not_allowed", None)
        if path == "/api/secondary-ip/status":
            if self.command == "GET":
                return ("secondary_ip_status", None)
            return ("method_not_allowed", None)
        if path == "/process-ports/scan":
            if self.command == "POST":
                return ("process_ports_scan", None)
            return ("method_not_allowed", None)
        if self.command == "POST" and path == "/sessions/create":
            return ("create", None)
        if self.command == "POST" and path == "/sessions/join":
            return ("join", None)
        if self.command == "GET" and path == "/sessions":
            return ("list", None)
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "status":
            if self.command == "GET":
                return ("status", parts[1])
            return ("method_not_allowed", None)
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "stop":
            if self.command == "POST":
                return ("stop", parts[1])
            return ("method_not_allowed", None)
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "logs":
            if self.command == "GET":
                return ("logs", parts[1])
            return ("method_not_allowed", None)
        if len(parts) >= 2 and parts[0] == "sessions":
            return ("not_found", None)

        # game profiles endpoints
        if path == "/api/games" or path == "/api/games/":
            if self.command == "GET":
                return ("games_list", None)
            if self.command == "POST":
                return ("games_create", None)
            return ("method_not_allowed", None)
        if len(parts) >= 2 and parts[0] == "api" and parts[1] == "games":
            if len(parts) == 3:
                game_id = parts[2]
                if self.command == "GET":
                    return ("games_get", game_id)
                if self.command == "DELETE":
                    return ("games_delete", game_id)
                return ("method_not_allowed", None)
            if len(parts) == 4 and parts[2] == "games":
                game_id = parts[3]
                action = parts[4] if len(parts) > 4 else ""
                if self.command == "POST":
                    if action == "scan-ports" or parts[4] == "scan-ports":
                        return ("games_scan_ports", game_id)
                    if action == "confirm-ports" or parts[4] == "confirm-ports":
                        return ("games_confirm_ports", game_id)
                return ("method_not_allowed", None)
            return ("not_found", None)

        return ("not_found", None)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        key, sid = self._route()
        if key == "health":
            self._handle_health()
        elif key == "lan_discovery_status":
            self._handle_lan_discovery_status()
        elif key == "lan_discovery_peers":
            self._handle_lan_discovery_peers()
        elif key == "secondary_ip_recommendation":
            self._handle_secondary_ip_recommendation()
        elif key == "secondary_ip_release":
            self._handle_secondary_ip_release()
        elif key == "secondary_ip_status":
            self._handle_secondary_ip_status()
        elif key == "status":
            self._handle_status(sid)
        elif key == "logs":
            self._handle_logs(sid)
        elif key == "list":
            self._handle_list()
        elif key == "games_list":
            self._handle_games_list()
        elif key == "games_get":
            self._handle_games_get(sid)
        elif key == "method_not_allowed":
            self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        else:
            self._send_error(404, "NOT_FOUND", "Not found")

    def do_POST(self) -> None:
        key, sid = self._route()
        if key == "create":
            self._handle_create()
        elif key == "lan_discovery_start":
            self._handle_lan_discovery_start()
        elif key == "lan_discovery_stop":
            self._handle_lan_discovery_stop()
        elif key == "join":
            self._handle_join()
        elif key == "stop":
            self._handle_stop(sid)
        elif key == "games_create":
            self._handle_games_create()
        elif key == "games_scan_ports":
            self._handle_games_scan_ports(sid)
        elif key == "games_confirm_ports":
            self._handle_games_confirm_ports(sid)
        elif key == "secondary_ip_release":
            self._handle_secondary_ip_release()
        elif key == "process_ports_scan":
            self._handle_process_ports_scan()
        elif key == "method_not_allowed":
            self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        else:
            self._send_error(404, "NOT_FOUND", "Not found")

    def do_DELETE(self) -> None:
        key, sid = self._route()
        if key == "games_delete":
            self._handle_games_delete(sid)
        elif key == "method_not_allowed":
            self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        else:
            self._send_error(404, "NOT_FOUND", "Not found")

    def do_PUT(self) -> None:
        self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")

    def do_DELETE(self) -> None:
        self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")

    def do_PATCH(self) -> None:
        self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")

    # ------------------------------------------------------------------
    # endpoint handlers
    # ------------------------------------------------------------------

    def _handle_health(self) -> None:
        self._send_json(200, {
            "status": "ok",
            "version": _VERSION,
            "uptime_seconds": round(time.time() - _START_TIME, 1),
            "backend": "s2pass",
            "mode": self.manager.runner_mode,
            "backend_admin": self.manager.backend_has_ip_mutation_permission(),
        })

    def _handle_lan_discovery_status(self) -> None:
        self._send_json(200, self.lan_discovery.status().to_dict())

    def _handle_lan_discovery_start(self) -> None:
        try:
            status = self.lan_discovery.start()
        except OSError as exc:
            self._send_error(
                503,
                "LAN_DISCOVERY_START_FAILED",
                "LAN discovery failed to start",
                {"reason": str(exc)},
            )
            return
        except Exception as exc:
            self._send_error(
                500,
                "LAN_DISCOVERY_START_FAILED",
                "LAN discovery failed to start",
                {"reason": str(exc)},
            )
            return
        self._send_json(200, status.to_dict())

    def _handle_lan_discovery_stop(self) -> None:
        self._send_json(200, self.lan_discovery.stop().to_dict())

    def _handle_lan_discovery_peers(self) -> None:
        self._send_json(200, self.lan_discovery.peers().to_dict())

    def _handle_secondary_ip_recommendation(self) -> None:
        self._send_json(200, self.manager.secondary_ip_recommendation())

    def _handle_secondary_ip_release(self) -> None:
        self._send_json(200, self.manager.release_secondary_ip())

    def _handle_secondary_ip_status(self) -> None:
        self._send_json(200, self.manager.secondary_ip_status())

    def _handle_process_ports_scan(self) -> None:
        body = self._read_body()
        if body is None:
            return
        pid = body.get("pid")
        if type(pid) is not int or pid <= 0:
            self._send_error(
                400,
                "INVALID_PID",
                "PID must be a positive integer",
                {"field": "pid"},
            )
            return
        try:
            result = self.process_port_detector.scan_pid(pid)
        except ProcessPortDetectionError as exc:
            status = 400 if exc.code == "INVALID_PID" else 500
            self._send_error(status, exc.code, exc.message, {"pid": pid})
            return
        self._send_json(200, result.to_dict())

    def _handle_create(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            info = self.manager.create_session(body)
        except BackendError as exc:
            self._send_error(400, exc.code, exc.message, exc.details)
            return
        self._send_json(201, info.to_dict())

    def _handle_join(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            info = self.manager.join_session(body)
        except BackendError as exc:
            self._send_error(400, exc.code, exc.message, exc.details)
            return
        self._send_json(201, info.to_dict())

    def _handle_status(self, session_id: Optional[str]) -> None:
        if session_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        try:
            info = self.manager.get_session(session_id)
        except BackendError as exc:
            status = 404 if exc.code == "SESSION_NOT_FOUND" else 500
            self._send_error(status, exc.code, exc.message, exc.details)
            return
        self._send_json(200, info.to_dict())

    def _handle_stop(self, session_id: Optional[str]) -> None:
        if session_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        try:
            info = self.manager.stop_session(session_id)
        except BackendError as exc:
            status = 404 if exc.code == "SESSION_NOT_FOUND" else 409
            self._send_error(status, exc.code, exc.message, exc.details)
            return
        self._send_json(200, info.to_dict())

    def _handle_list(self) -> None:
        sessions = self.manager.list_sessions()
        self._send_json(200, {
            "sessions": [s.to_dict() for s in sessions],
        })

    def _handle_logs(self, session_id: Optional[str]) -> None:
        if session_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        try:
            events = self.manager.get_logs(session_id)
        except BackendError as exc:
            status = 404 if exc.code == "SESSION_NOT_FOUND" else 500
            self._send_error(status, exc.code, exc.message, exc.details)
            return
        self._send_json(200, {
            "session_id": session_id,
            "events": [e.to_dict() for e in events],
        })

    # ------------------------------------------------------------------
    # games endpoints
    # ------------------------------------------------------------------

    def _handle_games_list(self) -> None:
        profiles = self.game_profiles.list()
        self._send_json(200, {
            "games": [p.to_dict() for p in profiles],
        })

    def _handle_games_create(self) -> None:
        body = self._read_body()
        if body is None:
            return
        display_name = body.get("display_name")
        executable_path = body.get("executable_path")
        if not isinstance(display_name, str) or not display_name.strip():
            self._send_error(400, "INVALID_REQUEST", "display_name is required")
            return
        if not isinstance(executable_path, str) or not executable_path.strip():
            self._send_error(400, "INVALID_REQUEST", "executable_path is required")
            return
        request = CreateGameRequest(
            display_name=display_name.strip(),
            executable_path=executable_path.strip(),
            working_directory=body.get("working_directory") if isinstance(body.get("working_directory"), str) else None,
            launch_args=body.get("launch_args") if isinstance(body.get("launch_args"), list) else None,
            notes=body.get("notes") if isinstance(body.get("notes"), str) else None,
        )
        profile = self.game_profiles.create(request)
        self._send_json(201, profile.to_dict())

    def _handle_games_get(self, game_id: Optional[str]) -> None:
        if game_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        profile = self.game_profiles.get(game_id)
        if profile is None:
            self._send_error(404, "GAME_NOT_FOUND", "Game profile not found")
            return
        self._send_json(200, profile.to_dict())

    def _handle_games_delete(self, game_id: Optional[str]) -> None:
        if game_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        deleted = self.game_profiles.delete(game_id)
        if not deleted:
            self._send_error(404, "GAME_NOT_FOUND", "Game profile not found")
            return
        self._send_json(200, {"deleted": True, "game_id": game_id})

    def _handle_games_scan_ports(self, game_id: Optional[str]) -> None:
        if game_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        profile = self.game_profiles.get(game_id)
        if profile is None:
            self._send_error(404, "GAME_NOT_FOUND", "Game profile not found")
            return
        body = self._read_body()
        if body is None:
            return
        stage = str(body.get("stage", "manual"))
        process_id = body.get("process_id") if body.get("process_id") is not None else None
        include_low = bool(body.get("include_low_confidence", False))
        try:
            runner = SubprocessCommandRunner()
            detector = ProcessPortDetector(runner)
            result = detector.scan(
                process_name=Path(profile.executable_path).stem if profile.executable_path else None,
                process_id=process_id,
                stage=stage,
                include_low_confidence=include_low,
            )
        except Exception as exc:
            self._send_error(500, "SCAN_FAILED", f"Port scan failed: {exc}")
            return
        self.game_profiles.update_candidates(game_id, result.candidates)
        self._send_json(200, result.to_dict())

    def _handle_games_confirm_ports(self, game_id: Optional[str]) -> None:
        if game_id is None:
            self._send_error(404, "NOT_FOUND", "Not found")
            return
        profile = self.game_profiles.get(game_id)
        if profile is None:
            self._send_error(404, "GAME_NOT_FOUND", "Game profile not found")
            return
        body = self._read_body()
        if body is None:
            return
        request = ConfirmPortsRequest(
            tcp_ports=[int(p) for p in body.get("tcp_ports", []) if isinstance(p, (int, float))],
            udp_ports=[int(p) for p in body.get("udp_ports", []) if isinstance(p, (int, float))],
        )
        updated = self.game_profiles.confirm_ports(game_id, request)
        if updated is None:
            self._send_error(500, "CONFIRM_FAILED", "Failed to confirm ports")
            return
        self._send_json(200, updated.to_dict())

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _read_body(self) -> Optional[Dict[str, Any]]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_error(400, "INVALID_REQUEST", "Empty request body")
            return None
        try:
            raw = self.rfile.read(content_length)
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error(400, "INVALID_REQUEST", "Invalid JSON in request body")
            return None
        if not isinstance(body, dict):
            self._send_error(400, "INVALID_REQUEST", "Request body must be a JSON object")
            return None
        return body

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._send_json(status, {"error": _make_error_dict(code, message, details)})


# ------------------------------------------------------------------
# server factory
# ------------------------------------------------------------------

def make_server(
    host: str = "127.0.0.1",
    port: int = 21520,
    quiet: bool = False,
    runner_mode: Optional[str] = None,
    lan_discovery_factory: Optional[Callable[[LanDiscoveryConfig], Any]] = None,
    secondary_ip_manager: Optional[Any] = None,
    process_port_detector: Optional[ProcessPortDetector] = None,
) -> ThreadingHTTPServer:
    """Create a ThreadingHTTPServer bound to *host*:*port*.

    If *host* is not 127.0.0.1, raises ValueError.
    """
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError(
            f"Backend must bind to 127.0.0.1 only. "
            f"Binding to 0.0.0.0 or external interfaces is not allowed. "
            f"Requested host: {host}"
        )

    effective_runner_mode = runner_mode
    if effective_runner_mode is None:
        effective_runner_mode = os.environ.get("S2PASS_BACKEND_RUNNER", "fake")
    manager = SessionManager(
        runner_mode=runner_mode,
        secondary_ip_manager=secondary_ip_manager
        or _default_secondary_ip_manager(effective_runner_mode),
    )

    class _ConfiguredHandler(_BackendHandler):
        pass

    _ConfiguredHandler.manager = manager
    _ConfiguredHandler.quiet = quiet
    _ConfiguredHandler.game_profiles = GameProfileStore()
    _ConfiguredHandler.process_port_detector = (
        process_port_detector
        or ProcessPortDetector(ProcessPortSubprocessCommandRunner())
    )

    server = ThreadingHTTPServer((host, port), _ConfiguredHandler)
    actual_port = int(server.server_address[1])
    lan_config = LanDiscoveryConfig(
        service_port=actual_port,
        instance_name=_default_instance_name(),
    )
    lan_service = _LanDiscoveryService(
        lan_config,
        discovery_factory=lan_discovery_factory or LanDiscovery,
    )
    _ConfiguredHandler.lan_discovery = lan_service
    server._manager = manager  # keep reference so tests can access
    server._lan_discovery = lan_service  # keep reference so tests can access
    return server


def main() -> None:
    host = "127.0.0.1"
    port = 21520
    parent_pid: Optional[int] = None

    # minimal CLI arg parsing
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] == "--parent-pid" and i + 1 < len(args):
            parent_pid = int(args[i + 1])
            i += 2
        else:
            print(
                "Usage: python -m backend.server "
                "[--host 127.0.0.1] [--port 21520] [--parent-pid PID]"
            )
            sys.exit(1)

    if host not in ("127.0.0.1", "localhost"):
        print(f"Error: backend must bind to 127.0.0.1 only. Requested: {host}")
        sys.exit(1)

    try:
        server = make_server(host=host, port=port, quiet=False)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    manager = getattr(server, "_manager", None)
    runner_mode = getattr(manager, "runner_mode", "unknown")
    print(f"S2Pass Backend ({runner_mode} mode) listening on {host}:{port}")

    if manager is not None and runner_mode == "real_core":
        try:
            cleanup_result = manager._secondary_ip_manager.startup_cleanup_stale_leases()
            if cleanup_result.items:
                print(f"[secondary-ip] startup cleaned {len(cleanup_result.items)} stale lease(s)")
        except Exception as exc:
            print(f"[secondary-ip] startup cleanup error: {exc}")
        print("[secondary-ip] auto-allocation is disabled; use the UI to manually enable Secondary IP")

    if parent_pid is not None:
        _start_parent_monitor(parent_pid, server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        if manager is not None:
            try:
                manager.shutdown_secondary_ip()
            except Exception:
                pass
        server.shutdown()


def _start_parent_monitor(parent_pid: int, server: ThreadingHTTPServer) -> None:
    """Stop the packaged backend when its owning UI process exits."""
    if parent_pid <= 0 or parent_pid == os.getpid():
        return

    def monitor() -> None:
        if sys.platform == "win32":
            _wait_for_windows_process_exit(parent_pid)
        else:
            while True:
                try:
                    os.kill(parent_pid, 0)
                except OSError:
                    break
                time.sleep(1.0)
        server.shutdown()

    threading.Thread(target=monitor, name="parent-pid-monitor", daemon=True).start()


def _wait_for_windows_process_exit(parent_pid: int) -> None:
    import ctypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, infinite)
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
