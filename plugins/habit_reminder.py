"""
MARK XLIX — Weather-Aware Habit Reminder plugin

Configures a daily nudge: at a set time each day Jarvis checks whether the
user has logged all their habits (see plugins/habit.py). If anything is
still unlogged it speaks a short, weather-aware prompt — e.g. "It's raining
in Istanbul — a good moment for your indoor habits."

Stored locally in memory/habit_reminder.json.
Tool name: habit_reminder

Public entry points:
  * handle(args, ctx)   — the LLM tool (set / enable / cancel / status / city
                          / push_on / push_off / push_topic / push_token)
  * check_and_fire()    — returns the nudge message to speak/notify, or None
  * sync_os_schedule()  — ensures an OS-level notification task is scheduled
                          for today, so the nudge fires even if JARVIS is
                          closed at the set time.
  * send_push(message)  — best-effort ntfy push to the user's phone; returns
                          True when ntfy accepted the message.
"""

import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from utils import BASE_DIR

DATA_DIR = BASE_DIR / "memory"
_LOCK = threading.Lock()

DEFAULT_TIME = "21:00"   # 9 PM

# WMO weather-code buckets used to shape the nudge.
_WET_CODES = {45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77,
              80, 81, 82, 85, 86, 95, 96, 99}
_CLEAR_CODES = {0, 1}


def _store_path() -> Path:
    return Path(DATA_DIR) / "habit_reminder.json"


def _load() -> dict:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("enabled", True)
    data.setdefault("time", DEFAULT_TIME)
    data.setdefault("last_fired", "")       # ISO date the daily check last happened
    data.setdefault("city", "")             # optional explicit city override
    data.setdefault("os_scheduled_for", "") # ISO date today's OS task was scheduled
    data.setdefault("os_retry_after", 0.0)   # epoch — never spam the OS scheduler
    data.setdefault("push_enabled", False)   # phone push via ntfy
    data.setdefault("push_topic", "")       # ntfy topic the phone subscribes to
    data.setdefault("push_token", "")       # optional ntfy access token (private topic)
    return data


def _save(data: dict) -> None:
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _store_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ── time helpers ───────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.IGNORECASE)


def _parse_time(raw) -> str | None:
    """Accept '21:00', '9pm', '7:30 pm', '12:00' → canonical 'HH:MM' (24h)."""
    m = _TIME_RE.match(str(raw or "").strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _fmt_time(t: str) -> str:
    try:
        hh, mm = t.split(":")
        hour = int(hh)
        ap = "pm" if hour >= 12 else "am"
        return f"{hour % 12 or 12}:{mm} {ap}"
    except Exception:
        return t


# ── helpers ────────────────────────────────────────────────────────────────────

def _natural_list(items: list) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _city_from_memory() -> str:
    """Read the user's city from their memory profile (identity.city)."""
    try:
        from memory.memory_manager import load_memory
        identity = (load_memory() or {}).get("identity", {}) or {}
        city = identity.get("city")
        if isinstance(city, dict):
            return (city.get("value") or "").strip()
        return (city or "").strip()
    except Exception:
        return ""


def _fetch_conditions(city: str):
    """Return (wmo_code, description, temp_c) for the city, or None on failure."""
    try:
        from actions.weather_report import _geocode, fetch_weather, describe_code
        place = _geocode(city)
        data = fetch_weather(place["latitude"], place["longitude"])
        current = data.get("current") or {}
        code = current.get("weather_code")
        desc = describe_code(code)
        temp = current.get("temperature_2m")
        return (int(code), desc, temp)
    except Exception:
        return None


def _build_nudge(missing: list, city: str) -> str:
    """Weather-aware one/two-sentence nudge. Weather is best-effort."""
    lst = _natural_list([m.lower() for m in missing])
    cond = _fetch_conditions(city) if city else None

    if cond:
        code, desc, temp = cond
        temp_s = f", {temp:g} degrees" if temp is not None else ""
        if code in _WET_CODES:
            return (f"It's {desc}{temp_s} in {city} — a good moment for your "
                    f"indoor habits. You haven't logged {lst} today. Shall I mark one off?")
        if code in _CLEAR_CODES:
            return (f"The weather in {city} is {desc}{temp_s} — nice and inviting. "
                    f"You haven't logged {lst} today. Care to check one off?")
        return (f"It's {desc}{temp_s} in {city}. You haven't logged {lst} today — "
                f"care to check one off?")

    return (f"You haven't logged {lst} today. Care to check them off "
            f"before the day is over?")


def check_and_fire() -> str | None:
    """
    Called by main.py's background loop (once per minute).

    Returns the nudge message to speak/notify when:
      • the reminder is enabled, AND
      • the configured daily time has been reached today, AND
      • this day's check hasn't already run, AND
      • at least one habit is still unlogged today.

    The daily check is marked as done (last_fired = today) the moment the
    time is reached — even if everything was already logged — so it never
    re-nudges later in the same day.
    """
    data = _load()
    if not data.get("enabled", True):
        return None
    time_str = data.get("time") or ""
    if not time_str:
        return None

    today = date.today().isoformat()
    if data.get("last_fired") == today:
        return None

    try:
        hh, mm = time_str.split(":")
        target = datetime.now().replace(
            hour=int(hh), minute=int(mm), second=0, microsecond=0
        )
    except Exception:
        return None
    if datetime.now() < target:
        return None

    # The daily check has happened — record it before doing any work so a
    # slow/failed weather fetch can never cause double nudges.
    data["last_fired"] = today
    _save(data)

    try:
        from plugins.habit import _load as _habits_load
        habits = (_habits_load().get("habits", {}) or {})
    except Exception:
        habits = {}

    missing = [name for name, info in habits.items()
               if today not in (info.get("log") or [])]
    if not missing:
        return None  # everything logged — nothing to nudge about

    city = (data.get("city") or "").strip() or _city_from_memory()
    return _build_nudge(missing, city)


# ── Phone push (ntfy) ──────────────────────────────────────────────────────────

def send_push(message: str) -> bool:
    """
    Push a message to the user's phone via ntfy.sh (free, no account).

    Uses only the standard library so it works even inside the standalone
    OS-scheduler script under any Python. Returns True when the message was
    accepted by ntfy; silently returns False when push is disabled, no topic
    is configured, or the network call fails (the nudge must never break).
    """
    data = _load()
    if not data.get("push_enabled", False):
        return False
    topic = (data.get("push_topic") or "").strip().lstrip("/")
    if not topic:
        return False
    token = (data.get("push_token") or "").strip()
    try:
        import urllib.parse
        import urllib.request

        req = urllib.request.Request(
            "https://ntfy.sh/" + urllib.parse.quote(topic, safe=""),
            data=message.encode("utf-8"),
            headers={
                "Title": "J.A.R.V.I.S Habit Reminder",
                "Priority": "default",
                "Tags": "bell",
            },
            method="POST",
        )
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201, 202)
    except Exception:
        return False


