# -*- coding: utf-8 -*-
"""Game profile CRUD with JSON disk storage.

v0.3-J: Local game profile store.  Path is explicit — tests inject temp paths.
JSON is written atomically.  No protocol payloads or tokens stored.
"""

from __future__ import annotations

import dataclasses
import json as _json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from backend.process_port_detector import PortCandidate


def _default_profiles_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return Path(base) / "Co-opWinG" / "game_profiles.json"


# ═══════════════════════════════════════════════════════════════════════════
# Models
# ═══════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class GameProfile:
    game_id: str
    display_name: str
    executable_path: str
    working_directory: Optional[str] = None
    launch_args: Optional[list[str]] = None
    confirmed_tcp_ports: list[int] = dataclasses.field(default_factory=list)
    confirmed_udp_ports: list[int] = dataclasses.field(default_factory=list)
    candidate_ports: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    notes: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "game_id": self.game_id,
            "display_name": self.display_name,
            "executable_path": self.executable_path,
            "confirmed_tcp_ports": list(self.confirmed_tcp_ports),
            "confirmed_udp_ports": list(self.confirmed_udp_ports),
            "candidate_ports": list(self.candidate_ports),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.working_directory is not None:
            d["working_directory"] = self.working_directory
        if self.launch_args is not None:
            d["launch_args"] = self.launch_args
        if self.notes is not None:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GameProfile":
        return cls(
            game_id=str(d["game_id"]),
            display_name=str(d["display_name"]),
            executable_path=str(d["executable_path"]),
            working_directory=d.get("working_directory") if d.get("working_directory") else None,
            launch_args=(
                [str(a) for a in d["launch_args"]]
                if isinstance(d.get("launch_args"), list) else None
            ),
            confirmed_tcp_ports=[int(p) for p in d.get("confirmed_tcp_ports", [])],
            confirmed_udp_ports=[int(p) for p in d.get("confirmed_udp_ports", [])],
            candidate_ports=list(d.get("candidate_ports", [])),
            notes=str(d["notes"]) if d.get("notes") else None,
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )


@dataclasses.dataclass
class CreateGameRequest:
    display_name: str
    executable_path: str
    working_directory: Optional[str] = None
    launch_args: Optional[list[str]] = None
    notes: Optional[str] = None


@dataclasses.dataclass
class UpdateGameRequest:
    display_name: Optional[str] = None
    executable_path: Optional[str] = None
    working_directory: Optional[str] = None
    launch_args: Optional[list[str]] = None
    notes: Optional[str] = None


@dataclasses.dataclass
class ScanPortsRequest:
    stage: str = "manual"
    process_id: Optional[int] = None
    include_low_confidence: bool = False


@dataclasses.dataclass
class ConfirmPortsRequest:
    tcp_ports: list[int] = dataclasses.field(default_factory=list)
    udp_ports: list[int] = dataclasses.field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Store
# ═══════════════════════════════════════════════════════════════════════════

class GameProfileStore:
    """CRUD store for GameProfile backed by a JSON file."""

    def __init__(
        self,
        path: Optional[Path] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path) if path is not None else _default_profiles_path()
        self._now = now

    @property
    def path(self) -> Path:
        return self._path

    def list(self) -> list[GameProfile]:
        return self._load_all()

    def get(self, game_id: str) -> Optional[GameProfile]:
        for profile in self._load_all():
            if profile.game_id == game_id:
                return profile
        return None

    def create(self, request: CreateGameRequest) -> GameProfile:
        import uuid
        now = self._now()
        profile = GameProfile(
            game_id=uuid.uuid4().hex[:12],
            display_name=request.display_name,
            executable_path=request.executable_path,
            working_directory=request.working_directory,
            launch_args=request.launch_args,
            notes=request.notes,
            created_at=now,
            updated_at=now,
        )
        profiles = self._load_all()
        profiles.append(profile)
        self._save_all(profiles)
        return profile

    def delete(self, game_id: str) -> bool:
        profiles = self._load_all()
        new_profiles = [p for p in profiles if p.game_id != game_id]
        if len(new_profiles) == len(profiles):
            return False
        self._save_all(new_profiles)
        return True

    def update_candidates(self, game_id: str, candidates: list[PortCandidate]) -> Optional[GameProfile]:
        profiles = self._load_all()
        for profile in profiles:
            if profile.game_id == game_id:
                profile.candidate_ports = [c.to_dict() for c in candidates]
                profile.updated_at = self._now()
                self._save_all(profiles)
                return profile
        return None

    def confirm_ports(self, game_id: str, request: ConfirmPortsRequest) -> Optional[GameProfile]:
        profiles = self._load_all()
        for profile in profiles:
            if profile.game_id == game_id:
                profile.confirmed_tcp_ports = sorted(set(request.tcp_ports))
                profile.confirmed_udp_ports = sorted(set(request.udp_ports))
                profile.updated_at = self._now()
                self._save_all(profiles)
                return profile
        return None

    # ── disk I/O ────────────────────────────────────────────────────────

    def _load_all(self) -> list[GameProfile]:
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        try:
            raw = _json.loads(text)
        except _json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        result: list[GameProfile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                result.append(GameProfile.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def _save_all(self, profiles: list[GameProfile]) -> None:
        raw = [p.to_dict() for p in profiles]
        text = _json.dumps(raw, indent=2, ensure_ascii=False, sort_keys=True)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".game_profiles_", dir=str(self._path.parent))
        try:
            os.write(fd, text.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(self._path))
