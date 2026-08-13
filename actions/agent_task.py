"""
MARK XLIX — Multi-Agent System (agent_task)

The main assistant (Gemini Live) calls `agent_task` for complex multi-step goals.
A planner LLM decomposes the goal into ordered steps, then each step is executed
by a specialised sub-agent. The sub-agents are thin orchestrators that reuse the
exact same tool handlers JARVIS already exposes — no duplicated logic.

Sub-agents:
  research     — deep web research via web_search (research/news/search modes).
  web          — browser automation via browser_control: navigate, search, extract
                 page text, then answer with Gemini.
  code         — write / run / fix code via code_helper; multi-file projects are
                 delegated to dev_agent.
  file         — read / list / find / summarise local files via file_controller
                 and file_processor.
  system       — hardware & system telemetry (CPU/RAM/GPU/temp, battery).
  media        — generate images (image_generator) and handle YouTube (play,
                 summarise, info, trending).
  finance      — stock quotes, crypto prices and currency conversion.
  translate    — language translation (translate_text).
  productivity — notes/to-dos, habit tracker, pomodoro, countdowns and reminders
                 (plugins + reminder action).
  travel       — weather reports and flight finding.
  apps         — open apps, desktop control, computer settings & control.

The planner is only used when JARVIS routes here with agent="auto" (the default).
If planning fails (no API key, bad JSON, rate limit) we fall back to a keyword
heuristic and run the single best-matching agent so the request never dies.
"""

import importlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from utils import new_gemini_client

PLANNER_MODEL = "gemini-2.5-flash"
AGENT_MODEL   = "gemini-2.5-flash"
MAX_STEPS_DEFAULT = 6
MAX_STEPS_LIMIT   = 10
MAX_RESULT_CHARS  = 4000
# Max steps that may run concurrently in one wave — capped by CPU cores so a
# 2-core machine never fires 3 parallel agents (agents are mostly I/O bound, but
# image generation / PIL work is CPU-bound).
MAX_PARALLEL = max(1, min(3, (os.cpu_count() or 2) - 1))

# Serialises spoken updates when steps run in parallel, so two agents can never
# talk over each other.
_speak_lock = threading.Lock()

# ── Small helpers ─────────────────────────────────────────────────────────────


def _import_action(name: str):
    """Lazy-import an actions/ module — keeps this module cheap to import."""
    return importlib.import_module(f"actions.{name}")


def _import_plugin(name: str):
    """Lazy-import a plugins/ module (plugin handlers use handle(args, ctx))."""
    return importlib.import_module(f"plugins.{name}")


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


def _locked_speak(speak):
    """Wrap a speak callable so parallel agents cannot speak simultaneously."""
    if speak is None:
        return None

    def _wrapped(text: str):
        with _speak_lock:
            return speak(text)

    return _wrapped


def _duration_str(seconds) -> str:
    """' (12.3s)' when a numeric duration is present, else ''."""
    if isinstance(seconds, (int, float)):
        return f" ({seconds:.1f}s)"
    return ""


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


# ── Media agent: images & YouTube ──────────────────────────────────────────────

_MEDIA_CRYPTO_WORDS = ("bitcoin", "btc", "ethereum", "eth", "solana", "sol",
                       "doge", "dogecoin", "xrp", "bnb", "cardano", "ada")
_MEDIA_SETTINGS_HINTS = (
    ("volume up", "volume_up"), ("volume down", "volume_down"),
    ("mute", "mute"), ("unmute", "unmute"),
    ("brightness up", "brightness_up"), ("brightness down", "brightness_down"),
    ("full screen", "full_screen"), ("fullscreen", "full_screen"),
    ("minimize", "minimize"), ("maximize", "maximize"),
    ("close app", "close_app"), ("close window", "close_window"),
    ("close tab", "close_tab"), ("new tab", "new_tab"),
    ("next tab", "next_tab"), ("previous tab", "prev_tab"), ("prev tab", "prev_tab"),
    ("go back", "go_back"), ("go forward", "go_forward"),
    ("refresh", "refresh_page"), ("reload", "reload"),
    ("zoom in", "zoom_in"), ("zoom out", "zoom_out"), ("zoom reset", "zoom_reset"),
    ("scroll up", "scroll_up"), ("scroll down", "scroll_down"),
    ("screenshot", "screenshot"), ("lock screen", "lock_screen"),
    ("dark mode", "dark_mode"), ("wifi", "toggle_wifi"),
    ("show desktop", "show_desktop"), ("switch window", "switch_window"),
    ("task manager", "task_manager"), ("sleep", "sleep_display"),
    ("screen off", "screen_off"),
)


