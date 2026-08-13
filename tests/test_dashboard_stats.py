"""
Tests for the JARVIS dashboard plugin-stats aggregators
(dashboard/server.py — habits / expenses / workouts).
"""

import asyncio
import json
from datetime import date, timedelta
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from dashboard import server as dash


def _write(monkeypatch, tmp_path, fname, data):
    monkeypatch.setattr(dash, "BASE_DIR", tmp_path)
    d = tmp_path / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(json.dumps(data), encoding="utf-8")


# ── habits ─────────────────────────────────────────────────────────────────────

def test_habits_streak_today_and_sorting(monkeypatch, tmp_path):
    today = date.today().isoformat()
    _write(monkeypatch, tmp_path, "habits.json", {"habits": {
        "Drink Water": {"created": today, "log": [today]},
        "Meditation":  {"created": today,
                        "log": [(date.today() - timedelta(days=1)).isoformat()]},
    }})
    s = dash._stats_habits()
    assert s["total"] == 2
    assert s["done_today"] == 1
    by_name = {h["name"]: h for h in s["habits"]}
    assert by_name["Drink Water"]["done_today"] is True
    assert by_name["Drink Water"]["streak"] == 1
    assert by_name["Meditation"]["done_today"] is False
    # done-today habit sorts to the top
    assert s["habits"][0]["name"] == "Drink Water"


def test_habits_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, "BASE_DIR", tmp_path)
    s = dash._stats_habits()
    assert s["habits"] == []
    assert s["total"] == 0
    assert s["done_today"] == 0


# ── expenses ───────────────────────────────────────────────────────────────────

def test_expenses_totals_budget_and_categories(monkeypatch, tmp_path):
    today = date.today()
    month = today.strftime("%Y-%m")
    _write(monkeypatch, tmp_path, "expenses.json", {
        "budget": {month: 100.0},
        "entries": [
            {"amount": 25.0, "desc": "lunch",     "cat": "food",
             "date": today.isoformat(), "ts": "13:00"},
            {"amount": 60.0, "desc": "groceries", "cat": "groceries",
             "date": today.isoformat(), "ts": "18:00"},
            {"amount": 5.0,  "desc": "old",       "cat": "food",
             "date": (today - timedelta(days=40)).isoformat(), "ts": "09:00"},
        ],
    })
    s = dash._stats_expenses()
    assert s["month"] == month
    assert s["total"] == 85.0
    assert s["count"] == 2
    assert s["budget"] == 100.0
    assert s["budget_left"] == 15.0
    assert s["week_total"] == 85.0  # both current entries fall inside 7 days
    cats = {c["cat"]: c for c in s["by_category"]}
    assert cats["food"]["amount"] == 25.0
    assert cats["groceries"]["amount"] == 60.0
    assert cats["groceries"]["pct"] == pytest.approx(70.6, abs=0.1)
    # most recent first
    assert s["recent"][0]["desc"] == "groceries"
    assert s["recent"][0]["cat"] == "groceries"


def test_expenses_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, "BASE_DIR", tmp_path)
    s = dash._stats_expenses()
    assert s["total"] == 0.0
    assert s["count"] == 0
    assert s["budget"] is None
    assert s["budget_left"] is None
    assert s["by_category"] == []
    assert s["recent"] == []


# ── workouts ───────────────────────────────────────────────────────────────────

def test_workouts_week_volume_and_pr(monkeypatch, tmp_path):
    today = date.today().isoformat()
    old   = (date.today() - timedelta(days=20)).isoformat()
    _write(monkeypatch, tmp_path, "workout_log.json", {"entries": [
        {"date": today, "ts": "18:00", "exercise": "Bench Press",
         "sets": 3, "reps": 5, "weight": 100.0},
        {"date": today, "ts": "18:30", "exercise": "Squat",
         "sets": 5, "reps": 5, "weight": 80.0},
        {"date": old,   "ts": "10:00", "exercise": "Bench Press",
         "sets": 3, "reps": 5, "weight": 90.0},
    ]})
    s = dash._stats_workouts()
    assert s["week_days"] == 1
    assert s["week_sets"] == 8
    assert s["week_volume"] == 3500  # 3*5*100 + 5*5*80
    assert s["total_entries"] == 3
    by_ex = {x["exercise"]: x for x in s["by_exercise"]}
    bp = by_ex["Bench Press"]
    assert bp["pr_weight"] == 100.0
    assert bp["pr_date"] == today
    assert bp["volume"] == 2850   # 3*5*100 + 3*5*90
    assert bp["reps_total"] == 30
    # sorted by volume desc → Bench Press first
    assert s["by_exercise"][0]["exercise"] == "Bench Press"


def test_workouts_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, "BASE_DIR", tmp_path)
    s = dash._stats_workouts()
    assert s["week_days"] == 0
    assert s["week_sets"] == 0
    assert s["week_volume"] == 0
    assert s["total_entries"] == 0
    assert s["by_exercise"] == []
    assert s["recent"] == []


# ── HTTP endpoint ──────────────────────────────────────────────────────────────

def test_stats_endpoint_auth():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    srv    = dash.DashboardServer()
    client = TestClient(srv.app)

    # no bearer token → 401
    assert client.get("/api/stats").status_code == 401

    # with token → 200 and all three sections present
    srv._tokens.add("tok123")
    r = client.get("/api/stats", headers={"Authorization": "Bearer tok123"})
    assert r.status_code == 200
    assert set(r.json()) == {"habits", "expenses", "workouts"}


# ── Client-aware broadcast (idle fast-path) ────────────────────────────────────

def test_has_clients_false_when_nobody_connected():
    srv = dash.DashboardServer()
    assert srv.has_clients() is False


def test_has_clients_true_when_a_client_is_connected():
    srv = dash.DashboardServer()
    srv._clients.add(object())
    assert srv.has_clients() is True
    srv._clients.clear()
    assert srv.has_clients() is False


def test_broadcast_keeps_history_but_sends_nothing_without_clients():
    """With zero clients, history still grows (for replay-on-connect) but nothing sends."""
    srv = dash.DashboardServer()
    asyncio.run(srv.broadcast({"type": "log", "text": "hello"}))
    assert len(srv._history) == 1          # replay-on-connect preserved
    assert srv.has_clients() is False


def test_broadcast_grows_history_and_sends_when_clients_exist():
    srv = dash.DashboardServer()
    fake = mock.MagicMock()
    fake.send_json = AsyncMock()
    srv._clients.add(fake)
    asyncio.run(srv.broadcast({"type": "log", "text": "hello"}))
    assert len(srv._history) == 1
    fake.send_json.assert_awaited_once()


def test_broadcast_drops_dead_clients():
    srv = dash.DashboardServer()
    dead = mock.MagicMock()
    dead.send_json = AsyncMock(side_effect=RuntimeError("gone"))
    srv._clients.add(dead)
    asyncio.run(srv.broadcast({"type": "log", "text": "x"}))
    assert srv.has_clients() is False
