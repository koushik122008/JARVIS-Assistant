"""
MARK XLIX — Flashcards plugin

Build flashcard decks and review them with a simple spaced-repetition
schedule (SM-2 style). Stored locally in memory/flashcards.json.
Tool name: flashcards
"""

import json
import threading
from datetime import date, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()


def _store_path() -> Path:
    return Path(DATA_DIR) / "flashcards.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("decks", {})      # deck -> [{"front","back","interval","ease","due","reps","lapses"}]
    data.setdefault("session", None)  # {"deck","queue":[idx...],"pos","correct","wrong"}
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


def _norm_deck(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_") or "general"


def _fmt_card(card: dict, idx: int, total: int) -> str:
    return (
        f"Card {idx + 1} of {total}. "
        f"{card['front']} (say 'answer <your answer>')"
    )


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    data = _load()

    if action in ("add", "new"):
        front = (args or {}).get("front", "").strip()
        back = (args or {}).get("back", "").strip()
        deck = _norm_deck((args or {}).get("deck"))
        if not front or not back:
            return "I need both a front and a back. Say 'add flashcard: front is Q, back is A'."
        data["decks"].setdefault(deck, []).append({
            "front": front,
            "back": back,
            "interval": 0,       # days
            "ease": 2.5,
            "due": _today(),
            "reps": 0,
            "lapses": 0,
        })
        _save(data)
        total = len(data["decks"][deck])
        return f"Added to deck '{deck}'. That deck now has {total} cards."

    if action in ("decks", "list"):
        if not data["decks"]:
            return "You have no decks yet. Say 'add flashcard' to create one."
        lines = ["Your decks:"]
        for deck, cards in data["decks"].items():
            due = sum(1 for c in cards if c["due"] <= _today())
            lines.append(f"  {deck}: {len(cards)} cards ({due} due)")
        result = "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("FLASHCARD DECKS", result)
            except Exception:
                pass
        return result

    if action in ("delete", "remove"):
        deck = _norm_deck((args or {}).get("deck"))
        if deck not in data["decks"]:
            return f"No deck named '{deck}'."
        del data["decks"][deck]
        if data.get("session") and data["session"].get("deck") == deck:
            data["session"] = None
        _save(data)
        return f"Deleted deck '{deck}'."

    if action in ("start", "review", "quiz"):
        deck = _norm_deck((args or {}).get("deck"))
        cards = data["decks"].get(deck)
        if not cards:
            return f"No deck named '{deck}'. Say 'flashcards list' to see your decks."
        due = [i for i, c in enumerate(cards) if c["due"] <= _today()]
        if not due:
            return f"Nothing due in '{deck}' - all caught up. Add new cards or pick another deck."
        data["session"] = {"deck": deck, "queue": due, "pos": 0, "correct": 0, "wrong": 0}
        _save(data)
        idx = data["session"]["queue"][0]
        return _fmt_card(cards[idx], 0, len(due))

    if action in ("answer", "check"):
        session = data.get("session")
        if not session:
            return "No active review. Say 'flashcards review <deck>' to start one."
        deck = session["deck"]
        cards = data["decks"].get(deck, [])
        q = session["queue"]
        pos = session["pos"]
        if pos >= len(q):
            return "Review already finished. Say 'flashcards stats' for your result."
        card = cards[q[pos]]
        user = ((args or {}).get("answer") or "").strip().lower()
        correct = user and (user == card["back"].lower()
                            or card["back"].lower() in user
                            or user in card["back"].lower())
        if correct:
            session["correct"] += 1
            # spaced repetition: shorten the interval the first time
            if card["reps"] == 0:
                card["interval"] = 1
            else:
                card["interval"] = max(1, int(card["interval"] * card["ease"]))
            card["ease"] = min(3.0, card["ease"] + 0.05)
            reply = f"Correct! The answer is {card['back']}."
        else:
            session["wrong"] += 1
            card["lapses"] += 1
            card["interval"] = 0
            card["ease"] = max(1.3, card["ease"] - 0.2)
            reply = f"Not quite. The answer is {card['back']}."
        card["reps"] += 1
        card["due"] = (date.today() + timedelta(days=card["interval"])).isoformat()
        session["pos"] += 1
        _save(data)

        if session["pos"] >= len(q):
            total = len(q)
            right = session["correct"]
            reply += (f" Review finished: {right} of {total} correct "
                      f"({round(100 * right / total) if total else 0}%). "
                      "Say 'flashcards review <deck>' to keep going.")
            data["session"] = None
        else:
            reply += " " + _fmt_card(cards[q[session["pos"]]], session["pos"], len(q))
        return reply

    if action in ("stats", "score"):
        session = data.get("session")
        if session and session["pos"] >= len(session["queue"]):
            data["session"] = None
            session = None
        if not session:
            total_cards = sum(len(c) for c in data["decks"].values())
            due_cards = sum(1 for c in data["decks"].values() for cc in c if cc["due"] <= _today())
            return (f"You have {total_cards} cards across {len(data['decks'])} decks, "
                    f"{due_cards} due today.")
        total = len(session["queue"])
        return (f"In-progress review: {session['correct']} correct, {session['wrong']} wrong, "
                f"{total - session['pos']} cards left.")

    return (
        "Unknown flashcards action. Try: add, decks, delete, review, answer, stats."
    )

PLUGIN = {
    "name": "flashcards",
    "description": (
        "Spaced-repetition flashcards. Use when the user creates a flashcard "
        "('add flashcard: front is the capital of France, back is Paris'), "
        "lists decks, starts a review session ('flashcards review french'), "
        "answers a card ('answer Paris'), or asks for stats. Stores decks locally."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | decks | delete | review | answer | stats",
            },
            "deck": {
                "type": "STRING",
                "description": "Deck name, e.g. 'french', 'python', 'geography' (default: general)",
            },
            "front": {
                "type": "STRING",
                "description": "Question side of the card",
            },
            "back": {
                "type": "STRING",
                "description": "Answer side of the card",
            },
            "answer": {
                "type": "STRING",
                "description": "User's answer during a review session",
            },
        },
        "required": ["action"],
    },
}
