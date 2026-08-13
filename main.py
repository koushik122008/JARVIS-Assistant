import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import array
import asyncio
import math
import re
import sys
import threading
import time
import json
import traceback
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from memory.config_manager import (
    get_brief_enabled, get_wake_word_keyword, get_wake_word_sensitivity,
    get_proactive_enabled, get_background_wake_enabled,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.image_generator  import generate_image as generate_image_action
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.alarm             import alarm
from actions.timer             import set_timer
from actions.battery_info      import battery_info
from actions.unit_converter    import unit_converter
from actions.currency_converter import currency_converter
from actions.crypto_prices     import crypto_prices
from actions.stock_prices      import stock_prices
from actions.translator        import translate_text
from actions.computer_settings import computer_settings
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.agent_task        import agent_task as agent_task_action
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.web_search        import _news as _fetch_news_sync
from actions.wake_word         import WakeWordDetector
from utils import (
    API_CONFIG_PATH, PROMPT_PATH,
    LIVE_MODEL, CHANNELS, SEND_SAMPLE_RATE, RECEIVE_SAMPLE_RATE, CHUNK_SIZE,
    get_api_key,
)
from plugins import PLUGIN_TOOLS, PLUGIN_HANDLERS, build_plugin_context
from plugins.habit_reminder import (
    check_and_fire as habit_reminder_check,
    send_push as habit_reminder_push,
    sync_os_schedule as habit_reminder_sync,
)


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

# Officially documented Gemini Live prebuilt voices (PrebuiltVoiceConfig.voice_name).
_GEMINI_VOICES = ("Puck", "Charon", "Kore", "Fenrir", "Aoede")

def _resolve_gemini_voice(raw: str) -> str:
    """Pick a valid Gemini Live voice; fall back to 'Charon' on anything unknown."""
    voice = (raw or "").strip()
    if voice in _GEMINI_VOICES:
        return voice
    if voice:
        print(f"[JARVIS] ⚠️ Unknown Gemini voice '{voice}' — using 'Charon'. "
              f"Valid: {', '.join(_GEMINI_VOICES)}")
    return "Charon"

TOOL_DECLARATIONS = [
    {
        "name": "generate_image",
        "description": (
            "Generates an image based on a text description. "
            "Use this when the user asks to create, generate, or draw an image. "
            "Supports various styles, colors, and customization options."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {
                    "type": "STRING",
                    "description": "Detailed description of the image to generate"
                },
                "style": {
                    "type": "STRING",
                    "description": "Style: realistic, artistic, cartoon, pixel, watercolor, sketch, abstract, retro, neon, cyberpunk, minimalist, vintage, fantasy, anime, oil_painting (default: realistic)"
                },
                "width": {
                    "type": "INTEGER",
                    "description": "Image width in pixels (default: 512)"
                },
                "height": {
                    "type": "INTEGER",
                    "description": "Image height in pixels (default: 512)"
                },
                "aspect_ratio": {
                    "type": "STRING",
                    "description": "Aspect ratio preset: square, portrait, landscape, wide, cinematic (overrides width/height if specified)"
                },
                "color_scheme": {
                    "type": "STRING",
                    "description": "Color scheme: warm, cool, vibrant, muted, monochrome, pastel, dark, bright (default: auto based on style)"
                },
                "complexity": {
                    "type": "STRING",
                    "description": "Detail level: simple, medium, complex (default: medium)"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name or phone number"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: whatsapp, telegram, instagram, signal, discord, messenger, or any desktop app name"},
                "dry_run":      {"type": "BOOLEAN", "description": "Validate without actually sending (default: false)"}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": (
            "Sets a timed reminder with an OS notification. "
            "Provide date/time OR the natural-language 'when' field "
            "(e.g. 'in 30 minutes', 'tomorrow at 9am', 'tonight at 8pm')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format (optional if 'when' is given)"},
                "time":    {"type": "STRING", "description": "Time in HH:MM, 24h or 12h with am/pm (optional if 'when' is given)"},
                "when":    {"type": "STRING", "description": "Natural language: 'in 30 minutes', 'tomorrow at 9am', 'tonight at 8pm', '3pm'"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "Camera angle captures a single still frame for analysis — it does NOT open the live feed. "
            "If the user wants to SEE the camera feed on screen, call open_camera instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "open_camera",
        "description": (
            "Opens the live camera feed on screen so the user can see what the camera sees. "
            "Call ONLY when the user explicitly asks to open/show/turn on the camera "
            "(e.g. 'open camera', 'show me the camera', 'turn on the camera'). "
            "The feed stays open until the user says close it or calls close_camera."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Tab and navigation shortcuts (close_tab, back, forward, reload) are sent to the user's "
            "real browser as keyboard shortcuts when no automation session is active. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | next_tab | prev_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Delegates a COMPLEX multi-step goal to the agent system. "
            "Use ONLY when a task needs multiple steps or several different tools "
            "(e.g. 'research the best budget laptops and write a summary report', "
            "'build a script that fetches stock prices, runs it, and saves a report'). "
            "Sub-agents: research (deep web research), web (browser pages), code "
            "(write/run/fix code), file (local files), system (hardware status), "
            "media (images & YouTube), finance (stocks/crypto/currency), translate, "
            "productivity (notes/habits/timers), travel (weather/flights), apps "
            "(open apps & control the computer). "
            "For anything a single tool can do, use that tool directly — never this."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {
                    "type": "STRING",
                    "description": "The complete task to accomplish, described in detail"
                },
                "agent": {
                    "type": "STRING",
                    "description": "Optional: force one agent — auto (default) | research | web | code | file | system | media | finance | translate | productivity | travel | apps"
                },
                "context": {
                    "type": "STRING",
                    "description": "Optional extra context: constraints, preferences, file paths"
                },
                "max_steps": {
                    "type": "INTEGER",
                    "description": "Max planner steps (default: 6, max: 10)"
                },
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "currency_converter",
        "description": (
            "Converts money between currencies using live exchange rates. "
            "Use when the user asks to convert an amount between currencies "
            "(e.g. 'convert 100 dollars to euros')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "amount": {"type": "NUMBER", "description": "Amount to convert (optional if given in text)"},
                "from":   {"type": "STRING", "description": "Source currency: name, code or symbol (e.g. 'dollars', 'USD', '$')"},
                "to":     {"type": "STRING", "description": "Target currency (e.g. 'euros', 'EUR')"},
                "text":   {"type": "STRING", "description": "Full free-text query, e.g. '100 usd to eur'"}
            },
            "required": []
        }
    },
    {
        "name": "crypto_prices",
        "description": (
            "Reports live cryptocurrency prices and 24h change. "
            "Use when the user asks about bitcoin, ethereum, solana, or any coin."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "asset":    {"type": "STRING", "description": "Coin name or ticker (bitcoin, btc, ethereum, sol, doge...). Omit for a top-6 watchlist"},
                "currency": {"type": "STRING", "description": "Quote currency: usd (default), eur, gbp, try"}
            },
            "required": []
        }
    },
    {
        "name": "unit_converter",
        "description": (
            "Converts between units: length, weight, temperature, speed, volume, data sizes. "
            "Use for '5 miles in km', '100 fahrenheit to celsius', '2 kg in pounds'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "value": {"type": "NUMBER", "description": "Numeric value to convert"},
                "from":  {"type": "STRING", "description": "Source unit (km, miles, kg, fahrenheit...)"},
                "to":    {"type": "STRING", "description": "Target unit (m, km, celsius...)"},
                "text":  {"type": "STRING", "description": "Full free-text query, e.g. '5 miles to km'"}
            },
            "required": []
        }
    },
    {
        "name": "alarm",
        "description": (
            "Sets a one-shot alarm at a specific time or after a duration. "
            "Use when the user says set an alarm (e.g. 'wake me at 7am', 'alarm in 10 minutes'). "
            "Supports natural-language times via 'when'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date YYYY-MM-DD (optional if 'when' given)"},
                "time":    {"type": "STRING", "description": "Time HH:MM (optional if 'when' given)"},
                "when":    {"type": "STRING", "description": "Natural language: 'in 10 minutes', 'tomorrow at 7am', '7:30'"},
                "message": {"type": "STRING", "description": "Alarm message text"}
            },
            "required": []
        }
    },
    {
        "name": "battery_info",
        "description": (
            "Reports battery level, charge state, estimated time remaining, "
            "and battery health tips. Use when the user asks about battery, charge, "
            "or laptop power."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "translate_text",
        "description": (
            "Translates text between languages using a live translation service. "
            "Use when the user asks to say something in another language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text":   {"type": "STRING", "description": "Text to translate"},
                "to":     {"type": "STRING", "description": "Target language (Japanese, Spanish, French, German, Turkish...)"},
                "from":   {"type": "STRING", "description": "Source language (default: English)"}
            },
            "required": ["text", "to"]
        }
    },
    {
        "name": "stock_prices",
        "description": (
            "Reports live stock quotes: current price, intraday change, day range. "
            "Use when the user asks about a stock or ticker (e.g. 'check AAPL')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker": {"type": "STRING", "description": "Ticker symbol (AAPL, MSFT, NVDA, TSLA...)"}
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "set_timer",
        "description": (
            "Sets an in-app countdown timer; JARVIS announces when it finishes. "
            "Use for 'set a timer for 5 minutes', 'timer 90 seconds'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "minutes": {"type": "INTEGER", "description": "Minutes (optional)"},
                "seconds": {"type": "INTEGER", "description": "Seconds (optional)"},
                "hours":   {"type": "INTEGER", "description": "Hours (optional)"},
                "text":    {"type": "STRING", "description": "Free-text duration, e.g. 'in 5 minutes'"}
            },
            "required": []
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# --- Plugin system: auto-discovered tools from the plugins/ folder ---
TOOL_DECLARATIONS.extend(PLUGIN_TOOLS)


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_sense_state    = self._on_sense_state
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._proactive_enabled = get_proactive_enabled()  # UI-toggleable
        self._last_user_speech = time.monotonic()  # updated on every user utterance

        # ── Wake word detection ────────────────────────────────────────────
        self._wake_word_detector: WakeWordDetector | None = None
        self._wake_event = threading.Event()  # set when wake word detected
        self._pending_wake_greeting = False  # send greeting after next reconnect
        self.ui.on_wake_word_toggled = self._on_wake_word_toggle
        self.ui.on_wake_word_settings = self._on_wake_word_settings_changed
        self.ui.on_proactive_toggled  = self._on_proactive_toggle

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        # Pause wake word detector while speaking to avoid echo triggers
        if self._wake_word_detector and self._wake_word_detector.is_running:
            if value:
                self._wake_word_detector.pause()
            else:
                self._wake_word_detector.resume()
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
            _voice = _resolve_gemini_voice(_cfg.get("gemini_voice"))
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""
            _voice = "Charon"

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else "ADDRESS: When speaking Turkish → always say \"efendim\". "
                      "When speaking English → say \"sir\". Never mix languages.")
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_voice
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "generate_image":
                out = await loop.run_in_executor(None, lambda: generate_image_action(args))
                result = out["result"]
                # Send the generated image to the remote dashboard if connected
                if out.get("image_bytes") and self._dashboard and self._dashboard.has_clients():
                    import base64
                    img_b64 = base64.b64encode(out["image_bytes"]).decode()
                    asyncio.create_task(self._dashboard.broadcast({
                        "type": "log",
                        "speaker": "jarvis",
                        "text": f"Generated image: {out.get('prompt', '')}",
                        "image": img_b64,
                        "ts": datetime.now().isoformat(),
                    }))

            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."
                self.ui.notify(self._asst_name,
                               f"Reminder scheduled: {(args.get('message') or '')[:60]}")

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                # Lazy import — screen_processor pulls in cv2 + mss + PIL (~200 MB,
                # 1-2 s import). Deferring it keeps startup fast on low-spec machines.
                from actions.screen_processor import _capture_camera, _capture_screen
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        # Single still for vision analysis only — the live feed is
                        # opened exclusively by the open_camera tool.
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in English, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "open_camera":
                self.ui.start_camera_stream()
                result = "Camera feed opened."

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                r = await loop.run_in_executor(
                    None,
                    lambda: agent_task_action(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."
                # Mirror the full agent report to the on-screen content panel
                if r and not r.startswith("Please tell me") and not r.startswith("I don't have"):
                    _goal = args.get("goal") or ""
                    self.ui.show_content(f"AGENTS — {_goal[:38]}", r)

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time
                    time.sleep(1.5)
                    self.ui.quit_app()
                threading.Thread(target=_shutdown, daemon=True).start()

            elif name == "currency_converter":
                r = await loop.run_in_executor(None, lambda: currency_converter(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "crypto_prices":
                r = await loop.run_in_executor(None, lambda: crypto_prices(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "unit_converter":
                r = await loop.run_in_executor(None, lambda: unit_converter(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "alarm":
                r = await loop.run_in_executor(None, lambda: alarm(parameters=args, response=None, player=self.ui))
                result = r or "Alarm set."
                self.ui.notify(self._asst_name, f"Alarm scheduled: {(args.get('message') or 'Alarm')[:60]}")

            elif name == "battery_info":
                r = await loop.run_in_executor(None, battery_info)
                result = r or "Battery status unavailable."

            elif name == "translate_text":
                r = await loop.run_in_executor(None, lambda: translate_text(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "stock_prices":
                r = await loop.run_in_executor(None, lambda: stock_prices(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "set_timer":
                r = await loop.run_in_executor(None, lambda: set_timer(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Timer set."

            elif name in PLUGIN_HANDLERS:
                ctx = build_plugin_context(self)
                r = await loop.run_in_executor(
                    None, lambda: PLUGIN_HANDLERS[name](args, ctx)
                )
                result = r or "Done."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            data = indata.tobytes()
            # Feed the HUD VU meter — strided RMS keeps this cheap in the audio thread
            try:
                _samples = array.array("h", data)[::8]
                if _samples:
                    _rms = math.sqrt(sum(s * s for s in _samples) / len(_samples)) / 32768.0
                    self.ui.set_vu(min(1.0, _rms * 4.0))
            except Exception:
                pass
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted and not self._phone_active:
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard and self._dashboard.has_clients():
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard and self._dashboard.has_clients():
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Single still for vision — nothing to keep open;
                                # release the busy flag straight away.
                                self._vision_busy = False

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, and say you are fetching today's news now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # If turn_complete didn't fire (timeout), give a small buffer
                if not turn_waited:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self._proactive_enabled:
                continue

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
                self.ui.notify(self._asst_name, "I'd like to check in with you.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    async def _run_habit_reminder(self) -> None:
        """
        Process-lifetime background task (runs even while the live session is
        sleeping or disconnected). Each tick:
          1. Keeps today's OS-level notification task in place, so the nudge
             fires via the OS scheduler even if JARVIS is fully closed.
          2. Checks whether it's time to nudge about unlogged habits. The
             nudge is ALWAYS a desktop notification; if phone push (ntfy) is
             configured it is also pushed to the phone; it is spoken when a
             session is live and not already speaking.
        check_and_fire() handles all scheduling state, so this is just a ticker.
        """
        while True:
            await asyncio.sleep(30)

            try:
                await asyncio.to_thread(habit_reminder_sync)
            except Exception as e:
                print(f"[HabitReminder] sync: {e}")

            try:
                nudge = await asyncio.to_thread(habit_reminder_check)
            except Exception as e:
                print(f"[HabitReminder] {e}")
                continue
            if not nudge:
                continue

            self.ui.write_log(f"SYS: Habit reminder — {nudge}")
            try:
                self.ui.notify(self._asst_name, nudge)
            except Exception:
                pass
            # Phone push (ntfy) — best-effort, never breaks the nudge.
            try:
                await asyncio.to_thread(habit_reminder_push, nudge)
            except Exception:
                pass
            with self._speaking_lock:
                speaking = self._is_speaking
            if self.session and not speaking:
                try:
                    self.speak(nudge)
                except Exception:
                    pass

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── Wake word detection ─────────────────────────────────────────────────────

    def _stop_wake_word(self):
        """Stop wake word detector and reset state."""
        if self._wake_word_detector:
            self._wake_word_detector.stop()
        self._wake_event.clear()

    def _on_wake_word_toggle(self, enabled: bool):
        """Called from UI thread when user toggles wake word button."""
        if enabled:
            kw = get_wake_word_keyword()
            sens = get_wake_word_sensitivity()
            if self._wake_word_detector is None:
                self._wake_word_detector = WakeWordDetector(keyword=kw, sensitivity=sens)
                self._wake_word_detector.on_detected = lambda: self._on_wake_word_detected()
            self._wake_word_detector.start()
            self.ui.write_log(f"SYS: Wake word detection enabled — say '{kw.title()}' to wake.")
        else:
            self._stop_wake_word()
            self.ui.write_log("SYS: Wake word detection disabled.")

    def _on_proactive_toggle(self, enabled: bool):
        """Called from UI thread when user toggles proactive check-ins."""
        self._proactive_enabled = bool(enabled)
        state = "enabled" if self._proactive_enabled else "disabled"
        self.ui.write_log(f"SYS: Proactive check-ins {state}.")
        print(f"[Proactive] {state} by user.")

    def _on_sense_state(self, snap) -> None:
        """Called from the UI thread when camera sensing state changes.

        A person appearing at the camera (after an absence) fast-tracks a
        proactive check-in: JARVIS marks the moment as if the user just spoke,
        so the greeting check fires on the next 60 s cycle without ever
        auto-speaking. No automatic speech — Gemini still decides.
        """
        try:
            if not snap:
                return
            person = bool(getattr(snap, "person", False))
            if person and not getattr(self, "_sense_person_present", False):
                # person just arrived — arm a proactive greeting check-in.
                # No auto-speech: Gemini still decides whether (and what) to say.
                if getattr(self, "_proactive", None) is not None:
                    self._proactive.note_person_arrival()
                self.ui.write_log("SENS: Person present — greeting check armed.")
                self._arm_wake_on_person_arrival()
            self._sense_person_present = person
        except Exception as e:
            print(f"[Sense] ⚠️ {e}")

    def _arm_wake_on_person_arrival(self) -> None:
        """Make JARVIS ready to hear the wake word the moment someone walks in.

        Called (UI thread) on the person-arrival edge, only after an absence:

        1. In-app detector — if the user has wake-word detection enabled, make
           sure it is actually listening right now (it may have been paused
           while JARVIS was speaking, or stopped entirely).
        2. Wake-from-closed listener — if cold-start wake is enabled but its
           always-on listener has died, restart it so 'hey jarvis' still
           launches JARVIS even from a fully closed app.

        Both respect the user's toggles: nothing is force-enabled, only
        restored/kept ready.
        """
        try:
            restored = False
            det = getattr(self, "_wake_word_detector", None)
            if det is not None:
                if det.is_running:
                    det.resume()      # wake it if it was paused during speech
                else:
                    det.start()       # user enabled it once — restore listening
                    restored = True
            # Cold-start path: restart a dead wake-from-closed listener.
            if get_background_wake_enabled():
                try:
                    from actions.background_wake import _listener_pids, start_listener
                    if not _listener_pids():
                        if start_listener():
                            self.ui.write_log(
                                "SYS: Wake-from-closed listener restarted (person arrived).")
                except Exception:
                    pass
            # Only announce when listening was actually restored — a detector
            # that was already active needs no fanfare on every arrival.
            if restored:
                kw = get_wake_word_keyword()
                self.ui.write_log(
                    f"SYS: Listening — say '{kw.title()}' anytime.")
        except Exception as e:
            print(f"[Sense] ⚠️ Wake-arm failed: {e}")

    def _on_wake_word_settings_changed(self):
        """Called from UI thread when user changes keyword/sensitivity in settings.
        Restarts the detector with new values if it's currently running."""
        was_running = self._wake_word_detector is not None and self._wake_word_detector.is_running
        if was_running:
            self._stop_wake_word()
        kw = get_wake_word_keyword()
        sens = get_wake_word_sensitivity()
        self._wake_word_detector = WakeWordDetector(keyword=kw, sensitivity=sens)
        self._wake_word_detector.on_detected = lambda: self._on_wake_word_detected()
        if was_running:
            self._wake_word_detector.start()
            self.ui.write_log(f"SYS: Wake word restarted — keyword='{kw.title()}', sensitivity={sens:.2f}")
        else:
            self.ui.write_log(f"SYS: Wake word settings updated — keyword='{kw.title()}', sensitivity={sens:.2f}")

    async def _send_wake_greeting(self) -> None:
        """Send a brief greeting after being woken by wake word."""
        await asyncio.sleep(0.5)
        if not self.session:
            return
        try:
            await self.session.send_client_content(
                turns={"parts": [{"text": "The user said your name to wake you. Greet them with one short sentence."}]},
                turn_complete=True,
            )
            self.ui.write_log("SYS: Wake greeting sent.")
        except Exception as e:
            print(f"[Wake] Greeting error: {e}")

    def _on_wake_word_detected(self) -> None:
        """Called (from background thread) when wake word is heard."""
        kw = get_wake_word_keyword()
        self.ui.write_log(f"WAKE: '{kw.title()}' detected — waking up...")
        self._wake_event.set()
        # If there is an active session, send a greeting prompt
        if self._loop and self.session:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": f"The user said your name ({kw}). Greet them briefly."}]},
                    turn_complete=True,
                ),
                self._loop
            )
        else:
            # No active session — mark for greeting after reconnection
            self._pending_wake_greeting = True

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            # Adaptive polling: tight 0.5 s loop only while a dashboard client is
            # connected; when nobody is watching, wake ~5× less often so the
            # event loop stays quiet on low-spec machines.
            timeout = 0.5 if self._dashboard.has_clients() else 2.5
            try:
                self.ui.set_dashboard_state(self._dashboard.has_clients())
            except Exception:
                pass
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=timeout
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        # Habit-reminder ticker runs for the process lifetime — it must keep
        # checking (and keep today's OS task scheduled) even while sleeping.
        asyncio.create_task(self._run_habit_reminder())

        while True:
            try:
                print("[JARVIS] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled)
                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

                    # Wake-word greeting: user said the wake word while sleeping
                    if self._pending_wake_greeting:
                        self._pending_wake_greeting = False
                        asyncio.create_task(self._send_wake_greeting())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                # Pause wake word detector during sleep to free mic resources
                if self._wake_word_detector and self._wake_word_detector.is_running:
                    self._wake_word_detector.pause()
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            # Resume wake word detector if it was paused
            if self.ui.wake_word_enabled and self._wake_word_detector:
                self._wake_word_detector.resume()
            # Wake-word-aware sleep: if wake event fires, reconnect immediately
            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self._wake_event.wait
                    ),
                    timeout=delay,
                )
                # Wake word fired — log and reconnect now
                self.ui.write_log("SYS: Wake word woke JARVIS — reconnecting...")
            except asyncio.TimeoutError:
                pass  # normal timeout — reconnect as usual

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        # Cold-start wake: launched by the background listener after "Hey Jarvis" —
        # enable the in-app wake word so the user can speak their command right away.
        if "--woke" in sys.argv:
            try:
                jarvis._on_wake_word_toggle(True)
                ui.write_log("SYS: Woken from background — say your command.")
            except Exception as e:
                print(f"[JARVIS] --woke wake word enable failed: {e}")
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()