"""
MARK XLIX — Multi-Agent System (agent_task)

The main assistant (Gemini Live) calls `agent_task` for complex multi-step goals.
A planner LLM decomposes the goal into ordered steps, then each step is executed
by a specialised sub-agent. The sub-agents are thin orchestrators that reuse the
exact same tool handlers JARVIS already exposes — no duplicated logic.

Sub-agents:
  research  — deep web research via web_search (research/news/search modes).
  web       — browser automation via browser_control: navigate, search, extract
              page text, then answer with Gemini.
  code      — write / run / fix code via code_helper; multi-file projects are
              delegated to dev_agent.
  file      — read / list / find / summarise local files via file_controller
              and file_processor.
  system    — hardware & system telemetry (CPU/RAM/GPU/temp, battery).

The planner is only used when JARVIS routes here with agent="auto" (the default).
If planning fails (no API key, bad JSON, rate limit) we fall back to a keyword
heuristic and run the single best-matching agent so the request never dies.
"""

import importlib
import json
import re
import time

from utils import new_gemini_client

PLANNER_MODEL = "gemini-2.5-flash"
AGENT_MODEL   = "gemini-2.5-flash"
MAX_STEPS_DEFAULT = 6
MAX_STEPS_LIMIT   = 10
MAX_RESULT_CHARS  = 4000

# ── Small helpers ─────────────────────────────────────────────────────────────


