
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