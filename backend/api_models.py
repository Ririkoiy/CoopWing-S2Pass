from typing import Any, Dict, Optional

# Standard Error Codes
PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
PROFILE_INVALID_EXE = "PROFILE_INVALID_EXE"
PROFILE_EXE_NOT_FOUND = "PROFILE_EXE_NOT_FOUND"
PROFILE_EXE_IS_DIRECTORY = "PROFILE_EXE_IS_DIRECTORY"
PROFILE_EXE_ACCESS_DENIED = "PROFILE_EXE_ACCESS_DENIED"
PROFILE_IN_USE = "PROFILE_IN_USE"
SERVER_NOT_FOUND = "SERVER_NOT_FOUND"
SERVER_INVALID_HOST = "SERVER_INVALID_HOST"
SERVER_DUPLICATE_HOST = "SERVER_DUPLICATE_HOST"
SERVER_IS_DEFAULT = "SERVER_IS_DEFAULT"
SETTINGS_INVALID_VALUE = "SETTINGS_INVALID_VALUE"
SETTINGS_WRITE_FAILED = "SETTINGS_WRITE_FAILED"
LAUNCH_ALREADY_RUNNING = "LAUNCH_ALREADY_RUNNING"
LAUNCH_FAILED = "LAUNCH_FAILED"
LAUNCH_NOT_RUNNING = "LAUNCH_NOT_RUNNING"
UDP_ALREADY_RUNNING = "UDP_ALREADY_RUNNING"
UDP_BIND_FAILED = "UDP_BIND_FAILED"
UDP_NOT_RUNNING = "UDP_NOT_RUNNING"
PROFILE_NOT_UDP_TYPE = "PROFILE_NOT_UDP_TYPE"
DOCTOR_ALREADY_RUNNING = "DOCTOR_ALREADY_RUNNING"
DOCTOR_TOOL_NOT_FOUND = "DOCTOR_TOOL_NOT_FOUND"
INTERNAL_ERROR = "INTERNAL_ERROR"

def make_success_response(data: Any) -> Dict[str, Any]:
    """Create a standardized success response envelope."""
    return {
        "ok": True,
        "data": data
    }

def make_error_response(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a standardized error response envelope."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {}
        }
    }

class BackendError(Exception):
    """Exception raised for backend business logic errors, carrying API-compatible code, message and details."""
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details if details is not None else {}