def _extract_ticker(task: str) -> str:
    """Pull a plausible stock ticker (uppercase token) out of free text."""
    m = re.search(r"\b[A-Z]{1,5}\b", task or "")
    return m.group(0) if m else ""


def _extract_crypto(task: str) -> str:
    """Find a known crypto name/ticker in the task, else empty."""
    low = (task or "").lower()
    for word in _MEDIA_CRYPTO_WORDS:
        if re.search(rf"\b{word}\b", low):
            return word
    return ""


_CITY_TRAILERS = ("today", "tomorrow", "tonight", "now", "please", "this morning",
                   "this afternoon", "this evening", "this week", "this weekend")

_FLIGHT_TRAILERS = ("tomorrow", "today", "tonight", "next week", "next month",
                    "this week", "this weekend")


def _extract_city(task: str) -> str:
    """Pull a city name from 'weather in X' / 'weather for X' / 'X weather'."""
    m = re.search(r"\bweather\s+(?:in|at|for|of)\s+([A-Za-z][A-Za-z .'-]{1,40})",
                  task or "", re.IGNORECASE)
    if not m:
        m = re.search(r"\b(?:in|at|for)\s+([A-Z][a-zA-Z .'-]{1,40})\b", task or "")
    if m and "weather" in (task or "").lower():
        city = m.group(1).strip().rstrip(".;,!")
        for trailer in _CITY_TRAILERS:
            if city.lower().endswith(trailer):
                city = city[:-len(trailer)].strip()
                break
        return city
    return ""


def _extract_target_lang(task: str) -> tuple[str, str]:
    """Split 'translate X to Spanish' into (text, target_language).

    Strips a leading 'translate / say / convert' verb so the translator does not
    end up translating the command word itself.
    """
    m = re.search(r"\b(?:to|into|in)\s+([A-Za-z]{2,30})\s*$", task or "", re.IGNORECASE)
    if m:
        text = (task[:m.start()]).strip().rstrip(",;:.!? ")
        text = re.sub(
            r"^(?:please\s+)?(?:translate|translation|say|convert)\s+(?:this|that|the|my|following|text)?\s*",
            "", text, flags=re.IGNORECASE,
        ).strip()
        return text, m.group(1)
    return (task or "").strip(), ""


def _extract_settings_action(task: str) -> str:
    """Map free-text to a computer_settings action name (or '')."""
    low = (task or "").lower()
    for hint, action in _MEDIA_SETTINGS_HINTS:
        if hint in low:
            return action
    if re.search(r"\bvolume\s+(?:to\s+)?(\d+)\b", low):
        return "volume_set"
    return ""


def _extract_control_action(task: str) -> str:
    """Map free-text to a computer_control action name (or '')."""
    low = (task or "").lower()
    if "double click" in low or "double-click" in low:
        return "double_click"
    if "right click" in low or "right-click" in low:
        return "right_click"
    if re.search(r"\b(click|tap)\b", low):
        return "click"
    if "drag" in low:
        return "drag"
    if "hotkey" in low:
        return "hotkey"
    if re.search(r"\b(press|hit|key)\b", low):
        return "press"
    if "scroll" in low:
        return "scroll"
    if "type" in low or "write" in low:
        return "type"
    if "screenshot" in low:
        return "screenshot"
    if "move" in low:
        return "move"
    return ""


