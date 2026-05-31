# -*- coding: utf-8 -*-
"""S2Pass Backend — stdlib HTTP server.

Binds 127.0.0.1 only. Exposes relay smoke session control endpoints.
Does not import network_core or adapters.

Manual real-core backend mode:
  S2PASS_BACKEND_RUNNER=real_core python -m backend.server
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from backend.models import BackendError
from backend.session_manager import SessionManager

_START_TIME = time.time()
_VERSION = "0.1.0"


def _make_error_dict(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        d["details"] = details
    return d


class _BackendHandler(BaseHTTPRequestHandler):
    """Request handler — one instance per request (stdlib threading model)."""

    # set by the server factory
    manager: SessionManager = None  # type: ignore[assignment]

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

        return ("not_found", None)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        key, sid = self._route()
        if key == "health":
            self._handle_health()
        elif key == "status":
            self._handle_status(sid)
        elif key == "logs":
            self._handle_logs(sid)
        elif key == "list":
            self._handle_list()
        elif key == "method_not_allowed":
            self._send_error(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        else:
            self._send_error(404, "NOT_FOUND", "Not found")

    def do_POST(self) -> None:
        key, sid = self._route()
        if key == "create":
            self._handle_create()
        elif key == "join":
            self._handle_join()
        elif key == "stop":
            self._handle_stop(sid)
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
        })

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

    manager = SessionManager(runner_mode=runner_mode)

    class _ConfiguredHandler(_BackendHandler):
        pass

    _ConfiguredHandler.manager = manager
    _ConfiguredHandler.quiet = quiet

    server = ThreadingHTTPServer((host, port), _ConfiguredHandler)
    server._manager = manager  # keep reference so tests can access
    return server


def main() -> None:
    host = "127.0.0.1"
    port = 21520

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
        else:
            print(f"Usage: python -m backend.server [--host 127.0.0.1] [--port 21520]")
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
