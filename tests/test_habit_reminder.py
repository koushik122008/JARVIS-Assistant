"""
MARK XLIX — Tests for the weather-aware habit reminder plugin.
"""

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from plugins import PLUGIN_TOOLS
from plugins import habit, habit_reminder


def _fake_ctx():
    return {"ui": None, "speak": None}


class TestDiscovery(unittest.TestCase):
    def test_habit_reminder_loaded(self):
        names = {t["name"] for t in PLUGIN_TOOLS}
        self.assertIn("habit_reminder", names)


class _TmpDataMixin:
    """Point one or more plugin DATA_DIRs at a temp folder for the test."""

    def _setup(self, *mods):
        self._tmp = tempfile.TemporaryDirectory()
        self._mods = []
        for mod in mods:
            self._mods.append((mod, mod.DATA_DIR))
            mod.DATA_DIR = Path(self._tmp.name)

    def _teardown(self):
        for mod, orig in self._mods:
            mod.DATA_DIR = orig
        self._tmp.cleanup()


class TestTimeParse(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(habit_reminder._parse_time("21:00"), "21:00")
        self.assertEqual(habit_reminder._parse_time("9pm"), "21:00")
        self.assertEqual(habit_reminder._parse_time("9:30 pm"), "21:30")
        self.assertEqual(habit_reminder._parse_time("07:05"), "07:05")
        self.assertEqual(habit_reminder._parse_time("12am"), "00:00")
        self.assertEqual(habit_reminder._parse_time("12pm"), "12:00")
        self.assertEqual(habit_reminder._parse_time("21:30"), "21:30")

    def test_parse_rejects(self):
        self.assertIsNone(habit_reminder._parse_time("nonsense"))
        self.assertIsNone(habit_reminder._parse_time("25:00"))
        self.assertIsNone(habit_reminder._parse_time("12:75"))
        self.assertIsNone(habit_reminder._parse_time(""))


class _FakeDT:
    """datetime stand-in pinned to 22:00 on 2026-08-07 (Fridays behave like any day)."""

    @classmethod
    def now(cls):
        return datetime(2026, 8, 7, 22, 0, 0)


class _FakeDate:
    @classmethod
    def today(cls):
        return date(2026, 8, 7)


class TestCheckAndFire(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(habit_reminder, habit)
        self._dt = mock.patch.object(habit_reminder, "datetime", _FakeDT)
        self._da = mock.patch.object(habit_reminder, "date", _FakeDate)
        self._dt.start()
        self._da.start()

    def tearDown(self):
        self._da.stop()
        self._dt.stop()
        self._teardown()

    def _seed_reminder(self, time="21:00", enabled=True, city="", last_fired=""):
        (Path(habit_reminder.DATA_DIR) / "habit_reminder.json").write_text(
            json.dumps({"enabled": enabled, "time": time,
                        "last_fired": last_fired, "city": city}),
            encoding="utf-8",
        )

    def _seed_habit(self, name, logged_today):
        p = Path(habit.DATA_DIR) / "habits.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {"habits": {}}
        data["habits"][name] = {
            "created": "2026-08-01",
            "log": ["2026-08-07"] if logged_today else [],
        }
        p.write_text(json.dumps(data), encoding="utf-8")

    def test_fires_after_time_with_missing_habit(self):
        self._seed_reminder(time="21:00")
        self._seed_habit("Drink Water", logged_today=False)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=None):
            msg = habit_reminder.check_and_fire()
        self.assertIsNotNone(msg)
        self.assertIn("drink water", msg)
        # once fired, the same day must never fire again
        self.assertIsNone(habit_reminder.check_and_fire())

    def test_all_logged_no_nudge_but_day_marked(self):
        self._seed_reminder(time="21:00")
        self._seed_habit("Drink Water", logged_today=True)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=None):
            self.assertIsNone(habit_reminder.check_and_fire())
            self.assertIsNone(habit_reminder.check_and_fire())  # still silent

    def test_before_time_does_not_fire(self):
        self._seed_reminder(time="23:00")   # fake now is 22:00
        self._seed_habit("Drink Water", logged_today=False)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=None):
            self.assertIsNone(habit_reminder.check_and_fire())

    def test_disabled_never_fires(self):
        self._seed_reminder(time="21:00", enabled=False)
        self._seed_habit("Drink Water", logged_today=False)
        self.assertIsNone(habit_reminder.check_and_fire())

    def test_weather_rain_nudge(self):
        self._seed_reminder(time="21:00", city="Istanbul")
        self._seed_habit("Meditation", logged_today=False)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=(61, "rain", 18.0)):
            msg = habit_reminder.check_and_fire()
        self.assertIsNotNone(msg)
        self.assertIn("rain", msg)
        self.assertIn("Istanbul", msg)
        self.assertIn("meditation", msg)

    def test_weather_clear_nudge(self):
        self._seed_reminder(time="21:00", city="London")
        self._seed_habit("Read", logged_today=False)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=(0, "clear sky", 22.0)):
            msg = habit_reminder.check_and_fire()
        self.assertIn("clear sky", msg)
        self.assertIn("read", msg)

    def test_weather_failure_still_nudges(self):
        self._seed_reminder(time="21:00", city="Nowhere")
        self._seed_habit("Run", logged_today=False)
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=None):
            msg = habit_reminder.check_and_fire()
        self.assertIn("run", msg)

    def test_no_habits_no_nudge(self):
        self._seed_reminder(time="21:00")
        with mock.patch.object(habit_reminder, "_fetch_conditions",
                               return_value=None):
            self.assertIsNone(habit_reminder.check_and_fire())


