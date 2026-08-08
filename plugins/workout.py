"""
MARK XLIX — Workout Log plugin

Log exercises with sets/reps/weight, track weekly volume and personal
records. Stored locally in memory/workout_log.json.
Tool name: workout_log
"""

import json
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(DATA_DIR) / "workout_log.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("entries", [])   # {"date","exercise","sets","reps","weight","ts"}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _norm_exercise(name: str) -> str:
    return (name or "").strip().title() or "Exercise"


def _best_set(entry: dict) -> float:
    """Heaviest single set weight (None-safe)."""
    try:
        return float(entry.get("weight") or 0)
    except (TypeError, ValueError):
        return 0.0


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    data = _load()

    if action in ("log", "add"):
        exercise = _norm_exercise((args or {}).get("exercise"))
        try:
            sets = int((args or {}).get("sets", 1))
            reps = int((args or {}).get("reps", 0))
            weight = float((args or {}).get("weight", 0) or 0)
        except (TypeError, ValueError):
            return "Tell me sets, reps and weight, e.g. 'log 3 sets of 5 at 100 kilos bench press'."
        sets = max(1, sets)
        reps = max(0, reps)
        # previous best BEFORE appending - so same-day earlier sets count too
        best = max((_best_set(e) for e in data["entries"]
                    if e["exercise"] == exercise),
                   default=0)
        entry = {
            "date": date.today().isoformat(),
            "ts": datetime.now().strftime("%H:%M"),
            "exercise": exercise,
            "sets": sets,
            "reps": reps,
            "weight": round(weight, 2),
        }
        data["entries"].append(entry)
        _save(data)
        reply = f"Logged {sets} x {reps} {exercise}"
        if weight > 0:
            reply += f" at {weight:g} kg"
        if weight > best and weight > 0:
            reply += " - that's a personal record!"
        return reply + "."

    if action in ("today", "latest"):
        today = date.today().isoformat()
        entries = [e for e in data["entries"] if e["date"] == today]
        if not entries:
            return "Nothing logged today yet. Say 'log 3 sets of 5 bench press at 100 kilos'."
        lines = [f"Today's workout ({len(entries)} exercise(s)):"]
        for e in entries:
            wt = f" @ {e['weight']:g} kg" if e["weight"] else ""
            lines.append(f"  {e['exercise']}: {e['sets']} x {e['reps']}{wt}")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("WORKOUT TODAY", result)
            except Exception:
                pass
        return result

    if action in ("week", "weekly"):
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        entries = [e for e in data["entries"] if e["date"] >= week_ago]
        if not entries:
            return "No workouts in the last 7 days."
        by_day = {}
        total_sets = 0
        total_vol = 0.0
        for e in entries:
            by_day.setdefault(e["date"], []).append(e)
            total_sets += e["sets"]
            total_vol += e["sets"] * e["reps"] * e["weight"]
        result = (
            f"Last 7 days: {len(by_day)} workout day(s), {total_sets} sets, "
            f"volume {total_vol:,.0f} kg.\n"
        )
        day_lines = []
        for d, day in sorted(by_day.items(), reverse=True):
            parts = [f"{e['exercise']} {e['sets']}x{e['reps']}" for e in day]
            day_lines.append(f"  {d}: {', '.join(parts)}")
        result += "\n".join(day_lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("WORKOUT WEEK", result)
            except Exception:
                pass
        return result

    if action in ("pr", "record"):
        exercise = _norm_exercise((args or {}).get("exercise"))
        entries = [e for e in data["entries"] if e["exercise"] == exercise]
        if not entries:
            return f"No logged sets for {exercise} yet."
        heaviest = max(entries, key=_best_set)
        if heaviest["weight"] == 0:
            best_reps = max(entries, key=lambda e: e["reps"])
            return (f"{exercise}: best rep count is {best_reps['reps']} "
                    f"({best_reps['sets']} sets, {best_reps['date']}).")
        return (
            f"Personal record for {exercise}: {heaviest['weight']:g} kg "
            f"on {heaviest['date']} ({heaviest['sets']} x {heaviest['reps']})."
        )

    if action in ("history", "all"):
        if not data["entries"]:
            return "No workouts logged yet."
        lines = [f"Workout history ({len(data['entries'])} entries):"]
        for e in data["entries"][-15:][::-1]:
            wt = f" @ {e['weight']:g} kg" if e["weight"] else ""
            lines.append(f"  {e['date']} {e['exercise']}: {e['sets']} x {e['reps']}{wt}")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("WORKOUT HISTORY", result)
            except Exception:
                pass
        return result

    if action == "delete_last":
        if not data["entries"]:
            return "No workouts to remove."
        removed = data["entries"].pop()
        _save(data)
        return f"Removed {removed['exercise']} ({removed['sets']} x {removed['reps']})."

    return (
        "Unknown workout action. Try: log, today, week, pr, history, delete_last."
    )


PLUGIN = {
    "name": "workout_log",
    "description": (
        "Tracks workouts and exercises. Use when the user logs a workout "
        "('log 3 sets of 5 bench press at 100 kilos'), asks about today's "
        "or this week's training, wants a personal record for an exercise "
        "('what's my deadlift PR'), or reviews workout history."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "log | today | week | pr | history | delete_last",
            },
            "exercise": {
                "type": "STRING",
                "description": "Exercise name, e.g. 'bench press', 'deadlift', 'squat'",
            },
            "sets": {
                "type": "NUMBER",
                "description": "Number of sets",
            },
            "reps": {
                "type": "NUMBER",
                "description": "Reps per set",
            },
            "weight": {
                "type": "NUMBER",
                "description": "Weight in kilograms (0 or omitted for bodyweight)",
            },
        },
        "required": ["action"],
    },
}