def _media_agent(task: str, ctx: dict) -> str:
    """Generate images (Imagen/PIL) or handle YouTube (play/summarize/trending)."""
    player = ctx.get("player")
    speak  = ctx.get("speak")
    low    = (task or "").lower()

    # YouTube requests (URLs, or explicit play/summarize/trending verbs).
    # Note: bare 'video' is NOT enough on its own — 'generate an image of a
    # video game character' must stay in image generation.
    if _extract_url(task) or re.search(
            r"\b(youtube|play|trending|summarize|watch|music video|video about|video of)\b", low):
        yt = _import_action("youtube_video")
        if "trending" in low:
            return yt.youtube_video(parameters={"action": "trending"}, player=player, speak=speak)
        url = _extract_url(task)
        if re.search(r"\b(summarize|summary|about)\b", low) or url:
            return yt.youtube_video(
                parameters={"action": "summarize", "url": url} if url
                            else {"action": "summarize", "query": task},
                player=player, speak=speak,
            )
        if re.search(r"\b(info|details|stats)\b", low):
            return yt.youtube_video(
                parameters={"action": "get_info", "url": url} if url
                            else {"action": "get_info", "query": task},
                player=player, speak=speak,
            )
        return yt.youtube_video(
            parameters={"action": "play", "query": task}, player=player, speak=speak)

    # Everything else is image generation.
    ig = _import_action("image_generator")
    result = ig.generate_image(parameters={"prompt": task})
    if isinstance(result, dict):
        out = str(result.get("result") or "Generated.")
        path = result.get("path")
        return f"{out} Saved to {path}." if path else out
    return str(result)


# ── Finance agent: stocks / crypto / currency ──────────────────────────────────


def _finance_agent(task: str, ctx: dict) -> str:
    """Stock quotes, crypto prices, or currency conversion from free text."""
    player = ctx.get("player")
    low    = (task or "").lower()

    if re.search(r"\b(convert|exchange|currency|dollars? to|euros? to|pounds? to|yen to|usd|eur|gbp|inr)\b", low):
        cur = _import_action("currency_converter")
        return cur.currency_converter(parameters={"query": task}, player=player)

    coin = _extract_crypto(task)
    if coin or re.search(r"\b(crypto|coin|wallet)\b", low):
        cry = _import_action("crypto_prices")
        return cry.crypto_prices(parameters={"asset": coin or task}, player=player)

    ticker = _extract_ticker(task)
    st = _import_action("stock_prices")
    return st.stock_prices(parameters={"ticker": ticker or task}, player=player)


# ── Translate agent ────────────────────────────────────────────────────────────


def _translate_agent(task: str, ctx: dict) -> str:
    """Translate text between languages."""
    player = ctx.get("player")
    text, target = _extract_target_lang(task)
    tr = _import_action("translator")
    return tr.translate_text(
        parameters={"text": text or task, "to": target}, player=player)


# ── Productivity agent: notes / habits / pomodoro / countdown / reminders ──────


def _productivity_agent(task: str, ctx: dict) -> str:
    """Route to notes, habit tracker, pomodoro, countdown or reminder."""
    player = ctx.get("player")
    speak  = ctx.get("speak")
    low    = (task or "").lower()
    pctx   = {"ui": player, "speak": speak}

    if re.search(r"\b(remind|reminder|remind me)\b", low):
        rm = _import_action("reminder")
        return rm.reminder(parameters={"when": task}, player=player)

    if re.search(r"\b(habit|streak|log (water|run|read|workout|meditat))\b", low):
        mod = _import_plugin("habit")
        if re.search(r"\b(add|create|new)\b", low):
            args = {"action": "add", "name": _after_keyword(low, "habit")}
        elif re.search(r"\b(log|done|today)\b", low):
            args = {"action": "log", "name": _after_keyword(low, "log")}
        elif re.search(r"\b(remove|delete)\b", low):
            args = {"action": "remove", "name": _after_keyword(low, "habit")}
        elif re.search(r"\b(overview|streak|status)\b", low):
            args = {"action": "overview"}
        else:
            args = {"action": "overview"}
        return mod.handle(args, pctx)

    if re.search(r"\b(pomodoro|focus session|focus timer|work session|break)\b", low):
        mod = _import_plugin("pomodoro")
        if re.search(r"\b(status|remaining|time left)\b", low):
            return mod.handle({"action": "status"}, pctx)
        if re.search(r"\b(stop|cancel)\b", low):
            return mod.handle({"action": "stop"}, pctx)
        if re.search(r"\b(today|history)\b", low):
            return mod.handle({"action": "today"}, pctx)
        if re.search(r"\b(break|rest)\b", low):
            return mod.handle({"action": "break"}, pctx)
        m = re.search(r"(\d+)\s*(min|minute|minutes)?", low)
        args = {"action": "start"}
        if m:
            args["minutes"] = int(m.group(1))
        return mod.handle(args, pctx)

    if re.search(r"\b(countdown|days until|birthday|event|anniversary)\b", low):
        mod = _import_plugin("countdown")
        if re.search(r"\b(add|save|create)\b", low):
            args = {"action": "add", "name": _after_keyword(low, "event")}
            d = re.search(r"\b(\d{4}-\d{2}-\d{2}|[A-Za-z]+ \d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b", task)
            if d:
                args["date"] = d.group(1)
            return mod.handle(args, pctx)
        if re.search(r"\b(list|all|next)\b", low):
            return mod.handle({"action": "list"}, pctx)
        if re.search(r"\b(remove|delete)\b", low):
            return mod.handle({"action": "remove", "name": _after_keyword(low, "event")}, pctx)
        return mod.handle({"action": "days", "name": _after_keyword(low, "until")}, pctx)

    # Default: notes & to-dos.
    mod = _import_plugin("notes")
    if re.search(r"\b(list|show|what notes)\b", low):
        return mod.handle({"action": "list"}, pctx)
    if re.search(r"\b(read|recall)\b", low):
        return mod.handle({"action": "read", "title": _after_keyword(low, "read")}, pctx)
    if re.search(r"\b(delete|remove)\b", low):
        return mod.handle({"action": "delete", "title": _after_keyword(low, "note")}, pctx)
    if re.search(r"\b(todo|to-do|checklist)\b", low):
        return mod.handle({"action": "todo_list"}, pctx)
    if re.search(r"\b(add|save|remember|note)\b", low):
        return mod.handle({"action": "add", "text": task}, pctx)
    return mod.handle({"action": "list"}, pctx)


