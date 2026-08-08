"""
MARK XLIX — Expense Tracker plugin

Voice-log spending, monthly and category totals, and a monthly budget.
Stored locally in memory/expenses.json.
Tool name: expense_tracker
"""

import json
import threading
from datetime import date, datetime
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

_CATEGORIES = {
    "food", "groceries", "transport", "rent", "bills", "entertainment",
    "shopping", "health", "travel", "education", "other",
}


def _store_path() -> Path:
    return Path(DATA_DIR) / "expenses.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("budget", {})     # "YYYY-MM" -> float
    data.setdefault("entries", [])    # {"amount", "desc", "cat", "date", "ts"}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _month() -> str:
    return date.today().strftime("%Y-%m")


def _norm_cat(cat: str) -> str:
    c = (cat or "").strip().lower()
    if not c:
        return "other"
    if c in _CATEGORIES:
        return c
    # fuzzy match
    for known in _CATEGORIES:
        if known.startswith(c) or c in known:
            return known
    return "other"


def _fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def _month_entries(data: dict, month: str) -> list:
    return [e for e in data["entries"] if e.get("date", "").startswith(month)]


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    data = _load()

    if action in ("add", "log", "spend"):
        try:
            amount = float((args or {}).get("amount", 0))
        except (TypeError, ValueError):
            return "How much was it? Say 'spent 25 on lunch' or 'log 12.50 groceries'."
        if amount <= 0:
            return "The amount needs to be a positive number."
        desc = (args or {}).get("description", "") or (args or {}).get("desc", "")
        cat = _norm_cat((args or {}).get("category", "other"))
        entry = {
            "amount": round(amount, 2),
            "desc": desc.strip(),
            "cat": cat,
            "date": date.today().isoformat(),
            "ts": datetime.now().strftime("%H:%M"),
        }
        data["entries"].append(entry)
        _save(data)
        reply = f"Logged {_fmt_money(amount)}"
        if desc:
            reply += f" for {desc.strip()}"
        reply += f" under {cat}."
        total = sum(e["amount"] for e in _month_entries(data, _month()))
        budget = data["budget"].get(_month())
        if budget:
            left = budget - total
            reply += f" {_fmt_money(total)} this month"
            reply += f", {_fmt_money(left)} of budget left." if left >= 0 else f", over budget by {_fmt_money(-left)}."
        return reply

    if action in ("today", "month", "total"):
        month = (args or {}).get("month") or _month()
        entries = _month_entries(data, month)
        total = sum(e["amount"] for e in entries)
        budget = data["budget"].get(month)
        if action == "today":
            today = date.today().isoformat()
            day_entries = [e for e in entries if e["date"] == today]
            day_total = sum(e["amount"] for e in day_entries)
            if not day_entries:
                return f"No spending logged today. You've spent {_fmt_money(total)} in {month}."
            result = f"Today: {_fmt_money(day_total)} in {len(day_entries)} purchase(s)."
            if ui and hasattr(ui, "show_content"):
                try:
                    ui.show_content("EXPENSES TODAY", "\n".join(
                        f"{e['ts']} {e['desc'] or e['cat']} - {_fmt_money(e['amount'])}"
                        for e in day_entries
                    ))
                except Exception:
                    pass
            return result
        if not entries:
            return f"No expenses logged in {month} yet. Say 'spent 20 on lunch'."
        result = f"Total for {month}: {_fmt_money(total)} across {len(entries)} entries."
        if budget:
            left = budget - total
            result += (f" Budget left: {_fmt_money(left)}." if left >= 0
                       else f" Over budget by {_fmt_money(-left)}.")
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content(f"EXPENSES {month}", "\n".join(
                    f"{e['date'][-5:]} {e['desc'] or e['cat']} ({e['cat']}) - {_fmt_money(e['amount'])}"
                    for e in entries[-20:]
                ))
            except Exception:
                pass
        return result

    if action in ("category", "categories", "breakdown"):
        entries = _month_entries(data, _month())
        if not entries:
            return "No expenses this month yet."
        by_cat = {}
        for e in entries:
            by_cat[e["cat"]] = by_cat.get(e["cat"], 0) + e["amount"]
        lines = [f"Spending by category ({_month()}):"]
        for cat, amt in sorted(by_cat.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {cat}: {_fmt_money(amt)}")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("EXPENSE CATEGORIES", result)
            except Exception:
                pass
        return result

    if action == "budget":
        try:
            budget = float((args or {}).get("budget", (args or {}).get("amount", 0)))
        except (TypeError, ValueError):
            return "Tell me the monthly budget, e.g. 'set a 800 dollar monthly budget'."
        if budget <= 0:
            return "The budget needs to be a positive number."
        month = (args or {}).get("month") or _month()
        data["budget"][month] = round(budget, 2)
        _save(data)
        return f"Monthly budget for {month} set to {_fmt_money(budget)}."

    if action in ("history", "recent"):
        entries = data["entries"]
        if not entries:
            return "No expenses logged yet."
        lines = [f"Recent expenses ({len(entries)} total):"]
        for e in entries[-15:][::-1]:
            lines.append(f"  {e['date']} {e['desc'] or e['cat']} - {_fmt_money(e['amount'])}")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("EXPENSE HISTORY", result)
            except Exception:
                pass
        return result

    if action == "delete_last":
        if not data["entries"]:
            return "No expenses to remove."
        removed = data["entries"].pop()
        _save(data)
        return f"Removed {_fmt_money(removed['amount'])} - {removed['desc'] or removed['cat']}."

    if action in ("categories_list", "list_categories"):
        return "Categories: " + ", ".join(sorted(_CATEGORIES))

    return (
        "Unknown expense action. Try: add, today, month, category, budget, "
        "history, delete_last."
    )


PLUGIN = {
    "name": "expense_tracker",
    "description": (
        "Tracks personal spending. Use when the user logs an expense "
        "('spent 25 on lunch', 'I paid 60 for groceries'), asks what they "
        "spent today/this month, asks for a category breakdown, sets a "
        "monthly budget, or reviews recent expenses. No external service."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | today | month | category | budget | history | delete_last | categories_list",
            },
            "amount": {
                "type": "NUMBER",
                "description": "Money amount for add or budget (e.g. 25, 12.5)",
            },
            "description": {
                "type": "STRING",
                "description": "What the money was for (e.g. 'lunch', 'taxi')",
            },
            "category": {
                "type": "STRING",
                "description": "food | groceries | transport | rent | bills | entertainment | shopping | health | travel | education | other (default: other)",
            },
            "budget": {
                "type": "NUMBER",
                "description": "Monthly budget amount for the 'budget' action",
            },
            "month": {
                "type": "STRING",
                "description": "Optional month key YYYY-MM (default: current month)",
            },
        },
        "required": ["action"],
    },
}
