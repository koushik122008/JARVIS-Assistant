"""
MARK XLIX — Shared Utilities

Single source of truth for:
  - Base directory resolution (frozen / script)
  - API key & config loading
  - Gemini client factory
  - Common constants

Import this instead of duplicating logic in every module.
"""

import json
import platform
import sys
from pathlib import Path
from typing import Any


# ── Paths ──────────────────────────────────────────────────────────────────────

def get_base_dir() -> Path:
    """Return the project root directory (works for both script and PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
CONFIG_DIR      = BASE_DIR / "config"
API_CONFIG_PATH = CONFIG_DIR / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
MEMORY_PATH     = BASE_DIR / "memory" / "long_term.json"


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(data: dict) -> None:
    """Write a dict to api_keys.json, preserving existing keys."""
    current = load_config()
    current.update(data)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    API_CONFIG_PATH.write_text(
        json.dumps(current, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )


def get_api_key() -> str:
    """Get the Gemini API key from config. Raises RuntimeError if missing."""
    key = load_config().get("gemini_api_key", "")
    if not key:
        raise RuntimeError("gemini_api_key not found in config/api_keys.json")
    return key


def get_os_name() -> str:
    """Return normalised OS name: 'windows', 'mac', or 'linux'."""
    return {
        "Windows": "windows",
        "Darwin":  "mac",
        "Linux":   "linux",
    }.get(platform.system(), "linux")


def is_windows() -> bool:
    return get_os_name() == "windows"


def is_mac() -> bool:
    return get_os_name() == "mac"


def is_linux() -> bool:
    return get_os_name() == "linux"


# ── Gemini client factory ──────────────────────────────────────────────────────

def new_gemini_client(model: str | None = None):
    """
    Return a Gemini client wrapper with a single `generate_content` method.
    Usage:
        model = new_gemini_client("gemini-2.5-flash")
        response = model.generate_content("Hello")
    """
    from google import genai

    client = genai.Client(
        api_key=get_api_key(),
        http_options={"api_version": "v1beta"},
    )

    _model_name = model or "gemini-2.5-flash"

    class _WrappedModel:
        def generate_content(self, contents, **_kw):
            return client.models.generate_content(
                model=_model_name, contents=contents, **_kw,
            )

    return _WrappedModel()


# ── Constants ──────────────────────────────────────────────────────────────────

LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16_000
RECEIVE_SAMPLE_RATE = 24_000
CHUNK_SIZE          = 1_024

# Image vision
IMG_MAX_W = 1280
IMG_MAX_H = 720
JPEG_Q    = 82

# Hidden window flag for Windows subprocess calls
_OS = platform.system()
WIN_HIDE: dict = (
    {"creationflags": 0x08000000}   # CREATE_NO_WINDOW
    if _OS == "Windows" else {}
)

# Screenshot safe root
SAFE_SCREENSHOT_ROOTS = (Path.home(),)

# Visual constants for UI accent colour system
DEFAULT_UI_COLOR = "#00d4ff"
