"""
MARK XLIX — Quiz Mode plugin

Jarvis quizzes the user from a built-in question bank. State is persisted so
questions/answers/score survive across turns.
Tool name: quiz_mode
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

_TOPICS = {
    "general": [
        {"q": "What is the capital of France?", "options": ["London", "Paris", "Berlin", "Madrid"], "answer": 1, "why": "Paris is the capital of France."},
        {"q": "How many continents are there on Earth?", "options": ["5", "6", "7", "8"], "answer": 2, "why": "There are seven continents."},
        {"q": "Which planet is known as the Red Planet?", "options": ["Venus", "Mars", "Jupiter", "Saturn"], "answer": 1, "why": "Mars is the Red Planet."},
        {"q": "How many colors are in a rainbow?", "options": ["5", "6", "7", "8"], "answer": 2, "why": "ROYGBIV — seven colors."},
        {"q": "What is the largest ocean on Earth?", "options": ["Atlantic", "Indian", "Arctic", "Pacific"], "answer": 3, "why": "The Pacific is the largest ocean."},
        {"q": "Which animal is known as the King of the Jungle?", "options": ["Tiger", "Elephant", "Lion", "Bear"], "answer": 2, "why": "The lion is called the King of the Jungle."},
        {"q": "How many days are in a leap year?", "options": ["365", "366", "364", "360"], "answer": 1, "why": "A leap year has 366 days."},
        {"q": "What gas do plants absorb from the air?", "options": ["Oxygen", "Nitrogen", "Carbon dioxide", "Hydrogen"], "answer": 2, "why": "Plants absorb carbon dioxide."},
    ],
    "science": [
        {"q": "What is the chemical symbol for water?", "options": ["H2O", "CO2", "O2", "NaCl"], "answer": 0, "why": "Water is H2O."},
        {"q": "What force pulls objects toward Earth?", "options": ["Magnetism", "Friction", "Gravity", "Inertia"], "answer": 2, "why": "Gravity pulls objects down."},
        {"q": "What is the speed of light approximately?", "options": ["300,000 km/s", "150,000 km/s", "1,000,000 km/s", "30,000 km/s"], "answer": 0, "why": "Light travels about 300,000 km per second."},
        {"q": "Which organ pumps blood around the body?", "options": ["Lungs", "Brain", "Heart", "Liver"], "answer": 2, "why": "The heart pumps blood."},
        {"q": "What is the hardest natural substance?", "options": ["Gold", "Iron", "Diamond", "Quartz"], "answer": 2, "why": "Diamond is the hardest natural substance."},
        {"q": "How many elements are in the periodic table?", "options": ["92", "100", "118", "120"], "answer": 2, "why": "There are 118 confirmed elements."},
        {"q": "What do bees produce?", "options": ["Milk", "Honey", "Wax only", "Silk"], "answer": 1, "why": "Bees produce honey."},
        {"q": "Which vitamin does sunlight give us?", "options": ["Vitamin A", "Vitamin B12", "Vitamin C", "Vitamin D"], "answer": 3, "why": "Sunlight helps produce vitamin D."},
    ],
    "geography": [
        {"q": "What is the longest river in the world?", "options": ["Amazon", "Nile", "Yangtze", "Mississippi"], "answer": 1, "why": "The Nile is generally considered the longest river."},
        {"q": "Which country has the largest population?", "options": ["USA", "China", "India", "Brazil"], "answer": 2, "why": "India currently has the largest population."},
        {"q": "What is the smallest country in the world?", "options": ["Monaco", "Vatican City", "Malta", "San Marino"], "answer": 1, "why": "Vatican City is the smallest country."},
        {"q": "Which desert is the largest hot desert?", "options": ["Gobi", "Sahara", "Kalahari", "Atacama"], "answer": 1, "why": "The Sahara is the largest hot desert."},
        {"q": "Mount Everest is in which mountain range?", "options": ["Andes", "Alps", "Himalayas", "Rockies"], "answer": 2, "why": "Everest is in the Himalayas."},
        {"q": "Which country is shaped like a boot?", "options": ["Greece", "Spain", "Italy", "Portugal"], "answer": 2, "why": "Italy is shaped like a boot."},
        {"q": "What is the capital of Japan?", "options": ["Osaka", "Kyoto", "Tokyo", "Hiroshima"], "answer": 2, "why": "Tokyo is the capital of Japan."},
        {"q": "Which is the largest island in the world?", "options": ["Madagascar", "Borneo", "Greenland", "Sumatra"], "answer": 2, "why": "Greenland is the largest island."},
    ],
    "history": [
        {"q": "Who was the first president of the United States?", "options": ["Lincoln", "Washington", "Jefferson", "Adams"], "answer": 1, "why": "George Washington was the first US president."},
        {"q": "In which year did World War 2 end?", "options": ["1943", "1944", "1945", "1946"], "answer": 2, "why": "World War 2 ended in 1945."},
        {"q": "Who painted the Mona Lisa?", "options": ["Michelangelo", "Raphael", "Van Gogh", "Leonardo da Vinci"], "answer": 3, "why": "Leonardo da Vinci painted the Mona Lisa."},
        {"q": "The Titanic sank in which year?", "options": ["1905", "1912", "1918", "1920"], "answer": 1, "why": "The Titanic sank in 1912."},
        {"q": "Who was the first man on the moon?", "options": ["Buzz Aldrin", "Yuri Gagarin", "Neil Armstrong", "John Glenn"], "answer": 2, "why": "Neil Armstrong was first on the moon."},
        {"q": "The Great Wall is in which country?", "options": ["Japan", "India", "China", "Korea"], "answer": 2, "why": "The Great Wall is in China."},
        {"q": "Which empire was ruled by Julius Caesar?", "options": ["Greek", "Roman", "Egyptian", "Persian"], "answer": 1, "why": "Julius Caesar led the Roman Empire."},
        {"q": "The Eiffel Tower was built in which city?", "options": ["Rome", "Paris", "London", "Vienna"], "answer": 1, "why": "The Eiffel Tower is in Paris."},
    ],
    "technology": [
        {"q": "Who co-founded Apple with Steve Jobs?", "options": ["Bill Gates", "Steve Wozniak", "Elon Musk", "Mark Zuckerberg"], "answer": 1, "why": "Steve Wozniak co-founded Apple."},
        {"q": "What does CPU stand for?", "options": ["Central Processing Unit", "Computer Power Unit", "Core Processor Unit", "Central Program Utility"], "answer": 0, "why": "CPU is the Central Processing Unit."},
        {"q": "Which company makes the iPhone?", "options": ["Samsung", "Google", "Apple", "Sony"], "answer": 2, "why": "Apple makes the iPhone."},
        {"q": "What does RAM stand for?", "options": ["Random Access Memory", "Read And Memory", "Rapid Access Module", "Random Allocation Memory"], "answer": 0, "why": "RAM is Random Access Memory."},
        {"q": "WWW stands for…", "options": ["World Wide Web", "World Web Wide", "Web World Wide", "Wide Web World"], "answer": 0, "why": "WWW is the World Wide Web."},
        {"q": "Which search engine was created by Larry Page and Sergey Brin?", "options": ["Bing", "Google", "Yahoo", "DuckDuckGo"], "answer": 1, "why": "Google was created by Page and Brin."},
        {"q": "What is the most widely used operating system on personal computers?", "options": ["Linux", "macOS", "Windows", "Android"], "answer": 2, "why": "Windows dominates the PC market."},
        {"q": "What does AI stand for?", "options": ["Automated Interface", "Artificial Intelligence", "Advanced Internet", "Automatic Input"], "answer": 1, "why": "AI is Artificial Intelligence."},
    ],
    "programming": [
        {"q": "Which language is known for snake emojis and is great for AI?", "options": ["Java", "C++", "Python", "Go"], "answer": 2, "why": "Python is widely used for AI."},
        {"q": "What does HTML stand for?", "options": ["Hyper Text Markup Language", "High Tech Modern Language", "Hyper Transfer Markup Language", "Home Tool Markup Language"], "answer": 0, "why": "HTML is Hyper Text Markup Language."},
        {"q": "Which of these is a version control system?", "options": ["Git", "Docker", "Nginx", "MySQL"], "answer": 0, "why": "Git is a version control system."},
        {"q": "What symbol comments a line in Python?", "options": ["//", "#", "--", "/*"], "answer": 1, "why": "# starts a comment in Python."},
        {"q": "What does CSS style?", "options": ["Databases", "Web pages", "Servers", "Compilers"], "answer": 1, "why": "CSS styles web pages."},
        {"q": "Which company created the JavaScript language?", "options": ["Microsoft", "Apple", "Netscape", "IBM"], "answer": 2, "why": "JavaScript was created at Netscape."},
        {"q": "What is the output of 2 + 2 in Python?", "options": ["22", "4", "2+2", "Error"], "answer": 1, "why": "2 + 2 equals 4."},
        {"q": "What does SQL stand for?", "options": ["Structured Query Language", "Simple Query Language", "System Query Logic", "Standard Question Language"], "answer": 0, "why": "SQL is Structured Query Language."},
    ],
}

PLUGIN = {
    "name": "quiz_mode",
    "description": (
        "Runs a spoken quiz. Use when the user wants to be quizzed or tested "
        "on a topic. Start with a topic (general, science, geography, history, "
        "technology, programming), then grade their answers one at a time."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | answer | score | end | topics",
            },
            "topic": {
                "type": "STRING",
                "description": "Quiz topic for 'start': general | science | geography | history | technology | programming"
            },
            "count": {
                "type": "INTEGER",
                "description": "Number of questions (default: 5)"
            },
            "answer": {
                "type": "STRING",
                "description": "User's answer for the 'answer' action: the option number, letter, or the text"
            },
        },
        "required": ["action"],
    },
}


def _state_path() -> Path:
    return Path(DATA_DIR) / "quiz_state.json"


def _load_state() -> dict:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        st = {}
    return st


def _save_state(st: dict) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _state_path().write_text(
            json.dumps(st, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _resolve_topic(topic: str) -> str | None:
    t = (topic or "").strip().lower()
    for name in _TOPICS:
        if t == name or t in name or name.startswith(t):
            return name
    return None


def _fmt_question(q: dict, idx: int, total: int) -> str:
    lines = [f"Question {idx} of {total}: {q['q']}"]
    for i, opt in enumerate(q["options"], 1):
        lines.append(f"{i}. {opt}")
    lines.append("Say the number, or just the answer.")
    return " ".join(lines)


def _grade(actual: str, user_answer: str) -> bool:
    u = (user_answer or "").strip().lower()
    correct_idx = actual["answer"]
    letters = "abcd"
    if u.isdigit() and 1 <= int(u) <= len(actual["options"]):
        return int(u) - 1 == correct_idx
    if u in letters and letters.index(u) == correct_idx:
        return True
    return u == actual["options"][correct_idx].lower()


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    st = _load_state()

    if action == "topics":
        return "Quiz topics: " + ", ".join(sorted(_TOPICS)) + ". Say 'start a quiz on science'."

    if action == "start":
        topic = _resolve_topic((args or {}).get("topic"))
        if not topic:
            return (
                f"I don't have questions for that topic. I can quiz you on: "
                + ", ".join(sorted(_TOPICS)) + "."
            )
        try:
            count = max(1, min(10, int((args or {}).get("count") or 5)))
        except (TypeError, ValueError):
            count = 5
        bank = _TOPICS[topic][:count]
        st = {
            "topic": topic,
            "total": len(bank),
            "questions": bank,
            "idx": 0,
            "score": 0,
            "asked": 0,
            "created": datetime.now().isoformat(),
        }
        _save_state(st)
        result = f"Quiz started: {topic}, {st['total']} questions. " + _fmt_question(bank[0], 1, st["total"])
        if ui and hasattr(ui, "write_log"):
            try:
                ui.write_log(f"[Quiz] Started — {topic}")
            except Exception:
                pass
        return result

    if action == "answer":
        if not st or not st.get("questions"):
            return "No active quiz. Say 'start a quiz on science' first."
        idx = st["idx"]
        if idx >= st["total"]:
            return "The quiz is already finished. Say 'score' for your results or 'end' to quit."
        q = st["questions"][idx]
        user_ans = (args or {}).get("answer", "")
        if not user_ans.strip():
            return _fmt_question(q, idx + 1, st["total"])
        if user_ans.strip().lower() in ("skip", "pass", "next"):
            st["asked"] += 1
            st["idx"] += 1
            msg = f"Alright, skipping. The answer was: {q['options'][q['answer']]}. {q['why']}"
        else:
            correct = _grade(q, user_ans)
            st["asked"] += 1
            if correct:
                st["score"] += 1
                msg = f"Correct! {q['why']}"
            else:
                msg = f"Not quite. The answer is {q['options'][q['answer']]}. {q['why']}"
            st["idx"] += 1
        _save_state(st)
        if st["idx"] < st["total"]:
            return msg + " " + _fmt_question(st["questions"][st["idx"]], st["idx"] + 1, st["total"])
        return (
            f"{msg} Quiz finished! You scored {st['score']} out of {st['total']}."
        )

    if action == "score":
        if not st or not st.get("questions"):
            return "No active quiz right now."
        return f"Current score: {st['score']} out of {st.get('asked', 0)} asked, {st['total']} total questions."

    if action in ("end", "stop", "quit", "cancel"):
        if st and st.get("questions"):
            summary = f"Quiz ended. Final score: {st['score']} out of {st['total']}."
            _save_state({})
            return summary
        return "There's no active quiz to end."

    return "Unknown quiz action. Try: start, answer, score, end, topics."
