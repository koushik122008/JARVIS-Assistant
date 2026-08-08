"""
MARK XLIX — Plugin system tests

Covers: plugin discovery, notes & lists, calorie tracker, quiz mode,
music control (dry-run only), and email configuration status.
"""

import tempfile
import unittest
from pathlib import Path

from plugins import PLUGIN_TOOLS, PLUGIN_HANDLERS
from plugins import notes, calorie, quiz, music, email as email_plugin


def _fake_ctx():
    return {"ui": None, "speak": None}


class TestDiscovery(unittest.TestCase):
    def test_all_plugins_loaded(self):
        names = {t["name"] for t in PLUGIN_TOOLS}
        self.assertTrue(
            {"music_control", "notes", "calorie_tracker", "quiz_mode",
             "email_assistant"} <= names
        )
        for n in names:
            self.assertIn(n, PLUGIN_HANDLERS)

    def test_no_collision_with_builtins(self):
        builtins = {
            "generate_image", "open_app", "web_search", "system_status",
            "weather_report", "send_message", "reminder", "youtube_video",
            "screen_process", "close_camera", "computer_settings",
            "browser_control", "file_controller", "desktop_control",
            "code_helper", "dev_agent", "computer_control", "game_updater",
            "flight_finder", "shutdown_jarvis", "file_processor",
            "currency_converter", "crypto_prices", "unit_converter", "alarm",
            "battery_info", "translate_text", "stock_prices", "set_timer",
            "save_memory",
        }
        plugin_names = {t["name"] for t in PLUGIN_TOOLS}
        self.assertFalse(plugin_names & builtins)


class TestNotes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        notes.DATA_DIR = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        from utils import BASE_DIR
        notes.DATA_DIR = BASE_DIR / "memory"

    def test_add_list_read_delete(self):
        out = notes.handle({"action": "add", "title": "grocery", "text": "milk and eggs"}, _fake_ctx())
        self.assertIn("saved", out.lower())

        out = notes.handle({"action": "list"}, _fake_ctx())
        self.assertIn("grocery", out)
        self.assertIn("milk and eggs", out)

        out = notes.handle({"action": "read", "title": "grocery"}, _fake_ctx())
        self.assertIn("milk and eggs", out)

        out = notes.handle({"action": "delete", "title": "grocery"}, _fake_ctx())
        self.assertIn("deleted", out.lower())

        out = notes.handle({"action": "list"}, _fake_ctx())
        self.assertIn("no saved notes", out)

    def test_todo_flow(self):
        notes.handle({"action": "todo_add", "title": "buy milk", "list": "todo"}, _fake_ctx())
        notes.handle({"action": "todo_add", "title": "call mom", "list": "todo"}, _fake_ctx())

        out = notes.handle({"action": "todo_list", "list": "todo"}, _fake_ctx())
        self.assertIn("buy milk", out)
        self.assertIn("call mom", out)

        out = notes.handle({"action": "todo_done", "title": "buy milk", "list": "todo"}, _fake_ctx())
        self.assertIn("checked off", out.lower())

        out = notes.handle({"action": "todo_remove", "title": "call mom", "list": "todo"}, _fake_ctx())
        self.assertIn("removed", out.lower())

        out = notes.handle({"action": "todo_clear", "list": "todo"}, _fake_ctx())
        self.assertIn("cleared", out.lower())


class TestCalorie(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        calorie.DATA_DIR = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        from utils import BASE_DIR
        calorie.DATA_DIR = BASE_DIR / "memory"

    def test_log_and_today(self):
        out = calorie.handle(
            {"action": "log", "food": "banana", "amount": 2, "unit": "servings", "meal": "breakfast"},
            _fake_ctx(),
        )
        self.assertIn("banana", out)
        self.assertIn("calories", out)

        out = calorie.handle({"action": "today"}, _fake_ctx())
        self.assertIn("calories", out)
        self.assertIn("remaining", out)

    def test_goal_and_history(self):
        out = calorie.handle({"action": "goal", "goal": 1800}, _fake_ctx())
        self.assertIn("1800", out)

        out = calorie.handle({"action": "history"}, _fake_ctx())
        self.assertIn("last 7 days", out.lower())

    def test_unknown_food(self):
        out = calorie.handle({"action": "log", "food": "unobtainium"}, _fake_ctx())
        self.assertIn("don't", out.lower())

    def test_foods_search(self):
        out = calorie.handle({"action": "foods", "query": "chicken"}, _fake_ctx())
        self.assertIn("chicken", out)


class TestQuiz(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        quiz.DATA_DIR = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        from utils import BASE_DIR
        quiz.DATA_DIR = BASE_DIR / "memory"

    def test_start_answer_score(self):
        out = quiz.handle({"action": "start", "topic": "general", "count": 3}, _fake_ctx())
        self.assertIn("Question 1", out)

        out = quiz.handle({"action": "answer", "answer": "1"}, _fake_ctx())
        self.assertIn("Question 2", out)

        out = quiz.handle({"action": "score"}, _fake_ctx())
        self.assertIn("score", out.lower())

        out = quiz.handle({"action": "end"}, _fake_ctx())
        self.assertIn("score", out.lower())

    def test_answer_before_start(self):
        out = quiz.handle({"action": "answer", "answer": "1"}, _fake_ctx())
        self.assertIn("no active quiz", out.lower())

    def test_bad_topic(self):
        out = quiz.handle({"action": "start", "topic": "astrology"}, _fake_ctx())
        self.assertIn("don't have questions", out)

    def test_topics(self):
        out = quiz.handle({"action": "topics"}, _fake_ctx())
        self.assertIn("science", out)


class TestMusic(unittest.TestCase):
    def test_dry_run(self):
        for action in ("play_pause", "next", "previous", "stop", "mute"):
            out = music.handle({"action": action, "dry_run": True}, _fake_ctx())
            self.assertIn("dry run", out.lower())

    def test_play_song_dry_run(self):
        out = music.handle({"action": "play_song", "query": "Shape of You", "dry_run": True}, _fake_ctx())
        self.assertIn("shape of you", out.lower())

    def test_unknown_action(self):
        out = music.handle({"action": "rewind"}, _fake_ctx())
        self.assertIn("unknown music action", out.lower())


class TestEmail(unittest.TestCase):
    def test_status_not_configured(self):
        out = email_plugin.handle({"action": "status"}, _fake_ctx())
        self.assertIn("configured", out.lower())
        self.assertIn("isn't", out.lower())

    def test_send_not_configured(self):
        out = email_plugin.handle({"action": "send", "to": "a@b.com", "body": "hi"}, _fake_ctx())
        self.assertIn("configured", out.lower())

    def test_unknown_action(self):
        out = email_plugin.handle({"action": "spam"}, _fake_ctx())
        self.assertIn("configured", out.lower())


if __name__ == "__main__":
    unittest.main()
