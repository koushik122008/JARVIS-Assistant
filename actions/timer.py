"""
MARK XLIX - Timer

Sets an in-app countdown timer. When it finishes, JARVIS speaks the
announcement (via the passed-in speak callback) and shows a notification.

Unlike the reminder engine this does NOT depend on the OS scheduler -
it always works while JARVIS is running.

Accepts either seconds/minutes/hours params or free-text like
"in 5 minutes", "3 minutes", "90 seconds".
"""

import re
import threading
import time

_MIN_TO_SEC = 60
_HOUR_TO_SEC = 3600


def _parse_duration(params: dict, text: str):
    """Return seconds from structured params or free text, or None."""
    seconds = params.get("seconds")
    minutes = params.get("minutes")
    hours   = params.get("hours")

    if seconds is not None or minutes is not None or hours is not None:
        total = 0
        if seconds is not None:
            total += int(seconds)
        if minutes is not None:
            total += int(minutes) * _MIN_TO_SEC
        if hours is not None:
            total += int(hours) * _HOUR_TO_SEC
        return total

    t = (text or "").strip().lower()
    if not t:
        return None

    total = 0
    found = False
    for m in re.finditer(r"(\d+)\s*(seconds?|secs?|s\b|minutes?|mins?|m\b|hours?|hrs?|h\b)", t):
        amount = int(m.group(1))
        unit = m.group(2)[0]
        if unit == "s":
            total += amount
        elif unit == "m":
            total += amount * _MIN_TO_SEC
        else:
            total += amount * _HOUR_TO_SEC
        found = True
    return total if found else None


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, _HOUR_TO_SEC)
    m, s = divmod(rem, _MIN_TO_SEC)
    if h:
        return f"{h} hours and {m} minutes"
    if m:
        return f"{m} minutes and {s} seconds"
    return f"{s} seconds"


def set_timer(parameters=None, response=None, player=None,
              session_memory=None, speak=None) -> str:
    params = parameters or {}
    text   = (params.get("text") or params.get("query") or params.get("duration")
              or "").strip()

    seconds = _parse_duration(params, text)

    if seconds is None:
        return (
            "I need a duration. Try 'set a timer for 5 minutes', "
            "'timer 90 seconds', or '3 minute timer'."
        )
    if seconds <= 0:
        return "That timer duration doesn't make sense."
    if seconds > 6 * _HOUR_TO_SEC:
        return "Timers work best under 6 hours - I'd recommend a reminder for longer."

    duration_str = _fmt_duration(seconds)

    def _fire():
        time.sleep(seconds)
        message = f"Sir, your {duration_str} timer is done."
        if speak:
            try:
                speak(message)
            except Exception:
                pass
        if player:
            try:
                player.notify("J.A.R.V.I.S", f"Timer finished ({duration_str})")
            except Exception:
                pass

    threading.Thread(target=_fire, daemon=True,
                     name=f"timer-{int(time.time())}").start()

    if player:
        player.write_log(f"[timer] {duration_str}")

    return f"Timer set for {duration_str}. I'll let you know when it's done."
