"""
MARK XLIX — Notes & Lists plugin

Voice memos, to-do lists and grocery lists stored locally in memory/notes_store.json.
Tool name: notes
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

PLUGIN = {
    "name": "notes",
    "description": (
        "Manages voice notes and to-do lists. Use for: saving a note, "
        "'remember to do X' lists, reading notes back, deleting notes, "
        "adding/listing/checking off to-do items, clearing lists."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "add | list | read | delete | todo_add | todo_list | "
                    "todo_done | todo_clear | todo_remove"
                ),
            },
            "title": {
                "type": "STRING",
                "description": "Note title (or to-do item text for todo_add/todo_done/todo_remove)",
            },
            "text": {
                "type": "STRING",
                "description": "Body of the note for add/read"
            },
            "list": {
                "type": "STRING",
                "description": "Optional list name for todo actions: todo | grocery | shopping | ideas (default: todo)"
            },
        },
        "required": ["action"],
    },
}


def _store_path() -> Path:
    return Path(DATA_DIR) / "notes_store.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("notes", {})
    data.setdefault("lists", {})   # list name -> [{"text", "done"}]
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _norm_list(name: str) -> str:
    n = (name or "").strip().lower()
    return n if n in ("todo", "grocery", "shopping", "ideas", "tasks") else "todo"


def _short_title(text: str) -> str:
    words = (text or "").split()
    return " ".join(words[:6]).title()[:60] or f"Note {datetime.now():%H:%M}"


def _fmt_lists(data: dict) -> str:
    out = []
    for list_name, items in data["lists"].items():
        if not items:
            continue
        out.append(f"{list_name.title()} list:")
        for i, it in enumerate(items, 1):
            mark = "✔" if it.get("done") else "•"
            out.append(f"  {i}. {mark} {it.get('text', '')}")
    return "\n".join(out)


def _fmt_notes(data: dict) -> str:
    if not data["notes"]:
        return "You have no saved notes yet."
    lines = ["Your notes:"]
    for title, body in data["notes"].items():
        lines.append(f"• {title} — {(body or '')[:80]}")
    return "\n".join(lines)


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    title  = (args or {}).get("title", "").strip()
    text   = (args or {}).get("text", "").strip()
    list_name = _norm_list((args or {}).get("list"))

    data = _load()

    if action in ("add", "save"):
        if not text:
            return "What should the note say? I need some text to save."
        key = title or _short_title(text)
        data["notes"][key] = text
        _save(data)
        return f"Note saved under '{key}'."

    if action == "list":
        result = _fmt_notes(data) + ("\n\n" + _fmt_lists(data) if _fmt_lists(data) else "")
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("NOTES & LISTS", result)
            except Exception:
                pass
        return result

    if action == "read":
        if not title:
            return "Which note do you want me to read? Give me the title."
        for key, body in data["notes"].items():
            if title.lower() in key.lower():
                return f"'{key}': {body}"
        return f"I couldn't find a note called '{title}'. Say 'list notes' to see what you have."

    if action == "delete":
        if not title:
            return "Which note should I delete?"
        for key in list(data["notes"]):
            if title.lower() in key.lower():
                del data["notes"][key]
                _save(data)
                return f"Deleted note '{key}'."
        return f"No note called '{title}' found."

    if action in ("todo_add", "add_todo"):
        if not title:
            return "What should I add to the list?"
        data["lists"].setdefault(list_name, []).append({"text": title, "done": False})
        _save(data)
        return f"Added '{title}' to your {list_name} list."

    if action == "todo_list":
        items = data["lists"].get(list_name, [])
        if not items:
            return f"Your {list_name} list is empty."
        lines = [f"{list_name.title()} list:"]
        for i, it in enumerate(items, 1):
            mark = "✔" if it.get("done") else "•"
            lines.append(f"{i}. {mark} {it.get('text', '')}")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content(f"{list_name.upper()} LIST", result)
            except Exception:
                pass
        return result

    if action in ("todo_done", "todo_check", "done"):
        items = data["lists"].get(list_name, [])
        target = title.strip()
        idx = None
        try:
            idx = int(target) - 1
        except ValueError:
            idx = None
        for i, it in enumerate(items):
            if idx == i or (target and target.lower() in it.get("text", "").lower()):
                it["done"] = True
                _save(data)
                return f"Checked off '{it['text']}'."
        return f"I couldn't find '{title}' on your {list_name} list."

    if action in ("todo_remove", "remove"):
        items = data["lists"].get(list_name, [])
        target = title.strip()
        try:
            idx = int(target) - 1
        except ValueError:
            idx = None
        for i, it in enumerate(items):
            if idx == i or (target and target.lower() in it.get("text", "").lower()):
                removed = items.pop(i)
                _save(data)
                return f"Removed '{removed['text']}' from your {list_name} list."
        return f"I couldn't find '{title}' on your {list_name} list."

    if action in ("todo_clear", "clear"):
        data["lists"][list_name] = []
        _save(data)
        return f"Cleared your {list_name} list."

    return (
        "Unknown notes action. Try: add, list, read, delete, todo_add, "
        "todo_list, todo_done, todo_clear."
    )