class TestHandle(_TmpDataMixin, unittest.TestCase):
    def setUp(self):
        self._setup(habit_reminder)

    def tearDown(self):
        self._teardown()

    def _data(self):
        return json.loads(
            (Path(habit_reminder.DATA_DIR) / "habit_reminder.json")
            .read_text(encoding="utf-8")
        )

    def test_set_and_status(self):
        out = habit_reminder.handle({"action": "set", "time": "8pm"}, _fake_ctx())
        self.assertIn("8:00 pm", out)
        data = self._data()
        self.assertEqual(data["time"], "20:00")
        self.assertTrue(data["enabled"])

    def test_set_bad_time(self):
        out = habit_reminder.handle({"action": "set", "time": "whenever"}, _fake_ctx())
        self.assertIn("couldn't understand", out.lower())

    def test_cancel_and_enable(self):
        out = habit_reminder.handle({"action": "cancel"}, _fake_ctx())
        self.assertIn("off", out.lower())
        self.assertFalse(self._data()["enabled"])
        out = habit_reminder.handle({"action": "enable"}, _fake_ctx())
        self.assertIn("enabled", out.lower())
        self.assertTrue(self._data()["enabled"])

    def test_set_city(self):
        out = habit_reminder.handle({"action": "city", "city": "paris"}, _fake_ctx())
        self.assertIn("Paris", out)
        self.assertEqual(self._data()["city"], "Paris")
        out = habit_reminder.handle({"action": "status"}, _fake_ctx())
        self.assertIn("Paris", out)

    def test_status_default(self):
        out = habit_reminder.handle({"action": "status"}, _fake_ctx())
        self.assertIn("9:00 pm", out)
        self.assertIn("on", out.lower())


