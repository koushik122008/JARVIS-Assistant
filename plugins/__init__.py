"""
MARK XLIX — Plugin System

Any module placed in this folder becomes a Jarvis tool automatically.
A plugin module must define:

    PLUGIN = {
        "name":        "tool_name",            # unique tool name called by Gemini
        "description": "What the tool does…",  # shown to the LLM
        "parameters":  {...},                  # JSON schema (same format as main.py tools)
    }

    def handle(args: dict, ctx: dict) -> str:
        # args  — parameters Gemini extracted
        # ctx   — {"ui": <JarvisUI or None>, "speak": <callable or None>}
        return "spoken reply for the user"

Drop a new .py file in this folder, restart Jarvis, and the tool is live —
no changes to main.py required.
"""

import importlib
import pkgutil
from pathlib import Path

_PKG_DIR = Path(__file__).parent


def _log(msg: str) -> None:
    """Console-safe logging — Windows cp1252 consoles can't print emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))

# Tool names that must never collide with built-in tools in main.py.
_RESERVED = {
    "generate_image", "open_app", "web_search", "system_status", "weather_report",
    "send_message", "reminder", "youtube_video", "screen_process", "close_camera",
    "computer_settings", "browser_control", "file_controller", "desktop_control",
    "code_helper", "dev_agent", "computer_control", "game_updater", "flight_finder",
    "shutdown_jarvis", "file_processor", "currency_converter", "crypto_prices",
    "unit_converter", "alarm", "battery_info", "translate_text", "stock_prices",
    "set_timer", "save_memory", "agent_task",
}


def _discover() -> tuple[list, dict]:
    """Scan this package for plugin modules and build tool declarations + handlers."""
    tools: list = []
    handlers: dict = {}

    for mod_info in pkgutil.iter_modules([str(_PKG_DIR)]):
        name = mod_info.name
        if name.startswith("_"):
            continue

        try:
            mod = importlib.import_module(f"plugins.{name}")
        except Exception as e:  # noqa: BLE001 — one broken plugin must not kill the app
            _log(f"[Plugins] !! Could not load '{name}': {e}")
            continue

        meta   = getattr(mod, "PLUGIN", None)
        handle = getattr(mod, "handle", None)

        if not (isinstance(meta, dict) and meta.get("name") and callable(handle)):
            _log(f"[Plugins] !! '{name}' must define PLUGIN dict + handle(args, ctx)")
            continue

        tool_name = meta["name"]
        if tool_name in _RESERVED or tool_name in handlers:
            _log(f"[Plugins] !! '{name}' tool name '{tool_name}' collides - skipped")
            continue

        tools.append({
            "name": tool_name,
            "description": meta.get("description", ""),
            "parameters": meta.get("parameters", {
                "type": "OBJECT",
                "properties": {},
            }),
        })
        handlers[tool_name] = handle
        _log(f"[Plugins] Loaded '{tool_name}' from plugins/{name}.py")

    return tools, handlers


PLUGIN_TOOLS, PLUGIN_HANDLERS = _discover()


def build_plugin_context(host) -> dict:
    """Build the ctx dict handed to every plugin handler."""
    return {
        "ui":   getattr(host, "ui", None),
        "speak": getattr(host, "speak", None),
    }
