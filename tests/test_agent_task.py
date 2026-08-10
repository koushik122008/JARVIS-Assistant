"""
Tests for the multi-agent system (actions/agent_task.py).

Strategy: no network, no real Gemini calls — `new_gemini_client` and the
tool handlers (`_import_action`) are patched. Mirrors the approach used by
test_regressions.py for dev_agent.
"""

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

    def test_heuristic_routing(self):
        assert at._heuristic_agent("build me a python script that renames files") == "code"
        assert at._heuristic_agent("read my notes file and summarize it") == "file"
        assert at._heuristic_agent("open github.com and tell me the star count") == "web"
        assert at._heuristic_agent("check cpu, ram and battery") == "system"
        assert at._heuristic_agent("what is the latest news about AI") == "research"

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