# ── OS-level fallback schedule ─────────────────────────────────────────────────

def _notification_block(os_name: str) -> str:
    """Desktop-notification code embedded in the standalone script."""
    if os_name == "windows":
        return (
            "\nnotified = False\n"
            "try:\n"
            "    from plyer import notification\n"
            "    notification.notify(title='J.A.R.V.I.S Habit Reminder', "
            "message=message, timeout=15)\n"
            "    notified = True\n"
            "except Exception:\n"
            "    pass\n"
            "if not notified:\n"
            "    try:\n"
            "        from win10toast import ToastNotifier\n"
            "        ToastNotifier().show_toast('J.A.R.V.I.S Habit Reminder', "
            "message, duration=15, threaded=False)\n"
            "        notified = True\n"
            "    except Exception:\n"
            "        pass\n"
            "if not notified:\n"
            "    try:\n"
            "        import subprocess\n"
            "        subprocess.run(['msg', '*', '/TIME:30', message], check=False)\n"
            "    except Exception:\n"
            "        pass\n"
            "try:\n"
            "    import winsound\n"
            "    for freq in [800, 1000, 1200]:\n"
            "        winsound.Beep(freq, 180)\n"
            "        import time; time.sleep(0.08)\n"
            "except Exception:\n"
            "    pass\n"
        )
    if os_name == "mac":
        lines = [
            "",
            "notified = False",
            "try:",
            "    from plyer import notification",
            "    notification.notify(title='J.A.R.V.I.S Habit Reminder', message=message, timeout=15)",
            "    notified = True",
            "except Exception:",
            "    pass",
            "if not notified:",
            "    try:",
            "        import subprocess",
            "        script = 'display notification \"{}\" with title \"J.A.R.V.I.S Habit Reminder\"'.format(message.replace('\"', ''))",
            "        subprocess.run(['osascript', '-e', script], check=False)",
            "    except Exception:",
            "        pass",
            "",
        ]
        return "\n".join(lines)
    return (
        "\nnotified = False\n"
        "try:\n"
        "    from plyer import notification\n"
        "    notification.notify(title='J.A.R.V.I.S Habit Reminder', "
        "message=message, timeout=15)\n"
        "    notified = True\n"
        "except Exception:\n"
        "    pass\n"
        "if not notified:\n"
        "    try:\n"
        "        import subprocess\n"
        "        subprocess.run(['notify-send', '--urgency=normal', "
        "'--expire-time=15000', 'J.A.R.V.I.S Habit Reminder', message], "
        "check=False)\n"
        "    except Exception:\n"
        "        pass\n"
    )