class TestOsSchedule(_TmpDataMixin, unittest.TestCase):
    """sync_os_schedule / _ensure_today_scheduled behaviour."""

    def setUp(self):
        self._setup(habit_reminder)
        self._dt = mock.patch.object(habit_reminder, "datetime", _FakeDT)
        self._da = mock.patch.object(habit_reminder, "date", _FakeDate)
        self._dt.start()
        self._da.start()
        self._scripts = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._da.stop()
        self._dt.stop()
        self._scripts.cleanup()
        self._teardown()

    def _seed(self, **kw):
        # 22:30 is in the future relative to the fake clock (22:00) so the
        # OS task can be scheduled for today.
        data = {"enabled": True, "time": "22:30", "last_fired": "",
                "city": "", "os_scheduled_for": "", "os_retry_after": 0.0}
        data.update(kw)
        (Path(habit_reminder.DATA_DIR) / "habit_reminder.json").write_text(
            json.dumps(data), encoding="utf-8")

    def _patches(self):
        return (
            mock.patch("utils.get_os_name", return_value="windows"),
            mock.patch("actions.reminder._scripts_dir",
                       return_value=Path(self._scripts.name)),
        )

    def test_schedules_windows_task_once(self):
        self._seed()
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="JARVISHabitReminder_2026-08-07") as sched:
            habit_reminder.sync_os_schedule()
        sched.assert_called_once()
        # standalone script is written and computes its own message
        script = Path(self._scripts.name) / "JARVISHabitReminder_2026-08-07.py"
        content = script.read_text(encoding="utf-8")
        self.assertIn("check_and_fire", content)
        self.assertIn("sys.path.insert", content)
        self.assertNotIn("unlink", content)  # daily task must NOT self-delete
        self.assertEqual(habit_reminder._load()["os_scheduled_for"], "2026-08-07")

    def test_no_reschedule_same_day(self):
        self._seed(os_scheduled_for="2026-08-07")
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="x") as sched:
            habit_reminder.sync_os_schedule()
        sched.assert_not_called()

    def test_disabled_no_schedule(self):
        self._seed(enabled=False)
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="x") as sched:
            habit_reminder.sync_os_schedule()
        sched.assert_not_called()

    def test_past_time_no_schedule(self):
        self._seed(time="21:00")   # fake now is 22:00 → 21:01 is in the past
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="x") as sched:
            habit_reminder.sync_os_schedule()
        sched.assert_not_called()

    def test_failed_schedule_sets_cooldown(self):
        self._seed()
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows", return_value=""):
            habit_reminder.sync_os_schedule()
        data = habit_reminder._load()
        self.assertEqual(data["os_scheduled_for"], "")
        self.assertGreater(data["os_retry_after"], 0)

    def test_cooldown_respected(self):
        self._seed(os_retry_after=10 ** 12)   # far in the future
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="x") as sched:
            habit_reminder.sync_os_schedule()
        sched.assert_not_called()

    def test_old_scripts_cleaned_up(self):
        self._seed()
        old = Path(self._scripts.name) / "JARVISHabitReminder_2026-08-01.py"
        old.write_text("x", encoding="utf-8")
        with self._patches()[0], self._patches()[1], \
             mock.patch("actions.reminder._schedule_windows",
                        return_value="JARVISHabitReminder_2026-08-07"):
            habit_reminder.sync_os_schedule()
        self.assertFalse(old.exists())   # >2 days old -> removed
        self.assertTrue(                 # today's task is kept
            (Path(self._scripts.name) / "JARVISHabitReminder_2026-08-07.py").exists())

    def test_set_resets_os_schedule(self):
        self._seed(os_scheduled_for="2026-08-07")
        habit_reminder.handle({"action": "set", "time": "22:00"}, _fake_ctx())
        self.assertEqual(habit_reminder._load()["os_scheduled_for"], "")


