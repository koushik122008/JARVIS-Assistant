"""
Tests for the 8 new feature tools:

  1. actions.currency_converter  - live FX (mocked) + offline fallback
  2. actions.crypto_prices       - CoinGecko (mocked) + offline fallback
  3. actions.unit_converter      - pure offline unit math
  4. actions.alarm               - delegates to the reminder engine
  5. actions.battery_info        - psutil battery (mocked) + health tips
  6. actions.translator          - MyMemory API (mocked) + phrasebook fallback
  7. actions.stock_prices        - Stooq CSV (mocked)
  8. actions.timer               - duration parsing + validation

Strategy: no real network calls - all requests.get are patched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from actions import alarm as al
from actions import battery_info as bi
from actions import crypto_prices as cp
from actions import currency_converter as cc
from actions import stock_prices as sp
from actions import timer as tm
from actions import translator as tr
from actions import unit_converter as uc


def _ok_response(payload):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    resp.text = payload if isinstance(payload, str) else ""
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# actions.currency_converter
# ═══════════════════════════════════════════════════════════════════════════════


class TestCurrency:
    def test_resolve_codes(self):
        assert cc._resolve_code("dollars") == "USD"
        assert cc._resolve_code("EUR") == "EUR"
        assert cc._resolve_code("lira") == "TRY"
        assert cc._resolve_code("banana") is None

    def test_parse_structured_input(self):
        amount, frm, to = cc._parse_inputs(
            {"amount": 100, "from": "usd", "to": "eur"}
        )
        assert (amount, frm, to) == (100.0, "USD", "EUR")

    def test_parse_free_text(self):
        amount, frm, to = cc._parse_inputs(
            {"text": "convert 50 dollars to euros"}
        )
        assert (amount, frm, to) == (50.0, "USD", "EUR")

    def test_parse_into_separator(self):
        amount, frm, to = cc._parse_inputs(
            {"text": "100 dollars into euros"}
        )
        assert (amount, frm, to) == (100.0, "USD", "EUR")

    def test_parse_word_amount(self):
        assert cc._parse_amount("one hundred") == 100.0
        assert cc._parse_amount("two hundred fifty") == 250.0
        assert cc._parse_amount("five thousand") == 5000.0

    def test_missing_inputs_error(self):
        out = cc.currency_converter({"amount": 5})
        assert "amount and two currencies" in out

    def test_live_conversion(self):
        with patch(
            "actions.currency_converter.requests.get",
            return_value=_ok_response({"rates": {"EUR": 0.92}}),
        ) as req:
            out = cc.currency_converter(
                {"amount": 100, "from": "USD", "to": "EUR"}
            )
        req.assert_called_once()
        assert "92" in out.replace(",", "").replace(".", "")
        assert "EUR" in out

    def test_offline_fallback(self):
        with patch(
            "actions.currency_converter.requests.get",
            side_effect=RuntimeError("no network"),
        ):
            out = cc.currency_converter(
                {"amount": 100, "from": "USD", "to": "EUR"}
            )
        assert "approximate offline rate" in out

    def test_same_currency(self):
        out = cc.currency_converter({"amount": 10, "from": "USD", "to": "USD"})
        assert "10" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.crypto_prices
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrypto:
    def test_resolve_alias(self):
        assert cp._resolve_id("btc") == "bitcoin"
        assert cp._resolve_id("ETH") == "ethereum"
        assert cp._resolve_id("doge") == "dogecoin"
        assert cp._resolve_id("madeupcoin") is None

    def test_live_single_coin(self):
        payload = {"bitcoin": {"usd": 61000.0, "usd_24h_change": 2.5}}
        with patch(
            "actions.crypto_prices.requests.get",
            return_value=_ok_response(payload),
        ):
            out = cp.crypto_prices({"asset": "btc"})
        assert "Bitcoin" in out
        assert "up 2.5%" in out
        assert "$61,000" in out

    def test_offline_fallback(self):
        with patch(
            "actions.crypto_prices.requests.get",
            side_effect=RuntimeError("no network"),
        ):
            out = cp.crypto_prices({"asset": "bitcoin"})
        assert "offline" in out

    def test_unknown_coin(self):
        with patch(
            "actions.crypto_prices.requests.get",
            side_effect=RuntimeError("no network"),
        ):
            out = cp.crypto_prices({"asset": "bananacoin"})
        assert "couldn't fetch" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.unit_converter (pure offline)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnitConverter:
    def test_miles_to_km_text(self):
        out = uc.unit_converter({"text": "convert 5 miles to km"})
        assert "8.0467" in out

    def test_fahrenheit_to_celsius_text(self):
        out = uc.unit_converter({"text": "100 fahrenheit to celsius"})
        assert "37.7778" in out

    def test_structured_kg_to_lb(self):
        out = uc.unit_converter({"value": 2, "from": "kg", "to": "lb"})
        assert "4.4092" in out

    def test_kph_to_mph(self):
        out = uc.unit_converter({"text": "100 km/h in mph"})
        assert "62.1371" in out

    def test_data_units_are_case_sensitive(self):
        # KB = kilobytes (bytes), kb = kilobits (bits) — must not collapse
        out_bytes = uc.unit_converter({"value": 1, "from": "KB", "to": "B"})
        assert "1,000" in out_bytes
        out_bits = uc.unit_converter({"value": 1, "from": "kb", "to": "b"})
        assert "1,000" in out_bits
        # 1 KB (8000 bits) vs 1 kb (1000 bits) differ by 8x
        out_cross = uc.unit_converter({"value": 1, "from": "KB", "to": "kb"})
        assert "8" in out_cross

    def test_into_separator(self):
        out = uc.unit_converter({"text": "convert 5 miles into km"})
        assert "8.0467" in out

    def test_unknown_units_error(self):
        out = uc.unit_converter({"value": 5, "from": "blorb", "to": "zork"})
        assert "couldn't match those units" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.alarm - delegates to reminder
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlarm:
    def test_delegates_to_reminder(self):
        with patch(
            "actions.alarm.reminder",
            return_value="Reminder set for X.",
        ) as rem:
            out = al.alarm({"when": "in 10 minutes", "message": "Wake up"})
        assert out == "Alarm set for X."
        rem.assert_called_once()
        assert rem.call_args.kwargs["parameters"]["message"] == "Wake up"

    def test_default_message(self):
        with patch(
            "actions.alarm.reminder",
            return_value="Reminder set for X.",
        ) as rem:
            al.alarm({"when": "in 10 minutes"})
        assert rem.call_args.kwargs["parameters"]["message"] == "Alarm!"


# ═══════════════════════════════════════════════════════════════════════════════
# actions.battery_info
# ═══════════════════════════════════════════════════════════════════════════════


class TestBattery:
    @staticmethod
    def _battery(percent=75.0, plugged=False, secsleft=7200):
        bat = MagicMock()
        bat.percent = percent
        bat.power_plugged = plugged
        bat.secsleft = secsleft
        return bat

    def test_reports_percent_and_remaining(self):
        with patch("actions.battery_info.psutil.sensors_battery",
                   return_value=self._battery()):
            out = bi.battery_info()
        assert "75%" in out
        assert "battery power" in out
        assert "2 hours" in out

    def test_charging_state(self):
        with patch("actions.battery_info.psutil.sensors_battery",
                   return_value=self._battery(percent=50, plugged=True)):
            out = bi.battery_info()
        assert "charging" in out

    def test_low_battery_tip(self):
        with patch("actions.battery_info.psutil.sensors_battery",
                   return_value=self._battery(percent=12, plugged=False)):
            out = bi.battery_info()
        assert "plug in soon" in out

    def test_no_battery(self):
        with patch("actions.battery_info.psutil.sensors_battery", return_value=None):
            out = bi.battery_info()
        assert "No battery detected" in out

    def test_read_error(self):
        with patch("actions.battery_info.psutil.sensors_battery",
                   side_effect=RuntimeError("boom")):
            out = bi.battery_info()
        assert "couldn't read" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.translator
# ═══════════════════════════════════════════════════════════════════════════════


class TestTranslator:
    def test_resolve_language(self):
        assert tr._resolve_lang("japanese") == "ja"
        assert tr._resolve_lang("FR") == "fr"
        assert tr._resolve_lang("turkish") == "tr"
        assert tr._resolve_lang("klingon") is None

    def test_live_translation(self):
        payload = {"responseData": {"translatedText": "bonjour"}}
        with patch(
            "actions.translator.requests.get",
            return_value=_ok_response(payload),
        ) as req:
            out = tr.translate_text({"text": "hello", "to": "french"})
        req.assert_called_once()
        assert "bonjour" in out
        assert "French" in out

    def test_offline_phrasebook_fallback(self):
        with patch(
            "actions.translator.requests.get",
            side_effect=RuntimeError("no network"),
        ):
            out = tr.translate_text({"text": "hello", "to": "japanese"})
        assert "konnichiwa" in out
        assert "offline" in out

    def test_missing_text(self):
        out = tr.translate_text({"to": "japanese"})
        assert "what to translate" in out

    def test_unknown_target_language(self):
        out = tr.translate_text({"text": "hi", "to": "klingon"})
        assert "don't recognise" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.stock_prices
# ═══════════════════════════════════════════════════════════════════════════════


class TestStocks:
    CSV = "Symbol,Date,Time,Open,High,Low,Close,Volume\naapl.us,2026-08-04,16:00,230.0,235.0,228.0,234.5,50000000\n"

    def test_normalize_ticker(self):
        assert sp._normalize_ticker("aapl") == "aapl.us"
        assert sp._normalize_ticker("brk-b") == "brk-b.us"
        assert sp._normalize_ticker("MSFT") == "msft.us"
        assert sp._normalize_ticker("aapl.us") == "aapl.us"

    def test_live_quote(self):
        with patch(
            "actions.stock_prices.requests.get",
            return_value=_ok_response(self.CSV),
        ) as req:
            out = sp.stock_prices({"ticker": "aapl"})
        req.assert_called_once()
        assert "Apple" in out
        assert "$234.50" in out
        assert "up 4.50" in out

    def test_missing_ticker(self):
        out = sp.stock_prices({})
        assert "ticker" in out

    def test_failed_fetch(self):
        with patch(
            "actions.stock_prices.requests.get",
            side_effect=RuntimeError("no network"),
        ):
            out = sp.stock_prices({"ticker": "aapl"})
        assert "couldn't fetch" in out


# ═══════════════════════════════════════════════════════════════════════════════
# actions.timer
# ═══════════════════════════════════════════════════════════════════════════════


class TestTimer:
    def test_parse_structured(self):
        assert tm._parse_duration({"minutes": 5}, "") == 300
        assert tm._parse_duration({"seconds": 90}, "") == 90
        assert tm._parse_duration({"hours": 1, "minutes": 30}, "") == 5400

    def test_parse_free_text(self):
        assert tm._parse_duration({}, "in 5 minutes") == 300
        assert tm._parse_duration({}, "90 seconds") == 90
        assert tm._parse_duration({}, "1 hour 10 minutes") == 4200

    def test_parse_garbage(self):
        assert tm._parse_duration({}, "banana") is None

    def test_missing_duration_error(self):
        out = tm.set_timer({})
        assert "need a duration" in out

    def test_set_timer_starts_thread(self):
        with (
            patch("actions.timer.threading.Thread") as thr,
            patch.object(tm.time, "sleep"),
        ):
            out = tm.set_timer({"minutes": 1})
        assert "Timer set" in out
        thr.assert_called_once()

    def test_too_long_timer_rejected(self):
        out = tm.set_timer({"hours": 10})
        assert "recommend a reminder" in out
