from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import os
import json

@dataclass
class GameProfile:
    profile_id: str
    display_name: str
    exe_path: str
    working_dir: str = ""
    launch_args: str = ""
    adapter_type: str = "launch_only"
    protocol: str = ""
    local_bind_host: str = "127.0.0.1"
    local_bind_port: Optional[int] = None
    remote_target_host: str = ""
    remote_target_port: Optional[int] = None
    expected_process_name: str = ""
    expected_ports: List[int] = field(default_factory=list)
    doctor_profile: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        # Resolve working_dir if not specified
        if not self.working_dir and self.exe_path:
            self.working_dir = os.path.dirname(os.path.abspath(self.exe_path))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GameProfile":
        return cls(
            profile_id=d.get("profile_id", ""),
            display_name=d.get("display_name", ""),
            exe_path=d.get("exe_path", ""),
            working_dir=d.get("working_dir", ""),
            launch_args=d.get("launch_args", ""),
            adapter_type=d.get("adapter_type", "launch_only"),
            protocol=d.get("protocol", ""),
            local_bind_host=d.get("local_bind_host", "127.0.0.1"),
            local_bind_port=d.get("local_bind_port"),
            remote_target_host=d.get("remote_target_host", ""),
            remote_target_port=d.get("remote_target_port"),
            expected_process_name=d.get("expected_process_name", ""),
            expected_ports=d.get("expected_ports") or [],
            doctor_profile=d.get("doctor_profile") or {},
            notes=d.get("notes", "")
        )

def load_profiles(file_path: str) -> List[GameProfile]:
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [GameProfile.from_dict(item) for item in data]
        elif isinstance(data, dict):
            return [GameProfile.from_dict(item) for item in data.values()]
        return []
    except Exception as e:
        raise RuntimeError(f"Failed to load profiles from {file_path}: {e}") from e

def save_profiles(profiles: List[GameProfile], file_path: str) -> None:
    dir_name = os.path.dirname(file_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)
    try:
        data = [p.to_dict() for p in profiles]
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        raise RuntimeError(f"Failed to save profiles to {file_path}: {e}") from e
