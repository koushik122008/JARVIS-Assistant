"""
MARK XLIX — Habit Tracker plugin

Daily habits with streaks, weekly check-ins and a one-line overview.
Stored locally in memory/habits.json.
Tool name: habit_tracker
"""

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(DATA_DIR) / "habits.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("habits", {})   # name -> {"created": "YYYY-MM-DD", "log": ["YYYY-MM-DD", ...]}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _today() -> str:
    return date.today().isoformat()


def _streak(days: list) -> int:
    """Consecutive-day streak ending today (or yesterday, so a skipped today
    doesn't kill the streak until it's actually broken)."""
    if not days:
        return 0
    logged_set = set(days)
    cursor = date.today()
    if cursor.isoformat() not in logged_set:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in logged_set:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _fmt_overview(data: dict) -> str:
    if not data["habits"]:
        return "You have no habits yet. Say 'add a habit: drink water' to start one."
    lines = ["Your habits:"]
    for name, info in data["habits"].items():
        log = info.get("log", [])
        st = _streak(log)
        done_today = _today() in log
        mark = "[x]" if done_today else "[ ]"
        lines.append(f"{mark} {name} - {st} day streak")
    return "\n".join(lines)


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    name = (args or {}).get("habit", "") or (args or {}).get("name", "")
    name = name.strip().title()

    data = _load()

    if action in ("add", "create"):
        if not name:
            return "What habit should I track? Say 'add a habit: drink water'."
        if name in data["habits"]:
            return f"You already track '{name}'. Say 'log {name}' when you do it."
        data["habits"][name] = {"created": _today(), "log": []}
        _save(data)
        return f"New habit '{name}' added. Log it each day you do it."

    if action in ("log", "done"):
        if not name:
            return "Which habit are you logging? Say 'log drink water'."
        if name not in data["habits"]:
            return f"I don't track '{name}'. Say 'add a habit: {name}' first."
        log = data["habits"][name].setdefault("log", [])
        today = _today()
        if today in log:
            return f"Already logged '{name}' today. Nice consistency!"
        log.append(today)
        _save(data)
        return f"Logged '{name}' for today. Current streak: {_streak(log)} days."

    if action in ("streak", "status", "overview", "list"):
        if name and name in data["habits"]:
            log = data["habits"][name].get("log", [])
            return (
                f"'{name}': {_streak(log)} day streak, "
                f"logged {len(log)} times since {data['habits'][name].get('created', '?')}."
            )
        result = _fmt_overview(data)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("HABIT TRACKER", result)
            except Exception:
                pass
        return result

    if action in ("remove", "delete"):
        if not name:
            return "Which habit should I remove?"
        if name not in data["habits"]:
            return f"I don't track '{name}'."
        del data["habits"][name]
        _save(data)
        return f"Removed habit '{name}'."

    if action in ("weekly", "week"):
        week_ago = date.today() - timedelta(days=7)
        lines = ["Last 7 days per habit:"]
        for hname, info in data["habits"].items():
            log = info.get("log", [])
            count = sum(1 for d in log if d >= week_ago.isoformat())
            lines.append(f"  {hname}: {count} day(s)")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("HABIT WEEK", result)
            except Exception:
                pass
        return result

    if action == "clear":
        data["habits"] = {}
        _save(data)
        return "Cleared all habits."

    return (
        "Unknown habit action. Try: add, log, streak, overview, weekly, remove, clear."
    )


PLUGIN = {
    "name": "habit_tracker",
    "description": (
        "Tracks daily habits with streaks. Use when the user logs a habit "
        "('log drink water', 'I meditated today'), creates a habit "
        "('add a habit: read 10 pages'), asks about streaks or a habit "
        "overview, or removes a habit."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | log | streak | overview | weekly | remove | clear",
            },
            "habit": {
                "type": "STRING",
                "description": "Habit name, e.g. 'drink water', 'meditation', 'read'",
            },
        },
        "required": ["action"],
    },
}
