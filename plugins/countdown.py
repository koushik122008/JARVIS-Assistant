"""
MARK XLIX — Countdown plugin

Upcoming dates and anniversaries: 'how many days until X', birthday
countdowns (recur yearly), and the soonest upcoming event.
Stored locally in memory/countdowns.json.
Tool name: countdown
"""

import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(DATA_DIR) / "countdowns.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("events", {})   # name -> {"date": "YYYY-MM-DD" or "MM-DD", "annual": bool}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _parse_date(raw: str):
    """Accept YYYY-MM-DD (one-off) or MM-DD (annual). Returns (date, annual)."""
    raw = (raw or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), False
    except ValueError:
        pass
    # annual month-day (no year) - parsed manually to stay compatible
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", raw)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                return date(2000, month, day), True
            except ValueError:
                return None, False
    return None, False


def _next_occurrence(d: date, annual: bool) -> date:
    """The next date that is >= today for this event."""
    today = date.today()
    if not annual:
        return d
    # annual: reuse month/day, roll forward if already passed this year
    try:
        candidate = date(today.year, d.month, d.day)
    except ValueError:  # Feb 29 on a non-leap year
        candidate = date(today.year, d.month, 28)
    if candidate < today:
        try:
            candidate = date(today.year + 1, d.month, d.day)
        except ValueError:
            candidate = date(today.year + 1, d.month, 28)
    return candidate


def _days_until(d: date, annual: bool) -> int:
    return (_next_occurrence(d, annual) - date.today()).days


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    name = (args or {}).get("name", "").strip()
    data = _load()

    if action in ("add", "save"):
        if not name:
            return "What should I call this event? Say 'add birthday June 5th'."
        d, annual = _parse_date((args or {}).get("date", ""))
        if not d:
            return (
                "I need a date. Say 'add my birthday June 5' (annual) or "
                "'add trip on 2026-12-20' (one-off)."
            )
        annual = annual or (args or {}).get("annual", False) in (True, "true", "yes", "1")
        data["events"][name] = {"date": d.isoformat(), "annual": bool(annual)}
        _save(data)
        days = _days_until(d, bool(annual))
        kind = "annual" if annual else "one-time"
        return f"Saved '{name}' ({kind}) - {days} days away."

    if action in ("days", "check", "until"):
        if not name:
            return "Which event? Say 'how many days until my birthday'."
        info = None
        for key, val in data["events"].items():
            if name.lower() in key.lower():
                info = val
                name = key
                break
        if not info:
            return f"I don't have '{name}'. Say 'countdown list' to see your events."
        d = date.fromisoformat(info["date"])
        days = _days_until(d, info["annual"])
        if days == 0:
            return f"{name} is today!"
        return f"{days} day{'s' if days != 1 else ''} until {name}."

    if action in ("list", "all", "next"):
        if not data["events"]:
            return "No saved events. Say 'add my birthday June 5'."
        rows = []
        for key, val in data["events"].items():
            d = date.fromisoformat(val["date"])
            rows.append((_days_until(d, val["annual"]), key, d, val["annual"]))
        rows.sort()
        if action == "next":
            days, key, d, annual = rows[0]
            return (f"Next up: {key} in {days} day(s)"
                    + (" (repeats yearly)" if annual else "") + ".")
        lines = ["Your events:"]
        today = date.today()
        for days, key, d, annual in rows:
            stamp = "today" if days == 0 else f"in {days} day(s)"
            yearly = " [yearly]" if annual else ""
            lines.append(f"  {key}: {stamp}{yearly} ({d.isoformat()})")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("COUNTDOWNS", result)
            except Exception:
                pass
        return result

    if action in ("remove", "delete"):
        if not name:
            return "Which event should I remove?"
        for key in list(data["events"]):
            if name.lower() in key.lower():
                del data["events"][key]
                _save(data)
                return f"Removed '{key}'."
        return f"No event called '{name}'."

    return (
        "Unknown countdown action. Try: add, days, list, next, remove."
    )


PLUGIN = {
    "name": "countdown",
    "description": (
        "Upcoming dates and countdowns. Use when the user asks how many "
        "days until something ('how many days until my birthday', 'when is "
        "my trip'), adds an event ('add my birthday June 5', 'add trip on "
        "2026-12-20'), or wants the soonest upcoming event. Birthdays and "
        "anniversaries recur yearly automatically."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | days | list | next | remove",
            },
            "name": {
                "type": "STRING",
                "description": "Event name, e.g. 'my birthday', 'trip to Tokyo'",
            },
            "date": {
                "type": "STRING",
                "description": "Date as MM-DD for yearly events (e.g. '06-05') or YYYY-MM-DD for one-time",
            },
            "annual": {
                "type": "BOOLEAN",
                "description": "True if the event repeats every year (default: auto from date format)",
            },
        },
        "required": ["action"],
    },
}
