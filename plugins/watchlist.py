"""
MARK XLIX — Watchlist plugin

Books, movies and shows with a status pipeline (want -> started -> done).
Stored locally in memory/watchlist.json. Includes a 'pick' action that
suggests something from the want list.
Tool name: watchlist
"""

import json
import random
import threading
from datetime import date
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

_KINDS = {"book", "movie", "show", "series", "anime", "game"}
_STATUSES = {"want", "started", "done"}


def _store_path() -> Path:
    return Path(DATA_DIR) / "watchlist.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("items", [])   # {"title","kind","status","added"}
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _norm_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if not k:
        return "book"
    if k in _KINDS:
        return k
    for known in _KINDS:
        if known.startswith(k) or k in known:
            return known
    return "book"


def _norm_status(status: str) -> str:
    s = (status or "").strip().lower()
    if not s:
        return "want"
    if s in _STATUSES:
        return s
    for known in _STATUSES:
        if known.startswith(s) or s in known:
            return known
    return "want"


def _find(data: dict, title: str):
    t = title.strip().lower()
    for item in data["items"]:
        if t == item["title"].lower() or t in item["title"].lower():
            return item
    return None


def _fmt_list(items: list) -> str:
    if not items:
        return "Nothing here yet."
    lines = []
    for i, it in enumerate(items, 1):
        mark = "[ ]"
        if it["status"] == "started":
            mark = "[>]"
        elif it["status"] == "done":
            mark = "[x]"
        lines.append(f"{i}. {mark} {it['title']} ({it['kind']})")
    return "\n".join(lines)


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    title = (args or {}).get("title", "").strip()
    data = _load()

    if action in ("add", "save"):
        if not title:
            return "What should I add? Say 'add Dune to my watchlist'."
        kind = _norm_kind((args or {}).get("kind"))
        status = _norm_status((args or {}).get("status", "want"))
        if _find(data, title):
            return f"'{title}' is already on your list."
        data["items"].append({
            "title": title,
            "kind": kind,
            "status": status,
            "added": date.today().isoformat(),
        })
        _save(data)
        return f"Added '{title}' ({kind}) to your list as '{status}'."

    if action in ("list", "all"):
        if not data["items"]:
            return "Your list is empty. Say 'add Dune to my watchlist'."
        kind = (args or {}).get("kind") or ""
        status = (args or {}).get("status") or ""
        items = data["items"]
        if kind:
            items = [i for i in items if i["kind"] == _norm_kind(kind)]
        if status:
            items = [i for i in items if i["status"] == _norm_status(status)]
        result = _fmt_list(items)
        label = "WATCHLIST" + (f" - {kind}" if kind else "") + (f" ({status})" if status else "")
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content(label, result)
            except Exception:
                pass
        return result

    if action in ("update", "status", "mark"):
        if not title:
            return "Which title should I update?"
        item = _find(data, title)
        if not item:
            return f"'{title}' isn't on your list. Say 'add {title}' first."
        new_status = _norm_status((args or {}).get("status", "started"))
        item["status"] = new_status
        _save(data)
        return f"'{item['title']}' is now '{new_status}'."

    if action in ("remove", "delete"):
        if not title:
            return "Which title should I remove?"
        item = _find(data, title)
        if not item:
            return f"'{title}' isn't on your list."
        data["items"].remove(item)
        _save(data)
        return f"Removed '{item['title']}' from your list."

    if action in ("pick", "suggest", "random"):
        want = [i for i in data["items"] if i["status"] == "want"]
        if not want:
            return ("Your 'want' list is empty. Say 'add something' first, "
                    "or 'watchlist list done' to see what you've finished.")
        item = random.choice(want)
        verb = "watch" if item["kind"] in ("movie", "show", "series", "anime") else "read"
        return f"How about {item['title']}? It's a {item['kind']} on your list. Say 'mark {item['title']} started' when you begin."

    if action in ("stats", "summary"):
        if not data["items"]:
            return "Your list is empty."
        by_kind = {}
        by_status = {}
        for i in data["items"]:
            by_kind[i["kind"]] = by_kind.get(i["kind"], 0) + 1
            by_status[i["status"]] = by_status.get(i["status"], 0) + 1
        return (
            f"{len(data['items'])} items total. "
            f"By kind: {', '.join(f'{k} {v}' for k, v in by_kind.items())}. "
            f"By status: {', '.join(f'{k} {v}' for k, v in by_status.items())}."
        )

    return (
        "Unknown watchlist action. Try: add, list, update, remove, pick, stats."
    )


PLUGIN = {
    "name": "watchlist",
    "description": (
        "Watchlist and reading list for books, movies, shows and games. "
        "Use when the user adds a title ('add Dune to my watchlist'), lists "
        "their list (optionally filtered by kind/status), updates status "
        "('mark Oppenheimer done', 'started watching The Bear'), asks what "
        "to watch or read tonight, or wants list statistics."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | list | update | remove | pick | stats",
            },
            "title": {
                "type": "STRING",
                "description": "Title of the book/movie/show, e.g. 'Dune', 'The Bear'",
            },
            "kind": {
                "type": "STRING",
                "description": "book | movie | show | series | anime | game (default: book)",
            },
            "status": {
                "type": "STRING",
                "description": "want | started | done (default for add: want; for update: started)",
            },
        },
        "required": ["action"],
    },
}