def _write_standalone_script(path: Path) -> None:
    """
    Write a self-computing notification script for the OS scheduler.

    At fire time the script re-evaluates check_and_fire() — so the message is
    always current (habits, weather, enabled state) — and stays silent when
    nothing needs nudging. Unlike one-shot reminders it does NOT self-delete.
    """
    from utils import get_os_name

    root = BASE_DIR   # project root — embedded so the script runs anywhere
    body = (
        "# Auto-generated by J.A.R.V.I.S habit reminder — do not edit\n"
        "import sys, pathlib\n"
        f"sys.path.insert(0, {json.dumps(str(root))})\n"
        "try:\n"
        "    from plugins.habit_reminder import check_and_fire\n"
        "    message = check_and_fire()\n"
        "except Exception:\n"
        "    message = None\n"
        "if not message:\n"
        "    raise SystemExit(0)\n"
        + _notification_block(get_os_name())
        + "\ntry:\n"
        "    from plugins.habit_reminder import send_push\n"
        "    send_push(message)\n"
        "except Exception:\n"
        "    pass\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _cleanup_old_scripts(keep_days: int = 2) -> None:
    """Remove stale standalone scripts so the scripts dir never grows forever."""
    try:
        from actions.reminder import _scripts_dir
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        for p in _scripts_dir().glob("JARVISHabitReminder_*.py"):
            if p.stem.replace("JARVISHabitReminder_", "") < cutoff:
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _ensure_today_scheduled() -> None:
    """
    Best-effort, once per day: schedule a standalone notification for today.

    The OS task fires one minute AFTER the in-app time — the in-app ticker
    claims the exact minute when JARVIS is running, so a task firing a minute
    later only ever nudges when JARVIS is not running (it re-checks
    check_and_fire and stays silent if already nudged).
    """
    data = _load()
    if not data.get("enabled", True):
        return
    time_str = data.get("time") or ""
    if not time_str:
        return

    today = date.today().isoformat()
    if data.get("os_scheduled_for") == today:
        return
    if time.time() < float(data.get("os_retry_after") or 0):
        return  # previous attempt failed — respect the backoff cooldown

    try:
        hh, mm = time_str.split(":")
        fire = (datetime.now()
                .replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                + timedelta(minutes=1))
    except Exception:
        return
    if fire <= datetime.now():
        return  # too late today — the in-app ticker handles it if running

    try:
        from actions.reminder import _schedule_linux, _schedule_mac, _schedule_windows
        from actions.reminder import _scripts_dir
        from utils import get_os_name
    except Exception as e:
        print(f"[HabitReminder] scheduler import: {e}")
        return

    task_name = f"JARVISHabitReminder_{today}"
    script_path = _scripts_dir() / f"{task_name}.py"
    _cleanup_old_scripts()
    try:
        _write_standalone_script(script_path)
        os_name = get_os_name()
        if os_name == "windows":
            job = _schedule_windows(fire, task_name, script_path, "")
        elif os_name == "mac":
            job = _schedule_mac(fire, task_name, script_path)
        else:
            job = _schedule_linux(fire, task_name, script_path)
    except Exception as e:
        print(f"[HabitReminder] OS schedule failed: {e}")
        job = ""

    if job:
        data["os_scheduled_for"] = today
        data["os_retry_after"] = 0.0
    else:
        # Don't hammer the OS scheduler — retry at most every 6 hours.
        data["os_retry_after"] = time.time() + 6 * 3600
    _save(data)


def sync_os_schedule() -> None:
    """Public entry point for main.py's background loop (runs every ~30s).
    Cheap: reads the store and returns early once today's task is scheduled."""
    try:
        _ensure_today_scheduled()
    except Exception as e:
        print(f"[HabitReminder] sync_os_schedule: {e}")


# ── LLM tool ───────────────────────────────────────────────────────────────────

def handle(args: dict, ctx: dict) -> str:
    action = (args or {}).get("action", "").strip().lower()
    data = _load()

    if action in ("set", "schedule", "time"):
        raw = (args or {}).get("time") or ""
        parsed = _parse_time(raw)
        if not parsed:
            return ("I couldn't understand that time. Say something like "
                    "'remind me about my habits at 8pm' or give me HH:MM.")
        data["time"] = parsed
        data["enabled"] = True
        data["os_scheduled_for"] = ""   # re-schedule the OS task at the new time
        data["os_retry_after"] = 0.0
        _save(data)
        return (f"Habit reminder set for {_fmt_time(parsed)} every day. "
                f"I'll nudge you if any habit is still unlogged.")

    if action in ("cancel", "off", "disable", "stop"):
        data["enabled"] = False
        _save(data)
        return ("Habit reminders turned off. Say 'enable habit reminders' "
                "to turn them back on.")

    if action in ("enable", "on"):
        data["enabled"] = True
        data["os_scheduled_for"] = ""
        data["os_retry_after"] = 0.0
        _save(data)
        return (f"Habit reminders enabled for {_fmt_time(data['time'])} every day.")

    if action in ("city", "set_city", "location"):
        city = str((args or {}).get("city") or "").strip()
        if not city:
            return ("Which city should I use for the weather? Say "
                    "'set my habit reminder city to Paris'.")
        data["city"] = city.strip().title()
        _save(data)
        return f"Using {data['city']} for weather-aware habit nudges."

    if action in ("push_on", "push_enable", "enable_push"):
        if not (data.get("push_topic") or "").strip():
            return ("Phone push needs a topic first — say \"push my reminders to "
                    "topic <name>\" and subscribe to that topic in the ntfy app "
                    "on your phone.")
        data["push_enabled"] = True
        _save(data)
        return ("Phone push is on. Your habit nudges will now also appear on "
                f"your phone via the '{data['push_topic']}' ntfy topic.")

    if action in ("push_off", "push_disable", "disable_push"):
        data["push_enabled"] = False
        _save(data)
        return "Phone push turned off. Nudges will only show on this desktop."

    if action in ("push_topic", "set_push_topic", "topic"):
        topic = str((args or {}).get("topic") or "").strip().lstrip("/")
        if not topic:
            return ("I need a topic name — say \"push my reminders to topic "
                    "habit-alerts\". Use an unguessable name for privacy.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", topic):
            return ("ntfy topics can only contain letters, numbers, dashes "
                    "and underscores — try something like 'habit-alerts'.")
        data["push_topic"] = topic
        data["push_enabled"] = True
        _save(data)
        return (f"Phone push set. Install the ntfy app on your phone and "
                f"subscribe to the topic '{topic}' to receive your habit "
                f"nudges there.")

    if action in ("push_token", "set_push_token", "token"):
        token = str((args or {}).get("token") or "").strip()
        if not token or token.lower() in ("clear", "none", "remove"):
            data["push_token"] = ""
            _save(data)
            return "Push token cleared — the topic is now public. Anyone with the " \
                   "topic name can read it."
        data["push_token"] = token
        _save(data)
        return ("Push token saved — ntfy will authenticate with it, so only "
                "devices with read access can see the topic.")

    if action in ("status", "info", "when"):
        state = "on" if data["enabled"] else "off"
        city = (data.get("city") or "").strip() or _city_from_memory() or "not set"
        push = "on" if data["push_enabled"] else "off"
        topic = (data.get("push_topic") or "").strip() or "not set"
        return (f"Habit reminder is {state}, set for {_fmt_time(data['time'])} "
                f"every day. Weather city: {city}. Phone push: {push} "
                f"(topic {topic}).")

    return ("I can set, enable, cancel or show my habit reminder, set the "
            "weather city, and manage phone push (topic/token/on/off). "
            "Try 'remind me about my habits at 8pm'.")


PLUGIN = {
    "name": "habit_reminder",
    "description": (
        "Configures a daily weather-aware habit reminder. Use when the user "
        "wants to be reminded about unlogged habits at a set time each day "
        "('remind me about my habits at 8pm', 'nudge me about my habits "
        "every evening'), turns the reminder on or off, asks when it fires, "
        "sets the city used for weather context, or sets up phone push "
        "notifications via ntfy ('send my habit reminders to my phone', "
        "'push my reminders to topic X'). The nudge itself fires "
        "automatically — this tool only configures it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("set | enable | cancel | status | city | push_on | "
                                "push_off | push_topic | push_token"),
            },
            "time": {
                "type": "STRING",
                "description": "Daily time — 'HH:MM' or natural like '9pm', '21:00'",
            },
            "city": {
                "type": "STRING",
                "description": "City for weather context, e.g. 'Istanbul'",
            },
            "topic": {
                "type": "STRING",
                "description": "ntfy topic for phone push, e.g. 'jarvis-habits'",
            },
            "token": {
                "type": "STRING",
                "description": "Optional ntfy access token for a private topic; 'clear' removes it",
            },
        },
        "required": ["action"],
    },
}
