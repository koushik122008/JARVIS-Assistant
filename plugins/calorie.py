"""
MARK XLIX — Calorie Counter plugin

Voice-log meals against a built-in food database (no external API).
Daily totals, macro breakdown, calorie goal, 7-day history.
Tool name: calorie_tracker
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

# Food database — values are per 100 g, with a typical serving size in grams.
_FOODS = {
    "egg":              {"kcal": 155, "protein": 13, "carbs": 1,  "fat": 11, "g": 50,  "per": "1 large egg"},
    "banana":           {"kcal": 89,  "protein": 1,  "carbs": 23, "fat": 0,  "g": 120, "per": "1 medium banana"},
    "apple":            {"kcal": 52,  "protein": 0,  "carbs": 14, "fat": 0,  "g": 180, "per": "1 medium apple"},
    "orange":           {"kcal": 47,  "protein": 1,  "carbs": 12, "fat": 0,  "g": 130, "per": "1 medium orange"},
    "grapes":           {"kcal": 69,  "protein": 1,  "carbs": 18, "fat": 0,  "g": 150, "per": "1 cup grapes"},
    "strawberries":     {"kcal": 32,  "protein": 1,  "carbs": 8,  "fat": 0,  "g": 150, "per": "1 cup strawberries"},
    "chicken breast":   {"kcal": 165, "protein": 31, "carbs": 0,  "fat": 4,  "g": 150, "per": "1 chicken breast"},
    "chicken":          {"kcal": 165, "protein": 31, "carbs": 0,  "fat": 4,  "g": 150, "per": "1 serving chicken"},
    "beef":             {"kcal": 250, "protein": 26, "carbs": 0,  "fat": 15, "g": 150, "per": "1 serving beef"},
    "salmon":           {"kcal": 208, "protein": 20, "carbs": 0,  "fat": 13, "g": 150, "per": "1 salmon fillet"},
    "tuna":             {"kcal": 132, "protein": 28, "carbs": 0,  "fat": 1,  "g": 100, "per": "1 can tuna"},
    "rice":             {"kcal": 130, "protein": 3,  "carbs": 28, "fat": 0,  "g": 150, "per": "1 cup cooked rice"},
    "brown rice":       {"kcal": 112, "protein": 2,  "carbs": 24, "fat": 1,  "g": 150, "per": "1 cup brown rice"},
    "pasta":            {"kcal": 158, "protein": 6,  "carbs": 31, "fat": 1,  "g": 150, "per": "1 cup cooked pasta"},
    "bread":            {"kcal": 265, "protein": 9,  "carbs": 49, "fat": 3,  "g": 60,  "per": "2 slices bread"},
    "toast":            {"kcal": 265, "protein": 9,  "carbs": 49, "fat": 3,  "g": 60,  "per": "2 slices toast"},
    "milk":             {"kcal": 42,  "protein": 3,  "carbs": 5,  "fat": 1,  "g": 250, "per": "1 glass milk"},
    "yogurt":           {"kcal": 61,  "protein": 10, "carbs": 5,  "fat": 0,  "g": 150, "per": "1 cup yogurt"},
    "greek yogurt":     {"kcal": 59,  "protein": 10, "carbs": 4,  "fat": 0,  "g": 150, "per": "1 cup greek yogurt"},
    "cheese":           {"kcal": 402, "protein": 25, "carbs": 3,  "fat": 33, "g": 40,  "per": "1 slice cheese"},
    "cheddar":          {"kcal": 402, "protein": 25, "carbs": 3,  "fat": 33, "g": 40,  "per": "1 slice cheddar"},
    "almonds":          {"kcal": 579, "protein": 21, "carbs": 22, "fat": 50, "g": 28,  "per": "1 handful almonds"},
    "walnuts":          {"kcal": 654, "protein": 15, "carbs": 14, "fat": 65, "g": 28,  "per": "1 handful walnuts"},
    "peanut butter":    {"kcal": 588, "protein": 25, "carbs": 20, "fat": 50, "g": 32,  "per": "2 tbsp peanut butter"},
    "oats":             {"kcal": 389, "protein": 17, "carbs": 66, "fat": 7,  "g": 40,  "per": "1 bowl oats"},
    "oatmeal":          {"kcal": 71,  "protein": 3,  "carbs": 12, "fat": 1,  "g": 250, "per": "1 bowl oatmeal"},
    "potato":           {"kcal": 77,  "protein": 2,  "carbs": 17, "fat": 0,  "g": 200, "per": "1 medium potato"},
    "sweet potato":     {"kcal": 86,  "protein": 2,  "carbs": 20, "fat": 0,  "g": 180, "per": "1 medium sweet potato"},
    "avocado":          {"kcal": 160, "protein": 2,  "carbs": 9,  "fat": 15, "g": 100, "per": "1/2 avocado"},
    "olive oil":        {"kcal": 884, "protein": 0,  "carbs": 0,  "fat": 100,"g": 14,  "per": "1 tbsp olive oil"},
    "butter":           {"kcal": 717, "protein": 1,  "carbs": 0,  "fat": 81, "g": 14,  "per": "1 tbsp butter"},
    "sugar":            {"kcal": 400, "protein": 0,  "carbs": 100,"fat": 0,  "g": 10,  "per": "2 tsp sugar"},
    "honey":            {"kcal": 304, "protein": 0,  "carbs": 82, "fat": 0,  "g": 21,  "per": "1 tbsp honey"},
    "chocolate":        {"kcal": 546, "protein": 5,  "carbs": 61, "fat": 31, "g": 30,  "per": "1 bar chocolate"},
    "pizza":            {"kcal": 266, "protein": 11, "carbs": 33, "fat": 10, "g": 250, "per": "2 slices pizza"},
    "burger":           {"kcal": 295, "protein": 17, "carbs": 24, "fat": 15, "g": 200, "per": "1 burger"},
    "french fries":     {"kcal": 312, "protein": 3,  "carbs": 41, "fat": 15, "g": 150, "per": "1 serving fries"},
    "noodles":          {"kcal": 138, "protein": 5,  "carbs": 25, "fat": 2,  "g": 200, "per": "1 bowl noodles"},
    "broccoli":         {"kcal": 34,  "protein": 3,  "carbs": 7,  "fat": 0,  "g": 150, "per": "1 cup broccoli"},
    "spinach":          {"kcal": 23,  "protein": 3,  "carbs": 4,  "fat": 0,  "g": 100, "per": "1 cup spinach"},
    "carrots":          {"kcal": 41,  "protein": 1,  "carbs": 10, "fat": 0,  "g": 130, "per": "1 cup carrots"},
    "tomato":           {"kcal": 18,  "protein": 1,  "carbs": 4,  "fat": 0,  "g": 150, "per": "1 medium tomato"},
    "cucumber":         {"kcal": 15,  "protein": 1,  "carbs": 4,  "fat": 0,  "g": 150, "per": "1 cup cucumber"},
    "salad":            {"kcal": 25,  "protein": 1,  "carbs": 4,  "fat": 0,  "g": 150, "per": "1 bowl salad"},
    "soup":             {"kcal": 50,  "protein": 2,  "carbs": 6,  "fat": 2,  "g": 250, "per": "1 bowl soup"},
    "coffee":           {"kcal": 1,   "protein": 0,  "carbs": 0,  "fat": 0,  "g": 240, "per": "1 cup coffee"},
    "tea":              {"kcal": 1,   "protein": 0,  "carbs": 0,  "fat": 0,  "g": 240, "per": "1 cup tea"},
    "orange juice":     {"kcal": 45,  "protein": 1,  "carbs": 10, "fat": 0,  "g": 250, "per": "1 glass orange juice"},
    "coke":             {"kcal": 42,  "protein": 0,  "carbs": 11, "fat": 0,  "g": 330, "per": "1 can coke"},
    "soda":             {"kcal": 42,  "protein": 0,  "carbs": 11, "fat": 0,  "g": 330, "per": "1 can soda"},
    "beer":             {"kcal": 43,  "protein": 0,  "carbs": 4,  "fat": 0,  "g": 355, "per": "1 bottle beer"},
    "wine":             {"kcal": 83,  "protein": 0,  "carbs": 3,  "fat": 0,  "g": 150, "per": "1 glass wine"},
}

PLUGIN = {
    "name": "calorie_tracker",
    "description": (
        "Tracks food and calories. Use when the user logs what they ate, "
        "asks how many calories they've had today, sets a calorie goal, or "
        "wants their calorie history. Has a built-in food database."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "log | today | history | goal | foods | delete_last",
            },
            "food": {
                "type": "STRING",
                "description": "Food name for log/foods (e.g. 'chicken breast', 'banana')",
            },
            "amount": {
                "type": "NUMBER",
                "description": "Amount eaten: grams if 'unit' is g/grams, otherwise servings of the standard portion",
            },
            "unit": {
                "type": "STRING",
                "description": "g | grams | serving | servings (default: grams if amount given, else 1 serving)"
            },
            "meal": {
                "type": "STRING",
                "description": "breakfast | lunch | dinner | snack (default: snack)"
            },
            "goal": {
                "type": "NUMBER",
                "description": "Daily calorie goal for the 'goal' action"
            },
            "query": {
                "type": "STRING",
                "description": "Search term for the 'foods' action"
            },
        },
        "required": ["action"],
    },
}


def _store_path() -> Path:
    return Path(DATA_DIR) / "calorie_log.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("goal", 2000)
    data.setdefault("days", {})
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _find_food(query: str):
    q = (query or "").strip().lower()
    if not q:
        return None
    # exact or substring match
    for name, info in _FOODS.items():
        if q == name or q in name or name in q:
            return name, info
    return None


def _suggest_foods(query: str, limit: int = 6) -> list:
    q = (query or "").strip().lower()
    if not q:
        return list(_FOODS)[:limit]
    return [name for name in _FOODS if q in name][:limit]


def _calc(info: dict, amount, unit: str) -> dict:
    """Scale a food entry to the eaten amount. Returns kcal/protein/carbs/fat."""
    grams = None
    if amount is not None:
        u = (unit or "").lower()
        if u in ("g", "gram", "grams"):
            grams = float(amount)
        elif u in ("serving", "servings", "portion", "portions"):
            grams = float(amount) * info["g"]
        else:
            # no unit → treat small numbers as servings, large as grams
            grams = float(amount) * info["g"] if float(amount) <= 3 else float(amount)
    else:
        grams = info["g"]

    f = grams / 100.0
    return {
        "grams": round(grams),
        "kcal": round(info["kcal"] * f),
        "protein": round(info["protein"] * f, 1),
        "carbs": round(info["carbs"] * f, 1),
        "fat": round(info["fat"] * f, 1),
    }


def handle(args: dict, ctx: dict) -> str:
    ui = (ctx or {}).get("ui")
    action = (args or {}).get("action", "").strip().lower()
    food_q = (args or {}).get("food", "").strip()
    meal   = (args or {}).get("meal", "").strip().lower() or "snack"
    if meal not in ("breakfast", "lunch", "dinner", "snack"):
        meal = "snack"
    data = _load()

    if action == "foods":
        matches = _suggest_foods((args or {}).get("query"))
        result = "Foods I know: " + ", ".join(matches)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("CALORIE FOOD DATABASE", result)
            except Exception:
                pass
        return result

    if action == "goal":
        try:
            goal = float((args or {}).get("goal", 2000))
        except (TypeError, ValueError):
            return "Tell me the daily goal in calories, e.g. 'set my goal to 2000'."
        data["goal"] = int(goal)
        _save(data)
        return f"Daily calorie goal set to {int(goal)} calories."

    if action == "log":
        match = _find_food(food_q)
        if not match:
            sugg = _suggest_foods(food_q)
            if sugg:
                return (
                    f"I don't know '{food_q}'. I know: {', '.join(sugg)}. "
                    "Try one of those, or tell me the calories directly."
                )
            return "I don't recognise that food yet. Try something like 'banana', 'chicken breast', or 'rice'."
        name, info = match
        entry = _calc(info, (args or {}).get("amount"), (args or {}).get("unit", ""))
        entry.update({"food": name, "meal": meal, "ts": datetime.now().strftime("%H:%M")})

        data["days"].setdefault(_today(), []).append(entry)
        _save(data)
        return (
            f"Logged {entry['grams']}g {name} for {meal} — "
            f"{entry['kcal']} calories, {entry['protein']}g protein."
        )

    if action == "delete_last":
        entries = data["days"].get(_today(), [])
        if not entries:
            return "Nothing logged today yet."
        removed = entries.pop()
        _save(data)
        return f"Removed {removed.get('food')} — {removed.get('kcal')} calories."

    if action == "today":
        entries = data["days"].get(_today(), [])
        if not entries:
            return (
                f"You've logged nothing today. Your goal is {data['goal']} calories. "
                "Tell me what you ate, like 'log 150 grams chicken breast for lunch'."
            )
        total = sum(e["kcal"] for e in entries)
        protein = round(sum(e["protein"] for e in entries), 1)
        remaining = data["goal"] - total
        by_meal = {}
        for e in entries:
            by_meal.setdefault(e["meal"], 0)
            by_meal[e["meal"]] += e["kcal"]
        meal_str = ", ".join(f"{m} {k}" for m, k in by_meal.items())
        rem_str = f"{remaining} remaining" if remaining >= 0 else f"{abs(remaining)} over your goal"
        result = (
            f"Today: {total} of {data['goal']} calories — {rem_str}. "
            f"Protein {protein}g. Breakdown: {meal_str}."
        )
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("CALORIES TODAY", "\n".join(
                    f"{e['ts']} {e['food']} ({e['grams']}g) — {e['kcal']} kcal"
                    for e in entries
                ))
            except Exception:
                pass
        return result

    if action == "history":
        lines = []
        for i in range(7):
            day = (datetime.now() - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            entries = data["days"].get(day, [])
            total = sum(e["kcal"] for e in entries)
            lines.append(f"{day}: {total} kcal" + (f" ({len(entries)} items)" if entries else " — nothing"))
        result = "Last 7 days:\n" + "\n".join(lines)
        if ui and hasattr(ui, "show_content"):
            try:
                ui.show_content("CALORIE HISTORY", result)
            except Exception:
                pass
        return result

    return (
        "Unknown calorie action. Try: log, today, history, goal, foods, delete_last."
    )
