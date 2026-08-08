"""
MARK XLIX — Config Package

Configuration loading, settings management, and API key access.
"""

import sys


def is_windows() -> bool:
    """Return True if running on Windows."""
    return sys.platform.startswith("win")


def is_mac() -> bool:
    """Return True if running on macOS."""
    return sys.platform.startswith("darwin")


def is_linux() -> bool:
    """Return True if running on Linux."""
    return sys.platform.startswith("linux")


def get_os() -> str:
    """Return the OS name: ``'windows'``, ``'mac'``, or ``'linux'``."""
    if is_windows():
        return "windows"
    if is_mac():
        return "mac"
    return "linux"
