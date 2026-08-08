"""
MARK XLIX — Pomodoro Timer plugin

Focus / break sessions computed from timestamps (no background threads
needed). Completed sessions are recorded in memory/pomodoro.json.
Tool name: pomodoro
"""

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

DEFAULT_FOCUS = 25   # minutes
DEFAULT_BREAK = 5


def _store_path() -> Path:
    return Path(DATA_DIR) / "pomodoro.json"


def _now() -> datetime:
    return datetime.now()


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("state", None)          # {"kind":"focus|break","start": iso, "minutes": n}
    data.setdefault("sessions", [])         # {"date","kind","minutes","completed":"YYYY-MM-DD"}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _remaining(state: dict) -> int:
    """Seconds remaining in the current session (0 if finished)."""
    start = datetime.fromisoformat(state["start"])
    total_s = state["minutes"] * 60
    elapsed = (_now() - start).total_seconds()
    return max(0, int(total_s - elapsed))


def _fmt_remaining(secs: int) -> str:
    m, s = divmod(secs, 60)
    return f"{m} minute(s), {s} second(s)"


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    speak = (ctx or {}).get("speak")
    action = (args or {}).get("action", "").strip().lower()
    data = _load()

    # Auto-complete a finished session before answering.
    state = data.get("state")
    if state and _remaining(state) == 0:
        data["sessions"].append({
            "date": date.today().isoformat(),
            "kind": state["kind"],
            "minutes": state["minutes"],
            "completed": _now().isoformat(),
        })
        data["state"] = None
        _save(data)
        if speak and callable(speak):
            try:
                speak(f"{state['kind']} session complete. Well done.")
            except Exception:
                pass

    if action in ("start", "focus", "work"):
        try:
            minutes = int((args or {}).get("minutes", DEFAULT_FOCUS))
        except (TypeError, ValueError):
            minutes = DEFAULT_FOCUS
        minutes = max(1, min(minutes, 180))
        data["state"] = {"kind": "focus", "start": _now().isoformat(), "minutes": minutes}
        _save(data)
        return (
            f"Focus session started for {minutes} minutes. "
            f"I'll remind you when it's done. Say 'pomodoro status' anytime."
        )

    if action in ("break", "rest"):
        try:
            minutes = int((args or {}).get("minutes", DEFAULT_BREAK))
        except (TypeError, ValueError):
            minutes = DEFAULT_BREAK
        minutes = max(1, min(minutes, 60))
        data["state"] = {"kind": "break", "start": _now().isoformat(), "minutes": minutes}
        _save(data)
        return f"Break started for {minutes} minutes. Stretch, breathe, hydrate."

    if action in ("status", "remaining"):
        state = data.get("state")
        if not state:
            return "No active session. Say 'pomodoro start 25' to focus."
        remain = _remaining(state)
        kind = state["kind"]
        if remain == 0:
            return f"Your {kind} session just finished."
        return f"{kind.title()} session: {_fmt_remaining(remain)} left."

    if action in ("stop", "cancel"):
        state = data.get("state")
        if not state:
            return "No active session to stop."
        data["state"] = None
        _save(data)
        return f"Stopped your {state['kind']} session."

    if action in ("skip", "next"):
        state = data.get("state")
        if not state:
            return "No active session to skip."
        data["state"] = None
        _save(data)
        return f"Skipped the {state['kind']} session."

    if action in ("history", "today"):
        today = date.today().isoformat()
        sess = [s for s in data["sessions"] if s["date"] == today]
        if not sess:
            return "No completed sessions today. Start one with 'pomodoro start 25'."
        focus_n = sum(1 for s in sess if s["kind"] == "focus")
        focus_min = sum(s["minutes"] for s in sess if s["kind"] == "focus")
        result = (f"{len(sess)} session(s) today - {focus_n} focus "
                  f"({focus_min} minutes total).")
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("POMODORO TODAY", "\n".join(
                    f"{s['completed'][11:16]} {s['kind']} - {s['minutes']} min"
                    for s in sess
                ))
            except Exception:
                pass
        return result

    return (
        "Unknown pomodoro action. Try: start, break, status, stop, skip, today."
    )


PLUGIN = {
    "name": "pomodoro",
    "description": (
        "Pomodoro focus timer. Use when the user wants to start a focus "
        "session ('pomodoro 25 minutes', 'start a focus timer'), start a "
        "break, check time remaining, stop or skip the timer, or see "
        "today's completed sessions."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | break | status | stop | skip | today",
            },
            "minutes": {
                "type": "NUMBER",
                "description": "Session length in minutes (focus default 25, break default 5)",
            },
        },
        "required": ["action"],
    },
}