def _after_keyword(text: str, keyword: str) -> str:
    """Text after the first occurrence of a keyword (original casing preserved)."""
    low = (text or "").lower()
    idx = low.find(keyword.lower())
    if idx < 0:
        return (text or "").strip()
    return (text[idx + len(keyword):]).strip(" :;,.!?-")


# ── Travel agent: weather & flights ────────────────────────────────────────────


def _travel_agent(task: str, ctx: dict) -> str:
    """Weather reports or flight search from free text."""
    player = ctx.get("player")
    speak  = ctx.get("speak")
    low    = (task or "").lower()

    if re.search(r"\b(flights?|fly|flying|airport|ticket|travel to)\b", low):
        ff = _import_action("flight_finder")
        origin, dest, date = _extract_flight_params(task)
        return ff.flight_finder(
            parameters={"origin": origin, "destination": dest, "date": date},
            player=player, speak=speak,
        )

    city = _extract_city(task)
    if not city:
        return "Which city should I check the weather for?"
    wa = _import_action("weather_report")
    return wa.weather_action(parameters={"city": city, "time": "today"}, player=player)


def _extract_flight_params(task: str) -> tuple[str, str, str]:
    """Best-effort 'from X to Y on date' → (origin, destination, date)."""
    origin, dest, date = "", "", ""
    m = re.search(r"\bfrom\s+([A-Za-z .'-]{2,40}?)\s+to\s+([A-Za-z .'-]{2,40}?)(?:\s+on\s+|$)",
                  task or "", re.IGNORECASE)
    if m:
        origin, dest = m.group(1).strip(), m.group(2).strip()
        for trailer in _FLIGHT_TRAILERS:
            if dest.lower().endswith(trailer):
                dest = dest[: -len(trailer)].strip()
                break
    d = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|[A-Za-z]+ \d{1,2})\b", task or "")
    if d:
        date = d.group(1)
    return origin, dest, date


# ── Apps agent: open apps / desktop / settings / control ───────────────────────