def _import_action(name: str):
    """Lazy-import an actions/ module — keeps this module cheap to import."""
    return importlib.import_module(f"actions.{name}")


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` (or any ```lang ... ```) fences if present."""
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _is_rate_limit(error: Exception) -> bool:
    msg = str(error).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg


class RateLimitError(Exception):
    """Raised when the Gemini API is rate-limiting us."""


def _log(ctx: dict, msg: str) -> None:
    """Console + UI log line (UI is optional)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))
    player = ctx.get("player")
    if player:
        try:
            player.write_log(msg)
        except Exception:  # noqa: S110, BLE001 — UI logging must never crash the agent
            pass


# ── Path / URL / term extraction (no-LLM heuristics) ─────────────────────────

_PATH_RE = re.compile(
    r"([A-Za-z]:[\\/][^\s\"']+|[\\/][^\s\"']+\.[A-Za-z0-9]{1,5}"
    r"|[^\s\"']+\.[A-Za-z0-9]{1,5})"
)


def _extract_path(task: str) -> str:
    m = _PATH_RE.search(task or "")
    return m.group(1) if m else ""


def _extract_term(task: str) -> str:
    m = re.search(r"[\"']([^\"']+)[\"']", task or "")
    if m:
        return m.group(1).strip()
    words = [w for w in re.split(r"\W+", task or "") if w]
    return words[-1] if words else ""


def _extract_url(task: str) -> str:
    m = re.search(r"https?://[^\s\"']+", task or "", re.IGNORECASE)
    if m:
        return m.group(0)
    # Bare domain like "github.com/foo/bar" or "example.com"
    m = re.search(r"\b([\w-]+\.)+[a-z]{2,}(?:/[^\s\"']*)?", task or "", re.IGNORECASE)
    if m:
        return "https://" + m.group(0)
    return ""


# ── Sub-agent implementations ─────────────────────────────────────────────────


def _research_agent(task: str, ctx: dict) -> str:
    """Deep web research on the given question."""
    tool = _import_action("web_search")
    return tool.web_search(
        parameters={"query": task, "mode": "research"},
        player=ctx.get("player"),
    )


def _web_agent(task: str, ctx: dict) -> str:
    """Navigate to a page (or search), extract text, answer the task with Gemini."""
    player = ctx.get("player")
    tool   = _import_action("browser_control")

    url = _extract_url(task)
    if url:
        tool.browser_control(parameters={"action": "go_to", "url": url}, player=player)
    else:
        tool.browser_control(parameters={"action": "search", "query": task}, player=player)

    time.sleep(1.5)  # let the page settle (we are already in a worker thread)

    text = tool.browser_control(parameters={"action": "get_text"}, player=player) or ""
    text = str(text)[:MAX_RESULT_CHARS]

    model = new_gemini_client(AGENT_MODEL)
    prompt = (
        "You are a web-extraction agent. Using ONLY the page text below, answer "
        "the user's task. Be concise and specific. If the page text does not "
        "contain the answer, say exactly that — do not invent facts.\n\n"
        f"Task: {task}\n\n"
        f"Page text:\n{text[:3000]}\n\nAnswer:"
    )
    try:
        return _strip_fences(model.generate_content(prompt).text)
    except Exception as e:  # noqa: BLE001 — degrade gracefully to raw page text
        return f"Page text captured, but the summariser failed ({e}):\n\n{text[:1500]}"


def _code_agent(task: str, ctx: dict) -> str:
    """Write/run/fix code via code_helper; multi-file projects go to dev_agent."""
    player = ctx.get("player")
    speak  = ctx.get("speak")
    tool   = _import_action("code_helper")
    lang   = _detect_language(task)
    low    = (task or "").lower()

    if any(h in low for h in ("project", "multi-file", "multi file",
                              "several files", "package that")):
        da = _import_action("dev_agent")
        return da.dev_agent(
            parameters={"description": task, "language": lang},
            player=player, speak=speak,
        )

    return tool.code_helper(
        parameters={"action": "build", "description": task, "language": lang},
        player=player, speak=speak,
    )


_LANG_HINTS = (
    ("python",      r"\b(python|py script|\.py)\b"),
    ("javascript",  r"\b(javascript|js script|\.js)\b"),
    ("typescript",  r"\b(typescript|\.ts)\b"),
    ("html",        r"\b(html|web page|\.html)\b"),
    ("css",         r"\b(css|stylesheet|\.css)\b"),
    ("bash",        r"\b(bash|shell script|\.sh)\b"),
    ("sql",         r"\b(sql|query|database script)\b"),
)


def _detect_language(task: str) -> str:
    low = (task or "").lower()
    for lang, pat in _LANG_HINTS:
        if re.search(pat, low):
            return lang
    return "python"


def _file_agent(task: str, ctx: dict) -> str:
    """Read / list / find local files; analyse them via file_processor."""
    player = ctx.get("player")
    speak  = ctx.get("speak")
    tool   = _import_action("file_controller")

    path = _extract_path(task)
    if path:
        low = (task or "").lower()
        if re.search(r"\b(summarize|analyse|analyze|describe|what is|about|review)\b", low):
            fp = _import_action("file_processor")
            return fp.file_processor(
                parameters={"file_path": path, "instruction": task},
                player=player, speak=speak,
            )
        return tool.file_controller(
            parameters={"action": "read", "path": path}, player=player,
        )

    if re.search(r"\b(find|search)\b", (task or ""), re.IGNORECASE):
        return tool.file_controller(
            parameters={"action": "find", "name": _extract_term(task)}, player=player,
        )

    return tool.file_controller(
        parameters={"action": "list", "path": "home"}, player=player,
    )


def _system_agent(task: str, ctx: dict) -> str:
    """Gather hardware & system telemetry."""
    sm   = _import_action("system_monitor")
    parts = [str(sm.get_system_status())]
    try:
        bat = _import_action("battery_info").battery_info()
        if bat:
            parts.append(str(bat))
    except Exception:  # noqa: S110, BLE001 — battery is optional telemetry
        pass
    return "\n\n".join(p for p in parts if p)


# ── Agent registry ────────────────────────────────────────────────────────────

AGENTS: dict = {
    "research": {
        "name":        "research",
        "description": "Deep web research on a topic or question (multiple sources, cited answer).",
        "run":         _research_agent,
    },
    "web": {
        "name":        "web",
        "description": "Opens websites, extracts page content, and answers questions from a page.",
        "run":         _web_agent,
    },
    "code": {
        "name":        "code",
        "description": "Writes, runs and fixes code; builds complete multi-file projects.",
        "run":         _code_agent,
    },
    "file": {
        "name":        "file",
        "description": "Reads, lists, finds and summarises local files and folders.",
        "run":         _file_agent,
    },
    "system": {
        "name":        "system",
        "description": "Gathers system status, hardware telemetry and battery info.",
        "run":         _system_agent,
    },
}


# ── Planner & synthesizer (Gemini) ────────────────────────────────────────────


def _plan(goal: str, context: str, max_steps: int) -> dict:
    """Decompose the goal into ordered agent steps. Returns a dict with 'steps'."""
    model = new_gemini_client(PLANNER_MODEL)

    agent_docs = "\n".join(
        f"  - {a['name']}: {a['description']}" for a in AGENTS.values()
    )

    prompt = (
        "You are the task planner for a personal AI assistant's agent system.\n"
        "Decompose the user's goal into an ordered plan of steps. Each step is "
        "executed by exactly ONE specialised agent.\n\n"
        "Available agents:\n"
        f"{agent_docs}\n\n"
        "Rules:\n"
        f"1. At most {max_steps} steps — prefer fewer, high-value steps.\n"
        "2. Each step is a single responsibility; split different kinds of work "
        "into different steps with the matching agent.\n"
        "3. Steps run sequentially, so order matters (research first, then code, etc.).\n"
        "4. Every 'task' must be self-contained and give the agent everything it "
        "needs (specific queries, URLs, file paths, languages).\n"
        "5. Never invent a step that changes the user's system without being asked.\n\n"
        f"User goal: {goal}\n"
        + (f"Extra context: {context}\n" if context else "")
        + (
            "Return ONLY valid JSON, no markdown, no explanation:\n"
            "{\n"
            '  "title": "short 3-6 word title",\n'
            '  "steps": [\n'
            '    {"agent": "research|web|code|file|system", "task": "..."}\n'
            "  ]\n"
            "}\n\nJSON:"
        )
    )

    try:
        response = model.generate_content(prompt)
        raw      = _strip_fences(response.text)
        plan     = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {e}") from e
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e)) from e
        raise

    if not isinstance(plan, dict):
        raise TypeError("Planner returned non-object JSON")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Planner returned no steps")
    for step in steps:
        if not isinstance(step, dict):
            raise TypeError("Planner returned malformed steps")
        agent = str(step.get("agent", "research")).strip().lower()
        if agent not in AGENTS:
            step["agent"] = "research"   # coerce unknown → research
    return plan


def _synthesize(goal: str, plan: dict, results: list) -> dict:
    """Turn step results into a spoken summary + a structured written report."""
    model = new_gemini_client(AGENT_MODEL)

    step_lines = "\n".join(
        f"[{i}] ({r['agent']}) {r['task']}\n{str(r['result'])[:2000]}"
        for i, r in enumerate(results, 1)
    )

    prompt = (
        "You are the synthesizer for an AI assistant's agent run.\n"
        f"User goal: {goal}\n\n"
        f"Agent step results:\n{step_lines}\n\n"
        "Write the final answer as ONLY valid JSON (no markdown):\n"
        "{\n"
        '  "summary": "2-4 short spoken sentences that directly answer the user\'s '
        'goal. Natural spoken tone, no headings, no markdown, no JSON. It will be '
        'read aloud by a voice assistant.",\n'
        '  "report": "A structured markdown report with a short title, a section '
        'per step, key findings with concrete facts, and a one-line conclusion."\n'
        "}\n\n"
        "If a step failed, say so honestly in both fields.\n\nJSON:"
    )

    try:
        response = model.generate_content(prompt)
        data     = json.loads(_strip_fences(response.text))
    except json.JSONDecodeError:
        # Last-resort: build a plain summary from the raw results.
        fallback = " ".join(str(r.get("result", ""))[:400] for r in results)
        return {
            "summary": f"I completed the task. {fallback}"[:600],
            "report":  "\n\n".join(
                f"## Step {i} — {r['agent']}\n{r['result']}" for i, r in enumerate(results, 1)
            ),
        }
    except Exception as e:
        if _is_rate_limit(e):
            raise RateLimitError(str(e)) from e
        raise

    return {
        "summary": str(data.get("summary", "")).strip() or "Task completed.",
        "report":  str(data.get("report", "")).strip(),
    }


# ── Execution ─────────────────────────────────────────────────────────────────


def _run_agent(name: str, task: str, ctx: dict) -> str:
    if name not in AGENTS:
        return f"[step skipped: unknown agent '{name}']"
    try:
        out = AGENTS[name]["run"](task, ctx) or ""
        return str(out)[:MAX_RESULT_CHARS]
    except Exception as e:  # noqa: BLE001 — a failing agent step must not crash the plan
        return f"[step failed: {e}]"


def _run_plan(plan: dict, ctx: dict) -> list:
    steps = plan.get("steps", []) or []
    results: list = []
    for i, step in enumerate(steps, 1):
        agent = str(step.get("agent", "research")).strip().lower()
        task  = str(step.get("task", "")).strip() or "(no task)"
        if agent not in AGENTS:
            agent = "research"
        _log(ctx, f"[Agents] Step {i}/{len(steps)} → {agent}: {task[:90]}")
        try:
            out = _run_agent(agent, task, ctx)
        except Exception as e:  # noqa: BLE001 — one bad step must not kill the plan
            out = f"[step failed: {e}]"
        results.append({"agent": agent, "task": task, "result": str(out)[:MAX_RESULT_CHARS]})
    return results


def _heuristic_agent(goal: str) -> str:
    """No-LLM fallback: route the goal to the single best-matching agent."""
    low = (goal or "").lower()

    if re.search(r"\b(code|script|program|app|project|build|function|fix this code|write a)\b", low):
        return "code"
    if re.search(r"\b(file|folder|document|read the file|notes|downloads)\b", low):
        return "file"
    if re.search(r"\b(website|web page|browser|open .*\.com|visit|go to|url)\b", low):
        return "web"
    if re.search(r"\b(cpu|ram|battery|temperature|system status|performance|gpu|hardware)\b", low):
        return "system"
    return "research"


def _run_single_agent(agent_name: str, goal: str, ctx: dict) -> str:
    """Run one agent and wrap its output into summary + report."""
    raw = _run_agent(agent_name, goal, ctx)
    try:
        final = _synthesize(
            goal,
            {"title": agent_name, "steps": [{"agent": agent_name, "task": goal}]},
            [{"agent": agent_name, "task": goal, "result": raw}],
        )
        return f"{final['summary']}\n\n{final['report']}".strip()
    except Exception:  # noqa: BLE001 — fall back to the raw agent output
        return raw


# ── Public tool entry (called from main.py) ───────────────────────────────────


def agent_task(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    """
    JARVIS tool — delegate a complex multi-step goal to the agent system.

    parameters:
        goal       : (required) the complete task to accomplish
        agent      : optional — auto (default) | research | web | code | file | system
        context    : optional extra context (preferences, constraints, paths)
        max_steps  : optional planner step cap (default 6, max 10)
    """
    p         = parameters or {}
    goal      = str(p.get("goal", "")).strip()
    agent     = str(p.get("agent", "auto") or "auto").strip().lower()
    context   = str(p.get("context", "")).strip()
    try:
        max_steps = int(p.get("max_steps", MAX_STEPS_DEFAULT) or MAX_STEPS_DEFAULT)
    except (TypeError, ValueError):
        max_steps = MAX_STEPS_DEFAULT   # never crash on a bad parameter
    max_steps = max(1, min(max_steps, MAX_STEPS_LIMIT))

    if not goal:
        return "Please tell me what task you want me to run, sir."

    ctx = {"player": player, "speak": speak}
    _log(ctx, f"[Agents] ▶ goal: {goal[:100]}  agent={agent}  max_steps={max_steps}")

    # ── Direct single-agent path ─────────────────────────────────────────────
    if agent != "auto":
        if agent not in AGENTS:
            return (
                f"I don't have an agent named '{agent}', sir. "
                f"Available: {', '.join(AGENTS)}."
            )
        return _run_single_agent(agent, goal, ctx)

    # ── Planner path ─────────────────────────────────────────────────────────
    try:
        plan = _plan(goal, context, max_steps)
    except RateLimitError:
        msg = "Rate limit reached, sir. Please try the task again in a moment."
        _log(ctx, "[Agents] ⚠️ " + msg)
        if speak:
            speak(msg)
        return msg
    except Exception as e:  # noqa: BLE001 — degraded mode is the intended fallback
        # Degraded mode: no planner available → route heuristically to one agent.
        _log(ctx, f"[Agents] ⚠️ Planner failed ({e}) — heuristic fallback.")
        fallback = _heuristic_agent(goal)
        _log(ctx, f"[Agents] ↩ Using single agent: {fallback}")
        return _run_single_agent(fallback, goal, ctx)

    results = _run_plan(plan, ctx)

    try:
        final = _synthesize(goal, plan, results)
    except RateLimitError:
        msg = ("Rate limit reached while summarising, sir. The step results are below.")
        _log(ctx, "[Agents] ⚠️ " + msg)
        return msg + "\n\n" + _plan_to_text(plan, results)
    except Exception as e:  # noqa: BLE001 — raw results beat a crashed summary
        _log(ctx, f"[Agents] ⚠️ Synthesis failed ({e}) — returning raw results.")
        return _plan_to_text(plan, results)

    _log(ctx, "[Agents] ✅ Task complete.")
    return f"{final['summary']}\n\n{final['report']}".strip()


def _plan_to_text(plan: dict, results: list) -> str:
    """Plain-text report of steps + results (used when synthesis is unavailable)."""
    title = (plan or {}).get("title", "Task report")
    lines = [f"# {title}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"## Step {i} — {r['agent']}")
        lines.append(r["task"])
        lines.append("")
        lines.append(str(r["result"])[:MAX_RESULT_CHARS])
        lines.append("")
    return "\n".join(lines)
