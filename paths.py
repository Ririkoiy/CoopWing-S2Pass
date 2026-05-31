"""
S2Pass Path Helpers — Preview 0.1

Portable path resolution for both source-run and PyInstaller onedir modes.

Detection logic:
- If running from a PyInstaller frozen bundle: base = directory of sys.executable
- Else (source run): base = directory of this file (project root)

Directories are NOT created by these helpers. They are created on demand
only when a write operation actually needs them (e.g. save_profiles, run doctor).
"""

import os
import sys


def app_base_dir() -> str:
    """Return the application base directory.

    - Source mode: directory containing this file (project root).
    - PyInstaller onedir: directory containing the executable.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller onedir: sys.executable is inside the dist folder
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Source run: this file lives at project root
        return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path: str) -> str:
    """Resolve a relative path against the application base directory."""
    return os.path.join(app_base_dir(), relative_path)


def config_dir() -> str:
    """Return the config directory path (not created automatically)."""
    return os.path.join(app_base_dir(), "config")


def diagnostics_dir() -> str:
    """Return the diagnostics directory path (not created automatically)."""
    return os.path.join(app_base_dir(), "diagnostics")


def tools_dir() -> str:
    """Return the tools directory path (not created automatically)."""
    return os.path.join(app_base_dir(), "tools")


def logs_dir() -> str:
    """Return the logs directory path (not created automatically)."""
    return os.path.join(app_base_dir(), "logs")
