"""Writable application state, independent of Qt and the launch directory."""
import os
import sys
from pathlib import Path


def user_data_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chem Suite"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Chem Suite"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "chem-suite"


def default_workspace():
    return user_data_directory() / "workspace" if getattr(sys, "frozen", False) else Path(".chem-suite")
