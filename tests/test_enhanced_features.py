"""
Tests for the "10/10" feature upgrades:

  1. actions.image_generator — real Imagen path (mocked) + offline PIL fallback
  2. actions.weather_report  — WMO code mapping, report formatting, fallbacks
  3. actions.flight_finder   — Google Flights URL no longer carries a stale tfs token

Strategy:
  - No network: ``requests.get`` and Imagen calls are patched.
  - The PIL fallback is exercised with real in-memory images (validated with PIL).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from actions import image_generator as ig
from actions import open_app as oa
from actions import reminder as rm
from actions import send_message as sm
from actions import weather_report as wr


# ═══════════════════════════════════════════════════════════════════════════════
# actions.image_generator
# ═══════════════════════════════════════════════════════════════════════════════


class TestImageGeneratorMeta:
    def test_all_declared_styles_have_hints(self):
        declared = {
            "realistic", "artistic", "cartoon", "pixel", "watercolor", "sketch",
            "abstract", "retro", "neon", "cyberpunk", "minimalist", "vintage",
            "fantasy", "anime", "oil_painting",
        }
        assert declared <= set(ig._STYLE_HINTS.keys())

    def test_build_prompt_appends_style_hint(self):
        out = ig._build_prompt("a castle", "neon", None)
        assert out.startswith("a castle")
        assert "neon glow" in out

    def test_build_prompt_appends_color_scheme(self):
        out = ig._build_prompt("a castle", "realistic", "pastel")
        assert "pastel" in out

    def test_unknown_style_keeps_prompt_clean(self):
        out = ig._build_prompt("a castle", "nonexistent", None)
        assert out == "a castle"


class TestImageGeneratorFallback:
    """The PIL fallback must produce a valid image with no network/API access."""

    @pytest.fixture(autouse=True)
    def _break_imagen(self):
        with patch.object(ig, "_generate_with_imagen", side_effect=RuntimeError("offline")):
            yield

    def test_fallback_returns_png_bytes_and_path(self):
        out = ig.generate_image({"prompt": "a sunset", "style": "realistic"})
        assert out["ai"] is False
        assert out["path"]
        assert out["mime"] == "image/png"
        assert out["image_bytes"].startswith(b"\x89PNG")

    def test_fallback_respects_aspect_ratio(self):
        out = ig.generate_image({"prompt": "x", "aspect_ratio": "landscape"})
        img = self._open(out)
        assert img.size == (768, 512)

    def test_fallback_respects_explicit_size(self):
        out = ig.generate_image({"prompt": "x", "width": 200, "height": 300})
        img = self._open(out)
        assert img.size == (200, 300)

    def test_fallback_defaults_to_square(self):
        out = ig.generate_image({"prompt": "x"})
        img = self._open(out)
        assert img.size == (512, 512)

    def test_empty_prompt_gets_default(self):
        out = ig.generate_image({})
        assert "a beautiful landscape" in out["prompt"]

    def test_unknown_style_falls_back_to_realistic(self):
        out = ig.generate_image({"prompt": "x", "style": "banana"})
        assert out["result"].find("banana") == -1  # no crash, normalized style

    @staticmethod
    def _open(out):
        from PIL import Image
        img = Image.open(io.BytesIO(out["image_bytes"]))
        img.load()
        return img


class TestImageGeneratorImagen:
    """The AI path must be attempted first and used when it succeeds."""

    def test_imagen_success_is_used(self):
        with (
            patch("actions.image_generator._generate_with_imagen") as m,
            patch("actions.image_generator._save_bytes", return_value=ig.IMAGE_DIR / "x.png"),
        ):
            m.return_value = (b"\x89PNG-fake-bytes", "image/png")
            out = ig.generate_image({"prompt": "robot", "style": "cyberpunk"})
        assert out["ai"] is True
        assert out["image_bytes"] == b"\x89PNG-fake-bytes"

    def test_color_scheme_reaches_imagen_prompt(self):
        from actions.image_generator import _build_prompt
        out = _build_prompt("a castle", "neon", "vibrant")
        assert "vibrant" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.weather_report
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeatherCodes:
    def test_common_codes(self):
        assert wr.describe_code(0) == "clear sky"
        assert wr.describe_code(3) == "overcast"
        assert wr.describe_code(61) == "slight rain"
        assert wr.describe_code(95) == "thunderstorm"

    def test_unknown_code(self):
        assert wr.describe_code(9999) == "unknown conditions"

    def test_string_code_coerced(self):
        assert wr.describe_code("2") == "partly cloudy"


class TestWeatherReportBuild:
    @staticmethod
    def _data():
        return {
            "current": {
                "temperature_2m": 21.4,
                "apparent_temperature": 19.0,
                "relative_humidity_2m": 55,
                "weather_code": 2,
                "wind_speed_10m": 12.3,
            },
            "daily": {
                "time": ["2026-08-04", "2026-08-05", "2026-08-06"],
                "weather_code": [2, 61, 0],
                "temperature_2m_max": [24.0, 19.0, 26.0],
                "temperature_2m_min": [15.0, 12.0, 16.0],
            },
        }

    def test_report_contains_current_and_forecast(self):
        report = wr.build_report("Paris", {"admin1": "Île-de-France", "country": "France"}, self._data())
        assert "Paris" in report
        assert "partly cloudy" in report
        assert "21" in report  # temperature present
        assert "Forecast:" in report
        assert "Today" in report
        assert "Tomorrow" in report

    def test_tomorrow_report_leads_with_tomorrow(self):
        report = wr.build_report("Paris", {}, self._data(), when="tomorrow")
        assert report.startswith("Tomorrow in Paris")
        assert "slight rain" in report  # daily[1] weather code 61
        assert "Forecast:" not in report

    def test_report_no_daily_data(self):
        report = wr.build_report("X", {}, {"current": {"temperature_2m": 5.0, "weather_code": 71}})
        assert "snow" in report
        assert "Forecast:" not in report


class TestWeatherAction:
    def test_missing_city_message(self):
        assert "city is missing" in wr.weather_action({})

    def test_live_fetch_path(self):
        place = {"latitude": 48.85, "longitude": 2.35, "admin1": "X", "country": "Y"}
        data = {
            "current": {"temperature_2m": 20.0, "weather_code": 1},
            "daily": {"time": ["2026-08-04", "2026-08-05"],
                      "weather_code": [1, 1],
                      "temperature_2m_max": [22.0, 23.0],
                      "temperature_2m_min": [14.0, 15.0]},
        }
        with (
            patch.object(wr, "_geocode", return_value=place),
            patch.object(wr, "fetch_weather", return_value=data),
        ):
            result = wr.weather_action({"city": "Paris"})
        assert "Paris" in result
        assert "mainly clear" in result

    def test_network_failure_falls_back_to_browser(self):
        with (
            patch.object(wr, "_geocode", side_effect=RuntimeError("no network")),
            patch.object(wr, "_browser_fallback", return_value="fallback used") as fb,
        ):
            result = wr.weather_action({"city": "Paris"})
        assert result == "fallback used"
        fb.assert_called_once_with("Paris", "today")


# ═══════════════════════════════════════════════════════════════════════════════
# actions.flight_finder — stale tfs token removed
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# actions.reminder — natural-language parsing + in-process fallback
# ═══════════════════════════════════════════════════════════════════════════════


class TestReminderWhenParsing:
    def test_explicit_datetime(self):
        dt = rm._parse_when("2026-08-10", "14:30", "")
        assert dt == datetime(2026, 8, 10, 14, 30)

    def test_relative_minutes(self):
        before = datetime.now()
        dt = rm._parse_when("", "", "in 30 minutes")
        assert before < dt < before + timedelta(minutes=31)

    def test_relative_hours_and_days(self):
        now = datetime.now()
        dt = rm._parse_when("", "", "in 2 hours")
        assert dt.hour == (now + timedelta(hours=2)).hour
        dt = rm._parse_when("", "", "in 3 days")
        assert dt.day == (now + timedelta(days=3)).day

    def test_tomorrow_at_nine_am(self):
        now = datetime.now()
        dt = rm._parse_when("", "", "tomorrow at 9am")
        expected = (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        assert dt == expected

    def test_tonight_defaults_to_eight_pm(self):
        now = datetime.now()
        dt = rm._parse_when("", "", "tonight")
        expected = now.replace(hour=20, minute=0, second=0, microsecond=0)
        assert dt == expected

    def test_time_only_rolls_to_tomorrow_when_past(self):
        now = datetime.now()
        past = (now - timedelta(hours=1)).strftime("%H:%M")
        dt = rm._parse_when("", past, "")
        assert dt > now

    def test_when_as_full_datetime(self):
        dt = rm._parse_when("", "", "2026-08-10 09:15")
        assert dt == datetime(2026, 8, 10, 9, 15)

    def test_unparseable_returns_none(self):
        assert rm._parse_when("", "", "banana banana") is None
        assert rm._parse_when("", "", "") is None


class TestReminderInProcessFallback:
    def test_fallback_used_when_scheduler_fails(self):
        target = datetime.now() + timedelta(minutes=30)
        with (
            patch.object(rm, "_schedule_windows", return_value="") as sched,
            patch.object(rm, "_schedule_in_process", return_value="inprocess_x") as inproc,
            patch.object(rm, "_write_notify_script", return_value=Path("fake.py")),
        ):
            out = rm.reminder({
                "date": target.strftime("%Y-%m-%d"),
                "time": target.strftime("%H:%M"),
                "message": "Test reminder",
            })
        assert "in-app timer" in out
        sched.assert_called_once()
        inproc.assert_called_once()

    def test_system_scheduler_used_when_available(self):
        target = datetime.now() + timedelta(minutes=30)
        with (
            patch.object(rm, "_schedule_windows", return_value="JARVISReminder_x") as sched,
            patch.object(rm, "_schedule_in_process") as inproc,
            patch.object(rm, "_write_notify_script", return_value=Path("fake.py")),
        ):
            out = rm.reminder({
                "date": target.strftime("%Y-%m-%d"),
                "time": target.strftime("%H:%M"),
                "message": "Test reminder",
            })
        assert "in-app timer" not in out
        inproc.assert_not_called()

    def test_natural_language_when_param(self):
        with (
            patch.object(rm, "_schedule_windows", return_value=""),
            patch.object(rm, "_schedule_in_process", return_value="inprocess_x"),
            patch.object(rm, "_write_notify_script", return_value=Path("fake.py")),
        ):
            out = rm.reminder({"when": "in 30 minutes", "message": "Stand up"})
        assert "in-app timer" in out

    def test_unparseable_time_message(self):
        out = rm.reminder({"message": "x"})
        assert "couldn't parse" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.send_message — dry-run validation + WhatsApp deep link
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendMessageDryRun:
    def test_dry_run_does_not_touch_desktop(self):
        with patch.object(sm, "_desktop_send") as desktop:
            out = sm.send_message({
                "receiver": "Alice",
                "message_text": "Hello",
                "platform": "telegram",
                "dry_run": True,
            })
        assert "DRY RUN" in out
        assert "Alice" in out
        desktop.assert_not_called()

    def test_dry_run_validates_missing_inputs(self):
        assert "recipient" in sm.send_message({"message_text": "x", "platform": "tg"})
        assert "message content" in sm.send_message({"receiver": "x", "platform": "tg"})

    def test_no_dry_run_paths_to_handler(self):
        with patch.object(sm, "_desktop_send", return_value="sent") as desktop:
            out = sm.send_message({
                "receiver": "Bob", "message_text": "Hi", "platform": "signal"
            })
        assert out == "sent"
        desktop.assert_called_once()


class TestWhatsAppDeepLink:
    def test_deep_link_builds_wa_me_url(self):
        url = sm._whatsapp_deep_link("+1 555 0100", "Hello there")
        assert url.startswith("https://wa.me/+15550100?text=")
        assert "Hello" in url

    def test_phone_receiver_uses_deep_link(self):
        with (
            patch.object(sm, "_open_browser_url", return_value=True) as opener,
            patch.object(sm, "_desktop_send") as desktop,
        ):
            out = sm._send_whatsapp("+15550100", "Hi")
        assert "wa.me" in out or "pre-filled" in out
        opener.assert_called_once()
        desktop.assert_not_called()

    def test_name_receiver_uses_desktop_path(self):
        with (
            patch.object(sm, "_open_browser_url"),
            patch.object(sm, "_desktop_send", return_value="sent via desktop") as desktop,
        ):
            out = sm._send_whatsapp("Alice", "Hi")
        assert out == "sent via desktop"
        desktop.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# actions.open_app — Start Menu shortcuts, process verification, URL handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestOpenAppShortcutResolution:
    def test_exact_shortcut_match(self, tmp_path):
        lnk = tmp_path / "WhatsApp.lnk"
        lnk.write_text("")
        with patch.object(oa, "_SYSTEM", "Windows"):
            found = oa._find_start_menu_shortcut("whatsapp", [str(tmp_path)])
        assert found == str(lnk)

    def test_partial_shortcut_match_picks_shortest(self, tmp_path):
        (tmp_path / "Google Chrome.lnk").write_text("")
        (tmp_path / "Chrome DevTools.lnk").write_text("")
        with patch.object(oa, "_SYSTEM", "Windows"):
            found = oa._find_start_menu_shortcut("chrome", [str(tmp_path)])
        assert found.endswith("Google Chrome.lnk")

    def test_no_shortcut_returns_none(self, tmp_path):
        with patch.object(oa, "_SYSTEM", "Windows"):
            found = oa._find_start_menu_shortcut("unknownapp", [str(tmp_path)])
        assert found is None

    def test_skipped_on_non_windows(self, tmp_path):
        with patch.object(oa, "_SYSTEM", "Linux"):
            assert oa._find_start_menu_shortcut("x", [str(tmp_path)]) is None

    def test_windows_launcher_prefers_shortcut_over_win_key(self, tmp_path):
        lnk = tmp_path / "Telegram.lnk"
        lnk.write_text("")
        with (
            patch.object(oa, "_SYSTEM", "Windows"),
            patch("actions.open_app.shutil.which", return_value=None),
            patch.object(oa, "_find_start_menu_shortcut", return_value=str(lnk)) as finder,
            patch("actions.open_app.os.startfile") as startfile,
            patch("actions.open_app.time.sleep"),
        ):
            ok = oa._launch_windows("Telegram")
        assert ok is True
        startfile.assert_called_once_with(str(lnk))
        finder.assert_called_once_with("Telegram")


class TestOpenAppProcessVerification:
    def test_verify_finds_running_process(self):
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "chrome.exe                1234 Console      1     300,000 K\n"
        with patch("actions.open_app.subprocess.run", return_value=fake):
            assert oa._verify_windows_process("chrome.exe") is True

    def test_verify_true_when_tasklist_unavailable(self):
        with patch("actions.open_app.subprocess.run", side_effect=OSError("no tasklist")):
            assert oa._verify_windows_process("chrome.exe") is True

    def test_binary_launch_confirmed_via_tasklist(self):
        with (
            patch("actions.open_app.shutil.which", return_value="C:/apps/spotify.exe"),
            patch("actions.open_app.subprocess.Popen"),
            patch.object(oa, "_verify_windows_process", return_value=True),
            patch("actions.open_app.time.sleep"),
        ):
            ok = oa._launch_windows("spotify")
        assert ok is True

    def test_verify_handles_cmd_shim(self):
        # VSCode's "code.cmd" shim spawns the real Code.exe process
        fake = MagicMock()
        fake.returncode = 0
        fake.stdout = "Code.exe                  1234 Console      1     300,000 K\n"
        with patch("actions.open_app.subprocess.run", return_value=fake):
            assert oa._verify_windows_process("code.cmd") is True


class TestOpenAppUrlHandling:
    def test_bare_domain_opens_in_browser(self):
        with patch.object(oa, "_open_url", return_value=True) as opener:
            out = oa.open_app({"app_name": "youtube.com"})
        assert "browser" in out
        opener.assert_called_once_with("https://youtube.com")

    def test_full_url_passed_through(self):
        with patch.object(oa, "_open_url", return_value=True) as opener:
            out = oa.open_app({"app_name": "https://github.com"})
        assert "browser" in out
        opener.assert_called_once_with("https://github.com")

    def test_open_url_failure_reported(self):
        with patch.object(oa, "_open_url", return_value=False):
            out = oa.open_app({"app_name": "youtube.com"})
        assert "Could not open" in out

    def test_app_names_are_not_treated_as_urls(self):
        assert oa._is_url_like("notepad") is False
        assert oa._is_url_like("visual studio code") is False
        assert oa._is_url_like("whatsapp") is False
        assert oa._is_url_like("spotify.exe") is False
        assert oa._is_url_like("notes.txt") is False
        assert oa._is_url_like("app.py") is False

    def test_urls_still_detected(self):
        assert oa._is_url_like("youtube.com") is True
        assert oa._is_url_like("https://github.com") is True
        assert oa._is_url_like("www.wikipedia.org") is True


# ═══════════════════════════════════════════════════════════════════════════════
# actions.flight_finder — stale tfs token removed
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlightUrl:
    def test_url_has_no_stale_tfs_token(self):
        from actions.flight_finder import _build_google_flights_url
        url = _build_google_flights_url("IST", "LHR", "2026-08-10")
        assert "tfs=" not in url
        assert url.startswith("https://www.google.com/travel/flights?q=Flights")

    def test_url_encodes_round_trip_and_options(self):
        from actions.flight_finder import _build_google_flights_url
        url = _build_google_flights_url("NYC", "LON", "2026-08-10", "2026-08-17", 2, "business")
        assert "returning+2026-08-17" in url
        assert "cabin=3" in url
        assert "adults=2" in url
