import sys
import os
from typing import Any, Dict

# Enable importing from parent directory (project root)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from backend.backend_controller import BackendController
from backend.api_models import (
    make_success_response,
    make_error_response,
    BackendError,
    INTERNAL_ERROR,
    SETTINGS_INVALID_VALUE,
    SETTINGS_WRITE_FAILED
)
from adapters.profile import GameProfile

# ------------------------------------------------------------------
# API Handler Functions
# ------------------------------------------------------------------

def handle_get_health(controller: BackendController) -> Dict[str, Any]:
    """GET /health"""
    try:
        return make_success_response({
            "version": "0.2.0-preview",
            "uptime_seconds": 0,  # placeholder
            "status": "ready"
        })
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_get_settings(controller: BackendController) -> Dict[str, Any]:
    """GET /settings"""
    try:
        settings_data = controller.settings_store.get_all()
        return make_success_response(settings_data)
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_put_settings(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """PUT /settings"""
    try:
        if not isinstance(data, dict):
            raise BackendError(SETTINGS_INVALID_VALUE, "Invalid settings data format")
            
        for k, v in data.items():
            controller.settings_store.set(k, v)
            
        try:
            controller.settings_store.save()
        except Exception as e:
            raise BackendError(SETTINGS_WRITE_FAILED, f"Failed to save settings: {e}")
            
        return make_success_response(controller.settings_store.get_all())
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_get_servers(controller: BackendController) -> Dict[str, Any]:
    """GET /servers"""
    try:
        servers = controller.server_store.get_all()
        default_server_id = controller.settings_store.get("default_server_id")
        return make_success_response({
            "servers": servers,
            "default_server_id": default_server_id
        })
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_post_servers(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /servers"""
    try:
        if not isinstance(data, dict):
            raise BackendError(INTERNAL_ERROR, "Invalid server data format")
            
        display_name = data.get("display_name", "")
        host = data.get("host", "")
        description = data.get("description", "")
        enabled = data.get("enabled", True)
        
        if not host:
            raise BackendError("SERVER_INVALID_HOST", "Server host cannot be empty")
            
        new_server = controller.server_store.add(
            display_name=display_name,
            host=host,
            description=description,
            enabled=enabled
        )
        return make_success_response(new_server)
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except (ValueError, KeyError) as e:
        # Map store level validations
        code = str(e)
        msg = "Validation failed"
        if code == "SERVER_INVALID_HOST":
            msg = "The server host address is invalid"
        elif code == "SERVER_DUPLICATE_HOST":
            msg = "A server with this host address already exists"
        return make_error_response(code, msg)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_get_profiles(controller: BackendController) -> Dict[str, Any]:
    """GET /profiles"""
    try:
        profiles = [p.to_dict() for p in controller.get_profiles()]
        return make_success_response({"profiles": profiles})
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_post_profiles(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /profiles"""
    try:
        if not isinstance(data, dict):
            raise BackendError(INTERNAL_ERROR, "Invalid profile data format")
            
        profile = GameProfile.from_dict(data)
        created_profile = controller.add_profile(profile)
        return make_success_response(created_profile.to_dict())
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_post_profiles_from_exe(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /profiles/from-exe"""
    try:
        if not isinstance(data, dict):
            raise BackendError("PROFILE_INVALID_EXE", "Invalid request body.")
            
        exe_path = data.get("exe_path", "")
        draft = controller.create_profile_draft_from_exe(exe_path)
        return make_success_response({"draft": draft.to_dict()})
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_launch_start(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /launch/start"""
    try:
        if not isinstance(data, dict):
            raise BackendError("PROFILE_NOT_FOUND", "Invalid request body.")
            
        profile_id = data.get("profile_id", "")
        if not profile_id:
            raise BackendError("PROFILE_NOT_FOUND", "Profile ID is required.")
            
        pid = controller.start_launch(profile_id)
        return make_success_response({
            "pid": pid,
            "profile_id": profile_id
        })
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_launch_stop(controller: BackendController, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """POST /launch/stop"""
    try:
        controller.stop_launch()
        return make_success_response({"status": "stopped"})
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_udp_start(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /udp/start"""
    try:
        if not isinstance(data, dict):
            raise BackendError("PROFILE_NOT_FOUND", "Invalid request body.")
            
        profile_id = data.get("profile_id", "")
        mode = data.get("mode", "echo")
        if not profile_id:
            raise BackendError("PROFILE_NOT_FOUND", "Profile ID is required.")
            
        res = controller.start_udp(profile_id, mode=mode)
        return make_success_response(res)
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_udp_stop(controller: BackendController, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """POST /udp/stop"""
    try:
        controller.stop_udp()
        return make_success_response({"status": "stopped"})
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_doctor_run(controller: BackendController, data: Dict[str, Any]) -> Dict[str, Any]:
    """POST /doctor/run"""
    try:
        if not isinstance(data, dict):
            data = {}
            
        peer_ip = data.get("peer_ip", "")
        interface = data.get("interface", "")
        server_host = data.get("server_host", "")
        no_zip = data.get("no_zip", False)
        
        started = controller.doctor_runner.run(
            peer_ip=peer_ip,
            interface=interface,
            server_host=server_host,
            no_zip=no_zip
        )
        if started:
            return make_success_response({
                "task_id": controller.doctor_runner.task_id,
                "status": "running"
            })
        else:
            raise BackendError("INTERNAL_ERROR", "Failed to start Network Doctor runner.")
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_doctor_status(controller: BackendController) -> Dict[str, Any]:
    """GET /doctor/status"""
    try:
        controller.doctor_runner.poll()
        return make_success_response(controller.doctor_runner.get_status())
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})

def handle_doctor_reports(controller: BackendController) -> Dict[str, Any]:
    """GET /doctor/reports"""
    try:
        reports = controller.get_doctor_reports()
        return make_success_response({"reports": reports})
    except BackendError as e:
        return make_error_response(e.code, e.message, e.details)
    except Exception as e:
        return make_error_response(INTERNAL_ERROR, "Internal server error", {"details": str(e)})


if __name__ == "__main__":
    # Standard entry check to initialize loop policy and create base controller
    import asyncio
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    print("S2Pass Backend Skeleton loaded successfully.")
    controller = BackendController()
    print("BackendController initialized successfully.")
    print("Settings file: ", controller.settings_store._settings_path)
    print("Servers file: ", controller.server_store._servers_path)
