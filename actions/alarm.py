"""
MARK XLIX - Alarm Clock

A one-shot alarm built on top of the existing reminder engine
(actions/reminder.py). Alarms and reminders share the exact same
OS-level scheduling and in-app fallback.

Accepts a natural-language time via the 'when' field, e.g.
  - "in 10 minutes"
  - "tomorrow at 7am"
  - "7:30"
or explicit date/time fields like the reminder tool.
"""

from actions.reminder import reminder


def alarm(parameters=None, response=None, player=None,
          session_memory=None) -> str:
    params = dict(parameters or {})

    # Reuse the reminder engine; give a sensible default message.
    if not params.get("message"):
        params["message"] = "Alarm!"

    result = reminder(
        parameters=params,
        response=response,
        player=player,
        session_memory=session_memory,
    )

    # Keep the wording alarm-appropriate while preserving the engine's
    # useful status notes (past time, parse errors, scheduler notes).
    if result.startswith("Reminder set"):
        return result.replace("Reminder set", "Alarm set", 1)
    return result