def _apps_agent(task: str, ctx: dict) -> str:
    """Open apps, desktop actions, computer settings, or mouse/keyboard control."""
    player = ctx.get("player")
    low    = (task or "").lower()

    # Desktop management.
    if re.search(r"\b(wallpaper|organize desktop|clean desktop|desktop stats)\b", low):
        dc = _import_action("desktop")
        if "wallpaper" in low:
            return dc.desktop_control(parameters={"action": "wallpaper", "task": task}, player=player)
        if "organize" in low:
            return dc.desktop_control(parameters={"action": "organize"}, player=player)
        if "clean" in low:
            return dc.desktop_control(parameters={"action": "clean"}, player=player)
        return dc.desktop_control(parameters={"action": "stats"}, player=player)

    # Computer settings (volume, brightness, window mgmt, shortcuts).
    settings_action = _extract_settings_action(task)
    if settings_action:
        cs = _import_action("computer_settings")
        params: dict = {"action": settings_action}
        m = re.search(r"\bvolume\s+(?:to\s+)?(\d+)\b", low)
        if settings_action == "volume_set" and m:
            params["value"] = int(m.group(1))
        return cs.computer_settings(parameters=params, player=player)

    # Computer control (click / type / scroll / screenshot).
    control_action = _extract_control_action(task)
    if control_action:
        cc = _import_action("computer_control")
        params = {"action": control_action}
        if control_action in ("type", "press", "hotkey"):
            params["text"] = _after_keyword(low, control_action) or task
        return cc.computer_control(parameters=params, player=player)

    # Default: open an app.
    oa = _import_action("open_app")
    name = re.sub(r"^\s*(open|launch|start)\s+", "", (task or "").strip(), flags=re.IGNORECASE)
    return oa.open_app(parameters={"app_name": name or task}, player=player)


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
    "media": {
        "name":        "media",
        "description": "Generates images from a description and handles YouTube (play, summarize, trending).",
        "run":         _media_agent,
    },
    "finance": {
        "name":        "finance",
        "description": "Live stock quotes, crypto prices and currency conversion.",
        "run":         _finance_agent,
    },
    "translate": {
        "name":        "translate",
        "description": "Translates text between languages (e.g. to Spanish, French, Japanese).",
        "run":         _translate_agent,
    },
    "productivity": {
        "name":        "productivity",
        "description": "Manages notes, to-do lists, habit streaks, pomodoro timers, countdowns and reminders.",
        "run":         _productivity_agent,
    },
    "travel": {
        "name":        "travel",
        "description": "Weather reports for a city and flight search between places.",
        "run":         _travel_agent,
    },
    "apps": {
        "name":        "apps",
        "description": "Opens apps, manages the desktop, and controls computer settings (volume, brightness, windows).",
        "run":         _apps_agent,
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
        "3. Order matters for dependent work (research first, then code, etc.). "
        "Steps that are FULLY independent — none needs another step's output — may "
        "be marked \"parallel\": true; consecutive parallel steps run at the same "
        "time to save the user time. Never mark a step parallel if it depends on "
        "an earlier step's result.\n"
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
            '    {"agent": "research|web|code|file|system|media|finance|translate|'
            'productivity|travel|apps", "task": "...", "parallel": false}\n'
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
        step.setdefault("parallel", False)
    return plan


def _synthesize(goal: str, plan: dict, results: list) -> dict:
    """Turn step results into a spoken summary + a structured written report."""
    model = new_gemini_client(AGENT_MODEL)

    step_lines = "\n".join(
        f"[{i}] ({r['agent']}) {r['task']}{_duration_str(r.get('seconds'))}\n"
        f"{str(r['result'])[:2000]}"
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


def _group_steps(steps: list) -> list:
    """Group consecutive parallel-flagged steps into waves; others run alone.

    A wave is a maximal run of steps marked "parallel": true — those execute
    concurrently. Any unmarked step starts a new single-step wave that runs after
    the previous one, preserving the planner's ordering for dependent work.
    """
    waves: list = []
    i, n = 0, len(steps)
    while i < n:
        if steps[i].get("parallel"):
            j = i
            while j < n and steps[j].get("parallel"):
                j += 1
            waves.append(steps[i:j])
            i = j
        else:
            waves.append([steps[i]])
            i += 1
    return waves


def _run_step(step: dict, ctx: dict) -> dict:
    """Execute one plan step, normalising the agent and guarding against crashes."""
    agent = str(step.get("agent", "research")).strip().lower()
    if agent not in AGENTS:
        agent = "research"
    task  = str(step.get("task", "")).strip() or "(no task)"
    start = time.monotonic()
    try:
        out = _run_agent(agent, task, ctx)
    except Exception as e:  # noqa: BLE001 — one bad step must not kill the plan
        out = f"[step failed: {e}]"
    return {
        "agent":   agent,
        "task":    task,
        "result":  str(out)[:MAX_RESULT_CHARS],
        "seconds": round(time.monotonic() - start, 2),
    }


def _run_wave(wave: list, ctx: dict) -> list:
    """Run a wave: one step directly, a parallel wave through a small pool."""
    if len(wave) == 1:
        return [_run_step(wave[0], ctx)]
    # _run_step guards every step internally, so future.result() cannot raise.
    with ThreadPoolExecutor(max_workers=min(len(wave), MAX_PARALLEL)) as ex:
        futures = [ex.submit(_run_step, step, ctx) for step in wave]
        return [future.result() for future in futures]


def _run_plan(plan: dict, ctx: dict) -> list:
    steps  = plan.get("steps", []) or []
    total  = len(steps)
    done   = 0
    start  = time.monotonic()
    results: list = []
    for wave in _group_steps(steps):
        for step in wave:
            done += 1
            agent = str(step.get("agent", "research")).strip().lower() or "research"
            task  = str(step.get("task", "")).strip() or "(no task)"
            _log(ctx, f"[Agents] ▶ Step {done}/{total} → {agent}: {task[:90]}")
        wave_out = _run_wave(wave, ctx)
        for r in wave_out:
            results.append(r)
            _log(ctx, f"[Agents] ✓ {r['agent']} done{_duration_str(r.get('seconds'))}")
    _log(ctx, f"[Agents] 🏁 {total} step(s) finished in {time.monotonic() - start:.1f}s total.")
    return results


def _heuristic_agent(goal: str) -> str:
    """No-LLM fallback: route the goal to the single best-matching agent."""
    low = (goal or "").lower()

    if re.search(r"\b(code|script|program|app|project|build|function|fix this code|write a)\b", low):
        return "code"
    if re.search(r"\b(file|folder|document|read the file|downloads)\b", low):
        return "file"
    if re.search(r"\b(website|web page|browser|open .*\.com|visit|go to|url)\b", low):
        return "web"
    if re.search(r"\b(cpu|ram|battery|temperature|system status|performance|gpu|hardware)\b", low):
        return "system"
    if re.search(r"\b(image|picture|photo|draw|generate .*image|youtube|video|trending|wallpaper)\b", low):
        return "media"
    if re.search(r"\b(stock|ticker|share price|bitcoin|crypto|convert .* (usd|eur|dollars|euros)|currency)\b", low):
        return "finance"
    if re.search(r"\b(translate|translation|say .* in (spanish|french|german|japanese|hindi|turkish|korean))\b", low):
        return "translate"
    if re.search(r"\b(note|notes|todo|to-do|habit|streak|pomodoro|countdown|birthday|remind|timer)\b", low):
        return "productivity"
    if re.search(r"\b(weather|forecast|flights?|fly|flying|airport|ticket|travel to)\b", low):
        return "travel"
    if re.search(r"\b(open |launch |start |volume|brightness|mute|desktop|click|type |screenshot)\b", low):
        return "apps"
    return "research"


def _run_single_agent(agent_name: str, goal: str, ctx: dict) -> str:
    """Run one agent and wrap its output into summary + report."""
    start = time.monotonic()
    raw = _run_agent(agent_name, goal, ctx)
    _log(ctx, f"[Agents] ✓ {agent_name} done in {time.monotonic() - start:.1f}s")
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

    ctx = {"player": player, "speak": _locked_speak(speak)}
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

    # Courtesy heads-up for longer runs — no dead air while the steps execute.
    steps = plan.get("steps", []) or []
    if speak and len(steps) >= 3:
        _names = list(dict.fromkeys(
            str(s.get("agent", "")).strip() for s in steps if s.get("agent")))
        ack = f"On it, sir — running {len(steps)} steps: {', '.join(_names)}."
        _log(ctx, "[Agents] 🗣 " + ack)
        try:
            speak(ack)
        except Exception:  # noqa: S110, BLE001 — courtesy line must never crash the task
            pass

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
        lines.append(f"## Step {i} — {r['agent']}{_duration_str(r.get('seconds'))}")
        lines.append(r["task"])
        lines.append("")
        lines.append(str(r["result"])[:MAX_RESULT_CHARS])
        lines.append("")
    return "\n".join(lines)
