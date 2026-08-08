"""On-device tools for the Pi 5 JARVIS.

Same pattern as ``TOOL_DECLARATIONS`` in ``main.py`` — Ollama understands this
OpenAI-style function schema. ``TOOLS`` is passed to the LLM and ``run_tool``
executes whatever the model asks for.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import urllib.parse
import urllib.request

# ── Optional: called when a timer fires, so the assistant can speak "Timer done!" ──
_speaker_cb = None


def set_timer_speaker_callback(cb) -> None:
    global _speaker_cb
    _speaker_cb = cb


# ── Tool declarations (LLM-facing schema) ──────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time on the Pi.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "Get today's date.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Get the current weather for a city using the free Open-Meteo "
                "API (no API key needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. 'Istanbul'"},
                    "country": {
                        "type": "string",
                        "description": "Optional country code, e.g. 'TR'",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": "Set a timer that speaks when it finishes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "Seconds to wait"},
                },
                "required": ["seconds"],
            },
        },
    },
]


# ── Handlers ────────────────────────────────────────────────────────────────
def _get_time(_: dict) -> str:
    return _dt.datetime.now().strftime("%I:%M %p").lstrip("0")


def _get_date(_: dict) -> str:
    return _dt.datetime.now().strftime("%A, %B %d, %Y")


def _http_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_weather(args: dict) -> str:
    city = str(args.get("city", "")).strip()
    if not city:
        return {"error": "no city provided"}

    # 1) geocode the city
    geo = _http_json(
        "https://geocoding-api.open-meteo.com/v1/search?"
        + urllib.parse.urlencode(
            {"name": city, "count": 1, "language": "en", "format": "json"}
        )
    )
    results = geo.get("results") or []
    if not results:
        return {"error": f"city '{city}' not found"}

    loc = results[0]
    # 2) current weather
    wx = _http_json(
        "https://api.open-meteo.com/v1/forecast?"
        + urllib.parse.urlencode(
            {
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current_weather": "true",
            }
        )
    )
    cur = wx.get("current_weather", {})
    return {
        "city": loc.get("name", city),
        "temperature_c": cur.get("temperature"),
        "windspeed_kmh": cur.get("windspeed"),
        "weather_code": cur.get("weathercode"),
    }


def _set_timer(args: dict) -> str:
    seconds = max(1, int(args.get("seconds", 0)))

    def _fire():
        if _speaker_cb:
            _speaker_cb("Timer finished!")
        else:
            print("[TIMER] Finished!")

    threading.Timer(seconds, _fire).start()
    return f"Timer set for {seconds} seconds"


_HANDLERS = {
    "get_time": _get_time,
    "get_date": _get_date,
    "get_weather": _get_weather,
    "set_timer": _set_timer,
}


def run_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name; always returns a JSON-ish string for the LLM."""
    fn = _HANDLERS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        result = fn(arguments or {})
    except Exception as e:  # never let a tool crash the conversation loop
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    return json.dumps(result) if not isinstance(result, str) else result
