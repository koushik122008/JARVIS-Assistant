
from utils import CONFIG_DIR, API_CONFIG_PATH as CONFIG_FILE, load_config, save_config


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return CONFIG_FILE.exists()


def save_api_keys(gemini_api_key: str) -> None:
    data = load_config()
    data["gemini_api_key"] = gemini_api_key.strip()
    save_config({"gemini_api_key": data["gemini_api_key"]})


def load_api_keys() -> dict:
    return load_config()


def get_gemini_key() -> str | None:
    return load_config().get("gemini_api_key")


def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_config().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_config().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    save_config({
        "assistant_name": assistant_name.strip() or "JARVIS",
        "user_name": user_name.strip(),
    })


def get_wake_word_keyword() -> str:
    """Return the configured wake word keyword, or 'jarvis' if not set."""
    return load_config().get("wake_word_keyword", "jarvis") or "jarvis"


def get_wake_word_sensitivity() -> float:
    """Return configured sensitivity (0.0-1.0), clamped, default 0.5."""
    try:
        val = float(load_config().get("wake_word_sensitivity", 0.5))
        return max(0.0, min(1.0, val))
    except (TypeError, ValueError):
        return 0.5


def get_brief_enabled() -> bool:
    return load_config().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    save_config({"morning_brief_enabled": enabled})


def get_gemini_voice() -> str:
    """Return the configured Gemini voice, or 'Charon' if not set."""
    return load_config().get("gemini_voice", "Charon") or "Charon"


def save_gemini_voice(voice: str) -> None:
    """Persist the Gemini voice; takes effect on the next session."""
    save_config({"gemini_voice": (voice or "Charon").strip() or "Charon"})


def get_boot_sound_enabled() -> bool:
    """Return whether the startup chime is enabled (default True)."""
    return bool(load_config().get("boot_sound_enabled", True))


def save_boot_sound_enabled(enabled: bool) -> None:
    """Persist the startup chime toggle; takes effect on the next boot."""
    save_config({"boot_sound_enabled": bool(enabled)})


def get_proactive_enabled() -> bool:
    """Return whether proactive check-ins are enabled (default True)."""
    return bool(load_config().get("proactive_enabled", True))


def save_proactive_enabled(enabled: bool) -> None:
    """Persist the proactive check-in toggle."""
    save_config({"proactive_enabled": bool(enabled)})


def get_background_wake_enabled() -> bool:
    """Return whether cold-start 'Hey Jarvis' wake is enabled (default False)."""
    return bool(load_config().get("background_wake_enabled", False))


def save_background_wake_enabled(enabled: bool) -> None:
    """Persist the cold-start wake toggle."""
    save_config({"background_wake_enabled": bool(enabled)})


def get_metrics_panel_collapsed() -> bool:
    """Return whether the SYS MONITOR column starts collapsed (default False)."""
    return bool(load_config().get("metrics_panel_collapsed", False))


def save_metrics_panel_collapsed(collapsed: bool) -> None:
    """Persist the SYS MONITOR collapsed state across restarts."""
    save_config({"metrics_panel_collapsed": bool(collapsed)})


def get_camera_sensing_enabled() -> bool:
    """Return whether continuous camera sensing is enabled (default False).

    Off by default because sensing keeps the webcam warm while JARVIS runs;
    the user opts in via the config or the settings toggle.
    """
    return bool(load_config().get("camera_sensing_enabled", False))


def save_camera_sensing_enabled(enabled: bool) -> None:
    """Persist the camera sensing toggle."""
    save_config({"camera_sensing_enabled": bool(enabled)})


def get_camera_sensing_scene_ai() -> bool:
    """Return whether AI scene analysis (Gemini vision) is enabled (default False).

    Costs an API call per run, so it stays opt-in.
    """
    return bool(load_config().get("camera_sensing_scene_ai", False))


def save_camera_sensing_scene_ai(enabled: bool) -> None:
    """Persist the AI scene analysis toggle."""
    save_config({"camera_sensing_scene_ai": bool(enabled)})


def get_camera_sensing_privacy() -> bool:
    """Return whether camera sensing runs in privacy mode (default False).

    Privacy mode performs local detection only — motion / person / ambient —
    and never invokes the AI scene hook, so no camera frame leaves the device.
    Detection events are still reported to the activity log and HUD.
    """
    return bool(load_config().get("camera_sensing_privacy", False))


def save_camera_sensing_privacy(enabled: bool) -> None:
    """Persist the camera sensing privacy mode toggle."""
    save_config({"camera_sensing_privacy": bool(enabled)})