class TestPhonePush(_TmpDataMixin, unittest.TestCase):
    """ntfy phone-push behaviour (send_push + handle push actions)."""

    def setUp(self):
        self._setup(habit_reminder)
        self._urlopen = mock.patch("urllib.request.urlopen")
        self._mock_open = self._urlopen.start()

    def tearDown(self):
        self._urlopen.stop()
        self._teardown()

    def _seed(self, **kw):
        data = {"enabled": True, "time": "21:00", "last_fired": "",
                "city": "", "os_scheduled_for": "", "os_retry_after": 0.0,
                "push_enabled": False, "push_topic": "", "push_token": ""}
        data.update(kw)
        (Path(habit_reminder.DATA_DIR) / "habit_reminder.json").write_text(
            json.dumps(data), encoding="utf-8")

    def _ok_resp(self):
        r = mock.Mock()
        r.status = 200
        r.__enter__ = lambda s: s
        r.__exit__ = lambda *a: None
        return r

    def test_disabled_no_request(self):
        self._seed(push_enabled=False, push_topic="my-topic")
        self.assertFalse(habit_reminder.send_push("nudge"))
        self._mock_open.assert_not_called()

    def test_no_topic_no_request(self):
        self._seed(push_enabled=True, push_topic="")
        self.assertFalse(habit_reminder.send_push("nudge"))
        self._mock_open.assert_not_called()

    def test_posts_to_ntfy_topic(self):
        self._seed(push_enabled=True, push_topic="jarvis-habits")
        self._mock_open.return_value = self._ok_resp()
        self.assertTrue(habit_reminder.send_push("You haven't logged read today."))
        req = self._mock_open.call_args[0][0]
        self.assertEqual(req.full_url, "https://ntfy.sh/jarvis-habits")
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.data, b"You haven't logged read today.")
        self.assertEqual(req.headers.get("Title"), "J.A.R.V.I.S Habit Reminder")
        self.assertNotIn("Authorization", req.headers)

    def test_token_sets_bearer_header(self):
        self._seed(push_enabled=True, push_topic="private-topic",
                   push_token="tk_secret")
        self._mock_open.return_value = self._ok_resp()
        self.assertTrue(habit_reminder.send_push("nudge"))
        req = self._mock_open.call_args[0][0]
        self.assertEqual(req.headers.get("Authorization"), "Bearer tk_secret")

    def test_network_failure_returns_false(self):
        self._seed(push_enabled=True, push_topic="my-topic")
        self._mock_open.side_effect = Exception("offline")
        self.assertFalse(habit_reminder.send_push("nudge"))

    def test_handle_set_topic_enables_push(self):
        out = habit_reminder.handle({"action": "push_topic",
                                     "topic": "jarvis-habits"}, _fake_ctx())
        self.assertIn("jarvis-habits", out)
        data = habit_reminder._load()
        self.assertEqual(data["push_topic"], "jarvis-habits")
        self.assertTrue(data["push_enabled"])

    def test_handle_push_on_requires_topic(self):
        self._seed(push_enabled=False, push_topic="")
        out = habit_reminder.handle({"action": "push_on"}, _fake_ctx())
        self.assertIn("topic", out)
        self.assertFalse(habit_reminder._load()["push_enabled"])

    def test_handle_topic_empty_returns_guidance(self):
        out = habit_reminder.handle({"action": "push_topic", "topic": ""},
                                    _fake_ctx())
        self.assertIn("topic", out)
        self.assertFalse(habit_reminder._load()["push_enabled"])

    def test_handle_topic_rejects_invalid_chars(self):
        out = habit_reminder.handle({"action": "push_topic",
                                     "topic": "my topic"}, _fake_ctx())
        self.assertIn("letters, numbers", out)
        self.assertEqual(habit_reminder._load()["push_topic"], "")

    def test_topic_whitespace_trimmed_in_send_push(self):
        self._seed(push_enabled=True, push_topic="  jarvis-habits  ")
        self._mock_open.return_value = self._ok_resp()
        self.assertTrue(habit_reminder.send_push("nudge"))
        req = self._mock_open.call_args[0][0]
        self.assertEqual(req.full_url, "https://ntfy.sh/jarvis-habits")

    def test_handle_push_off_disables(self):
        self._seed(push_enabled=True, push_topic="my-topic")
        out = habit_reminder.handle({"action": "push_off"}, _fake_ctx())
        self.assertFalse(habit_reminder._load()["push_enabled"])
        self.assertIn("off", out)

    def test_handle_set_and_clear_token(self):
        habit_reminder.handle({"action": "push_token",
                               "token": "tk_abc"}, _fake_ctx())
        self.assertEqual(habit_reminder._load()["push_token"], "tk_abc")
        habit_reminder.handle({"action": "push_token",
                               "token": "clear"}, _fake_ctx())
        self.assertEqual(habit_reminder._load()["push_token"], "")

    def test_status_reports_push(self):
        self._seed(push_enabled=True, push_topic="my-topic")
        out = habit_reminder.handle({"action": "status"}, _fake_ctx())
        self.assertIn("Phone push: on", out)
        self.assertIn("my-topic", out)

    def test_standalone_script_embeds_push(self):
        self._seed(push_enabled=True, push_topic="my-topic")
        script = Path(habit_reminder.DATA_DIR) / "nudge_script.py"
        with mock.patch("utils.get_os_name", return_value="windows"):
            habit_reminder._write_standalone_script(script)
        content = script.read_text(encoding="utf-8")
        self.assertIn("send_push", content)
        self.assertIn("from plugins.habit_reminder import send_push", content)


if __name__ == "__main__":
    unittest.main()
