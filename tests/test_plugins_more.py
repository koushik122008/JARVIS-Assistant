"""
MARK XLIX — Tests for the second plugin batch

Covers: habit tracker, expense tracker, flashcards, pomodoro,
watchlist, countdown, workout log, and bill split calculator.
"""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from plugins import PLUGIN_TOOLS
from plugins import (
    habit, expenses, flashcards, pomodoro, watchlist, countdown, workout, billsplit,
)


def _fake_ctx():
    return {"ui": None, "speak": None}


class TestDiscovery(unittest.TestCase):
    def test_new_plugins_loaded(self):
        names = {t["name"] for t in PLUGIN_TOOLS}
        self.assertTrue(
            {"habit_tracker", "expense_tracker", "flashcards", "pomodoro",
             "watchlist", "countdown", "workout_log", "bill_split"} <= names
        )


class _TmpDataMixin:
    """Point a plugin's DATA_DIR at a temp folder for the duration of a test."""

    def _setup(self, mod):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = mod.DATA_DIR
        mod.DATA_DIR = Path(self._tmp.name)
        return mod

    def _teardown(self, mod):
        self._tmp.cleanup()
        mod.DATA_DIR = self._orig


class TestHabit(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(habit)

    def tearDown(self):
        self._teardown(habit)

    def test_add_log_streak(self):
        out = habit.handle({"action": "add", "habit": "drink water"}, _fake_ctx())
        self.assertIn("added", out.lower())

        out = habit.handle({"action": "log", "habit": "drink water"}, _fake_ctx())
        self.assertIn("logged", out.lower())
        self.assertIn("streak", out.lower())

        out = habit.handle({"action": "log", "habit": "drink water"}, _fake_ctx())
        self.assertIn("already", out.lower())

        out = habit.handle({"action": "streak", "habit": "drink water"}, _fake_ctx())
        self.assertIn("1 day streak", out)

    def test_log_unknown_habit(self):
        out = habit.handle({"action": "log", "habit": "yoga"}, _fake_ctx())
        self.assertIn("don't track", out.lower())

    def test_overview_and_remove(self):
        habit.handle({"action": "add", "habit": "read"}, _fake_ctx())
        out = habit.handle({"action": "overview"}, _fake_ctx())
        self.assertIn("read", out.lower())

        out = habit.handle({"action": "remove", "habit": "read"}, _fake_ctx())
        self.assertIn("removed", out.lower())

    def test_unknown_action(self):
        out = habit.handle({"action": "frobnicate"}, _fake_ctx())
        self.assertIn("unknown habit action", out.lower())


class TestExpenses(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(expenses)

    def tearDown(self):
        self._teardown(expenses)

    def test_add_and_month_total(self):
        out = expenses.handle({"action": "add", "amount": 25, "description": "lunch", "category": "food"}, _fake_ctx())
        self.assertIn("$25.00", out)
        self.assertIn("food", out)

        out = expenses.handle({"action": "month"}, _fake_ctx())
        self.assertIn("$25.00", out)

    def test_bad_amount(self):
        out = expenses.handle({"action": "add", "amount": -5}, _fake_ctx())
        self.assertIn("positive", out.lower())

    def test_budget(self):
        out = expenses.handle({"action": "budget", "budget": 800}, _fake_ctx())
        self.assertIn("$800.00", out)

    def test_category_breakdown_and_delete_last(self):
        expenses.handle({"action": "add", "amount": 10, "category": "transport"}, _fake_ctx())
        expenses.handle({"action": "add", "amount": 20, "category": "food"}, _fake_ctx())
        out = expenses.handle({"action": "category"}, _fake_ctx())
        self.assertIn("transport", out)
        self.assertIn("food", out)

        out = expenses.handle({"action": "delete_last"}, _fake_ctx())
        self.assertIn("removed", out.lower())

    def test_unknown_action(self):
        out = expenses.handle({"action": "moon"}, _fake_ctx())
        self.assertIn("unknown expense action", out.lower())


class TestFlashcards(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(flashcards)

    def tearDown(self):
        self._teardown(flashcards)

    def test_add_and_review_flow(self):
        flashcards.handle({"action": "add", "deck": "geography", "front": "Capital of France", "back": "Paris"}, _fake_ctx())
        flashcards.handle({"action": "add", "deck": "geography", "front": "Capital of Italy", "back": "Rome"}, _fake_ctx())

        out = flashcards.handle({"action": "decks"}, _fake_ctx())
        self.assertIn("geography", out)
        self.assertIn("2 cards", out)

        out = flashcards.handle({"action": "review", "deck": "geography"}, _fake_ctx())
        self.assertIn("Card 1 of 2", out)

        out = flashcards.handle({"action": "answer", "answer": "Paris"}, _fake_ctx())
        self.assertIn("correct", out.lower())
        self.assertIn("Card 2 of 2", out)

    def test_answer_before_start(self):
        out = flashcards.handle({"action": "answer", "answer": "x"}, _fake_ctx())
        self.assertIn("no active review", out.lower())

    def test_unknown_deck(self):
        out = flashcards.handle({"action": "review", "deck": "nope"}, _fake_ctx())
        self.assertIn("no deck named", out.lower())

    def test_unknown_action(self):
        out = flashcards.handle({"action": "browse"}, _fake_ctx())
        self.assertIn("unknown flashcards action", out.lower())


class TestPomodoro(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(pomodoro)
        self._orig_now = pomodoro._now

    def tearDown(self):
        pomodoro._now = self._orig_now
        self._teardown(pomodoro)

    def test_start_status_stop(self):
        out = pomodoro.handle({"action": "start", "minutes": 25}, _fake_ctx())
        self.assertIn("25", out)

        out = pomodoro.handle({"action": "status"}, _fake_ctx())
        self.assertIn("focus", out.lower())
        self.assertIn("left", out)

        out = pomodoro.handle({"action": "stop"}, _fake_ctx())
        self.assertIn("stopped", out.lower())

    def test_auto_complete_records_session(self):
        start = datetime.now()
        pomodoro.handle({"action": "start", "minutes": 25}, _fake_ctx())
        # simulate time passing past the session
        pomodoro._now = lambda: start + timedelta(minutes=30)
        out = pomodoro.handle({"action": "today"}, _fake_ctx())
        self.assertIn("1 session", out)

    def test_break(self):
        out = pomodoro.handle({"action": "break", "minutes": 5}, _fake_ctx())
        self.assertIn("break", out.lower())

    def test_unknown_action(self):
        out = pomodoro.handle({"action": "spin"}, _fake_ctx())
        self.assertIn("unknown pomodoro action", out.lower())


class TestWatchlist(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(watchlist)

    def tearDown(self):
        self._teardown(watchlist)

    def test_add_list_update_pick(self):
        out = watchlist.handle({"action": "add", "title": "Dune", "kind": "book"}, _fake_ctx())
        self.assertIn("added", out.lower())

        out = watchlist.handle({"action": "add", "title": "The Bear", "kind": "show"}, _fake_ctx())
        self.assertIn("added", out.lower())

        out = watchlist.handle({"action": "list"}, _fake_ctx())
        self.assertIn("Dune", out)
        self.assertIn("The Bear", out)

        out = watchlist.handle({"action": "update", "title": "Dune", "status": "done"}, _fake_ctx())
        self.assertIn("done", out.lower())

        out = watchlist.handle({"action": "pick"}, _fake_ctx())
        self.assertIn("The Bear", out)

        out = watchlist.handle({"action": "stats"}, _fake_ctx())
        self.assertIn("2 items", out)

    def test_remove(self):
        watchlist.handle({"action": "add", "title": "Arrival", "kind": "movie"}, _fake_ctx())
        out = watchlist.handle({"action": "remove", "title": "Arrival"}, _fake_ctx())
        self.assertIn("removed", out.lower())

    def test_pick_empty(self):
        out = watchlist.handle({"action": "pick"}, _fake_ctx())
        self.assertIn("empty", out.lower())

    def test_unknown_action(self):
        out = watchlist.handle({"action": "sort"}, _fake_ctx())
        self.assertIn("unknown watchlist action", out.lower())


class TestCountdown(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(countdown)

    def tearDown(self):
        self._teardown(countdown)

    def test_add_and_days(self):
        future = (date.today() + timedelta(days=10)).isoformat()
        out = countdown.handle({"action": "add", "name": "trip to Tokyo", "date": future}, _fake_ctx())
        self.assertIn("saved", out.lower())

        out = countdown.handle({"action": "days", "name": "tokyo"}, _fake_ctx())
        self.assertIn("10 days", out)

    def test_annual_birthday(self):
        out = countdown.handle({"action": "add", "name": "my birthday", "date": "06-05"}, _fake_ctx())
        self.assertIn("saved", out.lower())

        out = countdown.handle({"action": "days", "name": "birthday"}, _fake_ctx())
        self.assertIn("day", out.lower())

    def test_list_and_next(self):
        future = (date.today() + timedelta(days=3)).isoformat()
        countdown.handle({"action": "add", "name": "exam", "date": future}, _fake_ctx())
        out = countdown.handle({"action": "next"}, _fake_ctx())
        self.assertIn("exam", out)

        out = countdown.handle({"action": "list"}, _fake_ctx())
        self.assertIn("exam", out)

    def test_unknown_event(self):
        out = countdown.handle({"action": "days", "name": "halloween"}, _fake_ctx())
        self.assertIn("don't have", out.lower())

    def test_bad_date(self):
        out = countdown.handle({"action": "add", "name": "x", "date": "someday"}, _fake_ctx())
        self.assertIn("date", out.lower())


class TestWorkout(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(workout)

    def tearDown(self):
        self._teardown(workout)

    def test_log_and_pr(self):
        out = workout.handle({"action": "log", "exercise": "bench press", "sets": 3, "reps": 5, "weight": 100}, _fake_ctx())
        self.assertIn("bench press", out.lower())
        self.assertIn("personal record", out.lower())

        out = workout.handle({"action": "log", "exercise": "bench press", "sets": 3, "reps": 5, "weight": 90}, _fake_ctx())
        self.assertNotIn("personal record", out.lower())

        out = workout.handle({"action": "pr", "exercise": "bench press"}, _fake_ctx())
        self.assertIn("100", out)

    def test_today_and_week(self):
        workout.handle({"action": "log", "exercise": "squat", "sets": 5, "reps": 5, "weight": 80}, _fake_ctx())
        out = workout.handle({"action": "today"}, _fake_ctx())
        self.assertIn("squat", out.lower())

        out = workout.handle({"action": "week"}, _fake_ctx())
        self.assertIn("squat", out.lower())

    def test_delete_last(self):
        workout.handle({"action": "log", "exercise": "curl", "sets": 3, "reps": 10}, _fake_ctx())
        out = workout.handle({"action": "delete_last"}, _fake_ctx())
        self.assertIn("removed", out.lower())

    def test_unknown_action(self):
        out = workout.handle({"action": "yoga"}, _fake_ctx())
        self.assertIn("unknown workout action", out.lower())


class TestBillSplit(unittest.TestCase):
    def test_equal_split(self):
        out = billsplit.handle({"action": "split", "total": 86.40, "people": 4}, _fake_ctx())
        self.assertIn("$21.60", out)

    def test_names_with_tip_and_tax(self):
        out = billsplit.handle(
            {"action": "split", "total": 50, "names": "Ana, Ben, Cal",
             "tax_percent": 10, "tip_percent": 20},
            _fake_ctx(),
        )
        self.assertIn("Ana", out)
        self.assertIn("Ben", out)
        self.assertIn("Cal", out)
        # grand = 50 * 1.10 * ... tax 10% on 50 = 5, tip 20% on 50 = 10 -> 65 / 3 = 21.67
        self.assertIn("$21.67", out)

    def test_skip_item(self):
        out = billsplit.handle(
            {"action": "split", "total": 50, "names": "Ana, Ben, Cal",
             "item_cost": 6, "skip": "Ana"},
            _fake_ctx(),
        )
        self.assertIn("skipped", out)
        # base = (50-6)/3 = 14.666 ; extra = 6/2 = 3 -> Ben & Cal pay 17.67, Ana 14.66
        self.assertIn("$17.67", out)
        self.assertIn("$14.66", out)

    def test_missing_total(self):
        out = billsplit.handle({"action": "split"}, _fake_ctx())
        self.assertIn("bill total", out.lower())

    def test_missing_people(self):
        out = billsplit.handle({"action": "split", "total": 20}, _fake_ctx())
        self.assertIn("how many people", out.lower())


if __name__ == "__main__":
    unittest.main()
