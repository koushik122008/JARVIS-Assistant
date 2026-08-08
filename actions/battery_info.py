"""
MARK XLIX - Battery & Laptop Health

Reads REAL battery status via psutil (cross-platform: Windows, macOS, Linux)
and returns a spoken-friendly report with smart health tips.

No network required.
"""

import psutil


def _seconds_to_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} hours and {m} minutes"
    if m:
        return f"{m} minutes"
    return f"{s} seconds"


def _tips(percent: float, plugged: bool) -> list:
    tips = []
    if percent <= 20:
        tips.append("Your battery is low - plug in soon to avoid shutdown.")
    elif percent >= 90 and plugged:
        tips.append("Battery is nearly full - unplugging now helps long-term health.")
    elif 40 <= percent <= 80:
        tips.append("You are in the ideal charge range for battery longevity.")
    if plugged and percent < 90:
        tips.append("Charging - it will top up soon.")
    if not plugged and percent > 20:
        tips.append("On battery power - unplugged and running normally.")
    return tips


def battery_info(parameters=None, response=None, player=None,
                 session_memory=None) -> str:
    try:
        battery = psutil.sensors_battery()
    except Exception as e:
        return f"I couldn't read the battery status: {e}"

    if battery is None:
        return "No battery detected - this looks like a desktop or virtual machine."

    percent = round(battery.percent)
    plugged = bool(battery.power_plugged)

    base = f"Battery is at {percent}%"
    if plugged:
        base += " and charging"
    else:
        base += " and on battery power"

    secs = getattr(battery, "secsleft", None)
    if secs is not None and secs > 0 and not plugged:
        base += f", about {_seconds_to_hms(secs)} remaining"

    # Only recommend when the estimate is meaningful
    if secs is not None and secs > 0 and plugged:
        base += f", about {_seconds_to_hms(secs)} until full"

    tips = _tips(percent, plugged)
    if tips:
        base += ". " + " ".join(tips)

    if player:
        player.write_log(f"[battery] {percent}% {'charging' if plugged else 'on battery'}")

    return base
