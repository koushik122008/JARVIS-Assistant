"""
MARK XLIX — Live Weather Report

Fetches REAL current + forecast weather from Open-Meteo (free, no API key
required) and returns a spoken-friendly summary.

Previously this tool only opened a Google search tab — it never actually
reported the weather. Now it delivers live conditions directly. If the network
or the API is unavailable it gracefully falls back to opening a browser search
so the user still gets something useful.

APIs used (both free, no key):
  - Geocoding : https://geocoding-api.open-meteo.com/v1/search?name=<city>
  - Forecast  : https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..
"""

import webbrowser
from urllib.parse import quote_plus

import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL  = "https://api.open-meteo.com/v1/forecast"

_TIMEOUT = 10.0

# WMO weather interpretation codes → human description.
WEATHER_CODES: dict[int, str] = {
    0:  "clear sky",
    1:  "mainly clear",
    2:  "partly cloudy",
    3:  "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    56: "freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "violent showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def describe_code(code: int) -> str:
    """Map a WMO weather code to a human description."""
    try:
        return WEATHER_CODES[int(code)]
    except (TypeError, ValueError, KeyError):
        return "unknown conditions"


def _geocode(city: str) -> dict:
    """Resolve a city name to lat/lon (+ country + admin area)."""
    params = {"name": city, "count": 1, "language": "en", "format": "json"}
    resp = requests.get(GEOCODING_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    results = (resp.json() or {}).get("results") or []
    if not results:
        raise LookupError(f"no geocoding result for '{city}'")
    return results[0]


def fetch_weather(lat: float, lon: float) -> dict:
    """
    Fetch current conditions + 3-day daily forecast from Open-Meteo.

    Returns the raw JSON body.
    """
    params = {
        "latitude":  lat,
        "longitude": lon,
        "current":  (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,precipitation"
        ),
        "daily":    "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
        "forecast_days": 3,
    }
    resp = requests.get(FORECAST_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def build_report(city: str, place: dict, data: dict, when: str = "today") -> str:
    """Turn raw API payloads into a spoken-friendly weather report."""
    current = data.get("current") or {}
    daily   = data.get("daily") or {}

    temp  = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    code  = current.get("weather_code")
    humid = current.get("relative_humidity_2m")
    wind  = current.get("wind_speed_10m")

    desc = describe_code(code)

    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    tmax  = daily.get("temperature_2m_max") or []
    tmin  = daily.get("temperature_2m_min") or []

    # The user may have asked for a specific day (e.g. "tomorrow").
    when_s = (when or "today").lower()
    is_tomorrow = any(k in when_s for k in ("tomorrow", "yarin", "yar\u0131n", "tmr"))

    if is_tomorrow and len(dates) >= 2:
        lines = [f"Tomorrow in {city} expect {describe_code(codes[1])}"]
        if len(tmax) >= 2 and len(tmin) >= 2:
            lines.append(f"with a high of {tmax[1]:g} and a low of {tmin[1]:g} degrees")
        if temp is not None:
            lines.append(f"Right now it is {temp:g} degrees with {desc}")
    else:
        lines = [f"Right now in {city} it is {desc}"]
        if temp is not None:
            lines.append(f"with a temperature of {temp:g} degrees")
        if feels is not None and temp is not None and abs(feels - temp) >= 2:
            lines.append(f"but it feels like {feels:g}")
        if humid is not None:
            lines.append(f"humidity at {humid:g} percent")
        if wind is not None:
            lines.append(f"and wind speed around {wind:g} kilometers per hour")

        if len(dates) >= 2:
            lines.append("")
            lines.append("Forecast:")
            for i, day_name in enumerate(("Today", "Tomorrow")):
                if i >= len(dates):
                    break
                day_desc = describe_code(codes[i])
                parts = [f"{day_name}: {day_desc}"]
                if i < len(tmax) and i < len(tmin):
                    parts.append(f"high {tmax[i]:g}, low {tmin[i]:g}")
                lines.append(", ".join(parts))

    country = place.get("country")
    region  = place.get("admin1")
    if country and region:
        lines.append("")
        lines.append(f"(Location resolved: {region}, {country})")

    return "\n".join(lines)


def _browser_fallback(city: str, when: str) -> str:
    """Last resort: open a Google search for the weather (old behaviour)."""
    search_query = f"weather in {city} {when}".strip()
    url          = f"https://www.google.com/search?q={quote_plus(search_query)}"
    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't fetch the weather or open the browser: {e}"
        _log(msg)
        return msg
    msg = f"Live weather is unavailable right now, sir — I opened the browser for {city} instead."
    _log(msg)
    return msg


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city") if parameters else None
    when = (parameters.get("time") or "today") if parameters else "today"

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (str(when) or "today").strip()

    try:
        place = _geocode(city)
        data  = fetch_weather(place["latitude"], place["longitude"])
    except Exception as e:
        _log(f"Live data failed ({e}) - browser fallback")
        return _browser_fallback(city, when)

    report = build_report(city, place, data, when=when)
    _log(report, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"weather in {city}", response=report)
        except Exception:
            pass

    return report


def _log(message: str, player=None) -> None:
    try:
        print(f"[Weather] {message}")
    except UnicodeEncodeError:
        print(f"[Weather] {message}".encode("ascii", "replace").decode("ascii"))
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
