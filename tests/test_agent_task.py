"""
Tests for the multi-agent system (actions/agent_task.py).

Strategy: no network, no real Gemini calls — `new_gemini_client` and the
tool handlers (`_import_action`) are patched. Mirrors the approach used by
test_regressions.py for dev_agent.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from actions import agent_task as at


def _ctx():
    return {"player": None, "speak": None}


# ═══════════════════════════════════════════════════════════════════════════════
# Registry & helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistry:
    def test_expected_agents_exposed(self):
        assert {"research", "web", "code", "file", "system"} <= set(at.AGENTS)
        for name, agent in at.AGENTS.items():
            assert agent["name"] == name
            assert callable(agent["run"])

    def test_new_agents_exposed(self):
        assert {"media", "finance", "translate", "productivity", "travel", "apps"} \
            <= set(at.AGENTS)

    def test_heuristic_routing(self):
        assert at._heuristic_agent("build me a python script that renames files") == "code"
        assert at._heuristic_agent("read my notes file and summarize it") == "file"
        assert at._heuristic_agent("open github.com and tell me the star count") == "web"
        assert at._heuristic_agent("check cpu, ram and battery") == "system"
        assert at._heuristic_agent("what is the latest news about AI") == "research"

    def test_heuristic_routing_new_agents(self):
        assert at._heuristic_agent("generate an image of a sunset") == "media"
        assert at._heuristic_agent("play a youtube video about space") == "media"
        assert at._heuristic_agent("what is the price of bitcoin") == "finance"
        assert at._heuristic_agent("check AAPL stock price") == "finance"
        assert at._heuristic_agent("convert 100 usd to eur") == "finance"
        assert at._heuristic_agent("translate hello to spanish") == "translate"
        assert at._heuristic_agent("save a note about the meeting") == "productivity"
        assert at._heuristic_agent("log drink water habit") == "productivity"
        assert at._heuristic_agent("weather in new york") == "travel"
        assert at._heuristic_agent("find flights to paris") == "travel"
        assert at._heuristic_agent("book a flight from london to new york") == "travel"
        assert at._heuristic_agent("open chrome") == "apps"

    def test_strip_fences(self):
        assert at._strip_fences("```json\n{\"a\": 1}\n```") == "{\"a\": 1}"
        assert at._strip_fences("```\nplain\n```") == "plain"
        assert at._strip_fences("already clean") == "already clean"

    def test_extract_url(self):
        assert at._extract_url("open https://github.com/FatihMakes") == "https://github.com/FatihMakes"
        assert at._extract_url("go to example.com/page") == "https://example.com/page"
        assert at._extract_url("no url here") == ""

    def test_extract_path_and_language(self):
        assert at._extract_path("read C:\\tmp\\notes.txt please") == "C:\\tmp\\notes.txt"
        assert at._detect_language("write a python script") == "python"
        assert at._detect_language("make an html page") == "html"
        assert at._detect_language("nothing specific") == "python"


# ═══════════════════════════════════════════════════════════════════════════════
# Planner / synthesizer (Gemini is mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPlanning:
    def test_plan_parses_json(self):
        plan_json = '{"title": "t", "steps": [{"agent": "research", "task": "q"}]}'
        fake = MagicMock()
        fake.generate_content.return_value.text = plan_json
        with patch.object(at, "new_gemini_client", return_value=fake):
            plan = at._plan("goal", "", 6)
        assert plan["steps"][0]["agent"] == "research"
        fake.generate_content.assert_called_once()

    def test_plan_strips_markdown_fences(self):
        plan_json = '{"title": "t", "steps": [{"agent": "code", "task": "c"}]}'
        fake = MagicMock()
        fake.generate_content.return_value.text = "```json\n" + plan_json + "\n```"
        with patch.object(at, "new_gemini_client", return_value=fake):
            plan = at._plan("goal", "", 6)
        assert plan["steps"][0]["agent"] == "code"

    def test_plan_coerces_unknown_agent(self):
        plan_json = '{"steps": [{"agent": "alien", "task": "x"}]}'
        fake = MagicMock()
        fake.generate_content.return_value.text = plan_json
        with patch.object(at, "new_gemini_client", return_value=fake):
            plan = at._plan("goal", "", 6)
        assert plan["steps"][0]["agent"] == "research"

    def test_plan_rejects_bad_json(self):
        fake = MagicMock()
        fake.generate_content.return_value.text = "not json at all"
        with (
            patch.object(at, "new_gemini_client", return_value=fake),
            pytest.raises(ValueError),
        ):
            at._plan("goal", "", 6)

    def test_plan_rejects_non_dict_steps(self):
        fake = MagicMock()
        fake.generate_content.return_value.text = '{"steps": ["just a string"]}'
        with (
            patch.object(at, "new_gemini_client", return_value=fake),
            pytest.raises(TypeError),
        ):
            at._plan("goal", "", 6)

    def test_plan_raises_rate_limit_on_429(self):
        fake = MagicMock()
        fake.generate_content.side_effect = RuntimeError("429 quota exceeded")
        with (
            patch.object(at, "new_gemini_client", return_value=fake),
            pytest.raises(at.RateLimitError),
        ):
            at._plan("goal", "", 6)

    def test_plan_normalizes_parallel_default(self):
        fake = MagicMock()
        fake.generate_content.return_value.text = '{"steps": [{"agent": "code", "task": "c"}]}'
        with patch.object(at, "new_gemini_client", return_value=fake):
            plan = at._plan("goal", "", 6)
        assert plan["steps"][0]["parallel"] is False

    def test_planner_prompt_mentions_parallel(self):
        fake = MagicMock()
        fake.generate_content.return_value.text = '{"steps": [{"agent": "research", "task": "q"}]}'
        with patch.object(at, "new_gemini_client", return_value=fake):
            at._plan("goal", "", 6)
        assert "parallel" in fake.generate_content.call_args.args[0]

    def test_synthesize_returns_summary_and_report(self):
        fake = MagicMock()
        fake.generate_content.return_value.text = (
            '{"summary": "Done.", "report": "# Report"}'
        )
        with patch.object(at, "new_gemini_client", return_value=fake):
            out = at._synthesize("g", {"title": "t"}, [{"agent": "research", "task": "q", "result": "r"}])
        assert out["summary"] == "Done."
        assert out["report"] == "# Report"


# ═══════════════════════════════════════════════════════════════════════════════
# Execution
# ═══════════════════════════════════════════════════════════════════════════════


class TestExecution:
    def test_run_plan_calls_agents_in_order(self):
        plan = {"title": "t", "steps": [
            {"agent": "research", "task": "q1"},
            {"agent": "code",     "task": "q2"},
        ]}
        calls = []

        def fake_run(name, task, ctx):
            calls.append((name, task))
            return f"result-{name}"

        with patch.object(at, "_run_agent", side_effect=fake_run):
            results = at._run_plan(plan, _ctx())

        assert calls == [("research", "q1"), ("code", "q2")]
        assert results[0]["result"] == "result-research"

    def test_run_plan_continues_on_step_error(self):
        plan = {"steps": [
            {"agent": "research", "task": "q1"},
            {"agent": "code",     "task": "q2"},
        ]}
        with patch.object(at, "_run_agent", side_effect=[RuntimeError("boom"), "ok"]):
            results = at._run_plan(plan, _ctx())
        assert "failed" in results[0]["result"].lower()
        assert results[1]["result"] == "ok"

    def test_run_agent_caps_result(self):
        with patch.object(at, "AGENTS", {
            "code": {"run": lambda task, ctx: "x" * 10_000},
        }):
            out = at._run_agent("code", "t", _ctx())
        assert len(out) == at.MAX_RESULT_CHARS

    def test_locked_speak_wraps_and_serializes(self):
        assert at._locked_speak(None) is None
        calls = []
        fn = at._locked_speak(lambda text: calls.append(text))
        fn("hello")
        assert calls == ["hello"]


# ═══════════════════════════════════════════════════════════════════════════════
# Parallel wave execution (speed)
# ═══════════════════════════════════════════════════════════════════════════════


class TestParallelExecution:
    def test_parallel_steps_run_concurrently(self):
        plan = {"title": "t", "steps": [
            {"agent": "research", "task": "q1", "parallel": True},
            {"agent": "finance",  "task": "q2", "parallel": True},
        ]}
        lock, active, peak = threading.Lock(), 0, 0

        def fake_run(name, task, ctx):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1
            return f"result-{name}"

        with (
            patch.object(at, "MAX_PARALLEL", 3),
            patch.object(at, "_run_agent", side_effect=fake_run),
        ):
            results = at._run_plan(plan, _ctx())

        assert peak >= 2  # both ran at the same time
        assert [r["agent"] for r in results] == ["research", "finance"]
        assert all(isinstance(r.get("seconds"), (int, float)) for r in results)

    def test_parallel_waves_do_not_overlap_sequential_steps(self):
        plan = {"steps": [
            {"agent": "research", "task": "q1", "parallel": True},
            {"agent": "finance",  "task": "q2", "parallel": True},
            {"agent": "code",     "task": "q3"},
            {"agent": "media",    "task": "q4", "parallel": True},
            {"agent": "travel",   "task": "q5", "parallel": True},
        ]}
        events, lock = [], threading.Lock()

        def fake_run(name, task, ctx):
            t0 = time.monotonic()
            time.sleep(0.1)
            t1 = time.monotonic()
            with lock:
                events.append((name, t0, t1))
            return name

        with (
            patch.object(at, "MAX_PARALLEL", 3),
            patch.object(at, "_run_agent", side_effect=fake_run),
        ):
            results = at._run_plan(plan, _ctx())

        # Results keep the planner's order regardless of concurrency.
        assert [r["agent"] for r in results] == \
            ["research", "finance", "code", "media", "travel"]
        ev = {name: (s, e) for name, s, e in events}
        ms, me = ev["code"]
        # The sequential step overlaps neither wave.
        for other in ("research", "finance", "media", "travel"):
            os_, oe = ev[other]
            assert not (os_ < me and ms < oe), f"sequential step overlapped {other}"
        # Members of each wave overlap each other.
        assert ev["research"][0] < ev["finance"][1] and ev["finance"][0] < ev["research"][1]
        assert ev["media"][0] < ev["travel"][1] and ev["travel"][0] < ev["media"][1]

    def test_non_parallel_plan_stays_sequential(self):
        plan = {"steps": [
            {"agent": "research", "task": "q1"},
            {"agent": "code",     "task": "q2"},
        ]}
        calls = []
        with patch.object(
            at, "_run_agent",
            side_effect=lambda n, t, c: (calls.append((n, t)) or f"r-{n}"),
        ):
            results = at._run_plan(plan, _ctx())
        assert calls == [("research", "q1"), ("code", "q2")]
        assert [r["agent"] for r in results] == ["research", "code"]


# ═══════════════════════════════════════════════════════════════════════════════
# Individual agents call the right tool handlers
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentsRun:
    def test_research_agent_uses_web_search(self):
        fake = MagicMock()
        fake.web_search.return_value = "answer"
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["research"]["run"]("query", _ctx())
        assert out == "answer"
        assert fake.web_search.call_args.kwargs["parameters"]["mode"] == "research"

    def test_file_agent_reads_path(self):
        fake = MagicMock()
        fake.file_controller.return_value = "file content"
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["file"]["run"]("read C:\\tmp\\notes.txt", _ctx())
        assert out == "file content"
        assert fake.file_controller.call_args.kwargs["parameters"]["action"] == "read"

    def test_code_agent_builds_simple_script(self):
        fake = MagicMock()
        fake.code_helper.return_value = "built ok"
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["code"]["run"]("write a python script to sort files", _ctx())
        assert out == "built ok"
        kw = fake.code_helper.call_args.kwargs["parameters"]
        assert kw["action"] == "build"
        assert kw["language"] == "python"

    def test_code_agent_delegates_projects_to_dev_agent(self):
        fake = MagicMock()
        fake.dev_agent.return_value = "project built"
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["code"]["run"]("build a small web project with 3 files", _ctx())
        assert out == "project built"
        fake.dev_agent.assert_called_once()
        assert not fake.code_helper.called

    def test_system_agent_gathers_telemetry(self):
        fake = MagicMock()
        fake.get_system_status.return_value = "cpu: 12%"
        fake.battery_info.return_value = "battery: 80%"
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["system"]["run"]("check status", _ctx())
        assert "cpu: 12%" in out
        assert "battery: 80%" in out


# ═══════════════════════════════════════════════════════════════════════════════
# New sub-agents (media, finance, translate, productivity, travel, apps)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewAgentsRun:
    def test_media_agent_generates_image(self):
        fake = MagicMock()
        fake.generate_image.return_value = {"result": "Here you go.", "path": "C:\\x.png"}
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["media"]["run"]("generate an image of a cat", _ctx())
        assert "Here you go" in out
        assert "x.png" in out
        assert fake.generate_image.call_args.kwargs["parameters"]["prompt"] == \
            "generate an image of a cat"

    def test_media_agent_youtube_play(self):
        fake = MagicMock()
        fake.youtube_video.return_value = "Playing."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["media"]["run"]("play some relaxing music", _ctx())
        assert out == "Playing."
        assert fake.youtube_video.call_args.kwargs["parameters"]["action"] == "play"

    def test_media_agent_image_beats_video_keyword(self):
        # 'video game character' mentions 'video' but must stay in image generation
        fake = MagicMock()
        fake.generate_image.return_value = {"result": "ok", "path": "x.png"}
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["media"]["run"](
                "generate an image of a video game character", _ctx())
        assert fake.generate_image.called
        assert not fake.youtube_video.called

    def test_finance_agent_currency(self):
        fake = MagicMock()
        fake.currency_converter.return_value = "100 USD is about 92 EUR."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["finance"]["run"]("convert 100 usd to eur", _ctx())
        assert "92 EUR" in out
        assert fake.currency_converter.called

    def test_finance_agent_crypto(self):
        fake = MagicMock()
        fake.crypto_prices.return_value = "Bitcoin is up 2%."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["finance"]["run"]("what is the price of bitcoin", _ctx())
        assert "Bitcoin" in out
        assert fake.crypto_prices.call_args.kwargs["parameters"]["asset"] == "bitcoin"

    def test_finance_agent_stock(self):
        fake = MagicMock()
        fake.stock_prices.return_value = "AAPL is at $250."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["finance"]["run"]("check AAPL stock price", _ctx())
        assert "$250" in out
        assert fake.stock_prices.call_args.kwargs["parameters"]["ticker"] == "AAPL"

    def test_translate_agent(self):
        fake = MagicMock()
        fake.translate_text.return_value = "Hola."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["translate"]["run"]("translate hello to spanish", _ctx())
        assert out == "Hola."
        kw = fake.translate_text.call_args.kwargs["parameters"]
        assert kw["to"] == "spanish"
        # the command verb must NOT be passed to the translator
        assert kw["text"] == "hello"

    def test_translate_agent_strips_say_verb(self):
        fake = MagicMock()
        fake.translate_text.return_value = "Bonjour."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["translate"]["run"]("say good morning in french", _ctx())
        assert out == "Bonjour."
        assert fake.translate_text.call_args.kwargs["parameters"]["text"] == "good morning"

    def test_travel_agent_weather(self):
        fake = MagicMock()
        fake.weather_action.return_value = "22°C and sunny in London."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["travel"]["run"]("weather in london today", _ctx())
        assert "London" in out
        assert fake.weather_action.call_args.kwargs["parameters"]["city"] == "london"

    def test_travel_agent_flights(self):
        fake = MagicMock()
        fake.flight_finder.return_value = "Best flight: TK 100."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["travel"]["run"](
                "find flights from istanbul to new york on 2026-12-20", _ctx())
        assert "TK 100" in out
        kw = fake.flight_finder.call_args.kwargs["parameters"]
        assert kw["origin"] == "istanbul"
        assert kw["destination"] == "new york"

    def test_travel_agent_strips_flight_trailer(self):
        fake = MagicMock()
        fake.flight_finder.return_value = "Best flight: BA 202."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["travel"]["run"](
                "find a flight from london to paris tomorrow", _ctx())
        assert "BA 202" in out
        kw = fake.flight_finder.call_args.kwargs["parameters"]
        assert kw["origin"] == "london"
        assert kw["destination"] == "paris"

    def test_travel_agent_city_strips_please(self):
        assert at._extract_city("weather in london please") == "london"

    def test_apps_agent_opens_app(self):
        fake = MagicMock()
        fake.open_app.return_value = "Opened chrome."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["apps"]["run"]("open chrome", _ctx())
        assert out == "Opened chrome."
        assert fake.open_app.call_args.kwargs["parameters"]["app_name"] == "chrome"

    def test_apps_agent_settings(self):
        fake = MagicMock()
        fake.computer_settings.return_value = "Volume up."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["apps"]["run"]("turn volume up", _ctx())
        assert out == "Volume up."
        assert fake.computer_settings.call_args.kwargs["parameters"]["action"] == "volume_up"

    def test_productivity_agent_notes(self):
        fake = MagicMock()
        fake.handle.return_value = "Note saved."
        with patch.object(at, "_import_plugin", return_value=fake):
            out = at.AGENTS["productivity"]["run"]("save a note about the meeting", _ctx())
        assert out == "Note saved."
        assert fake.handle.call_args.args[0]["action"] == "add"

    def test_productivity_agent_reminder(self):
        fake = MagicMock()
        fake.reminder.return_value = "Reminder set."
        with patch.object(at, "_import_action", return_value=fake):
            out = at.AGENTS["productivity"]["run"]("remind me in 30 minutes", _ctx())
        assert out == "Reminder set."
        assert fake.reminder.called


# ═══════════════════════════════════════════════════════════════════════════════
# agent_task tool entry
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgentTaskTool:
    def test_requires_goal(self):
        out = at.agent_task({}, **_ctx())
        assert "what task" in out.lower()

    def test_unknown_agent(self):
        out = at.agent_task({"goal": "x", "agent": "nope"}, **_ctx())
        assert "don't have an agent" in out.lower()

    def test_single_agent_direct_path(self):
        with patch.object(at, "_run_single_agent", return_value="done") as m:
            out = at.agent_task({"goal": "do thing", "agent": "file"}, **_ctx())
        assert out == "done"
        m.assert_called_once_with("file", "do thing", _ctx())

    def test_auto_falls_back_to_heuristic_when_planner_fails(self):
        with (
            patch.object(at, "_plan", side_effect=RuntimeError("no api key")),
            patch.object(at, "_heuristic_agent", return_value="research") as h,
            patch.object(at, "_run_single_agent", return_value="fallback done") as rs,
        ):
            out = at.agent_task({"goal": "what is the latest ai news"}, **_ctx())
        assert out == "fallback done"
        h.assert_called_once()
        rs.assert_called_once_with("research", "what is the latest ai news", _ctx())

    def test_rate_limit_message(self):
        with patch.object(at, "_plan", side_effect=at.RateLimitError("429 exceeded")):
            out = at.agent_task({"goal": "g"}, **_ctx())
        assert "rate limit" in out.lower()

    def test_bad_max_steps_never_crashes(self):
        with patch.object(at, "_plan", side_effect=at.RateLimitError("429")):
            out = at.agent_task({"goal": "g", "max_steps": "many"}, **_ctx())
        assert "rate limit" in out.lower()
        with patch.object(at, "_plan", side_effect=at.RateLimitError("429")):
            out = at.agent_task({"goal": "g", "max_steps": None}, **_ctx())
        assert "rate limit" in out.lower()

    def test_full_pipeline(self):
        plan = {"title": "t", "steps": [{"agent": "research", "task": "q1"}]}
        results = [{"agent": "research", "task": "q1", "result": "data"}]
        with (
            patch.object(at, "_plan", return_value=plan),
            patch.object(at, "_run_plan", return_value=results),
            patch.object(at, "_synthesize", return_value={"summary": "Sum.", "report": "Rep."}),
        ):
            out = at.agent_task({"goal": "g"}, **_ctx())
        assert out == "Sum.\n\nRep."

    def test_raw_report_when_synthesis_fails(self):
        plan = {"title": "t", "steps": [{"agent": "research", "task": "q1"}]}
        results = [{"agent": "research", "task": "q1", "result": "data"}]
        with (
            patch.object(at, "_plan", return_value=plan),
            patch.object(at, "_run_plan", return_value=results),
            patch.object(at, "_synthesize", side_effect=RuntimeError("boom")),
        ):
            out = at.agent_task({"goal": "g"}, **_ctx())
        assert "data" in out
