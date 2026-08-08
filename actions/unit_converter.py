"""
MARK XLIX - Unit Converter

Pure offline unit math - no network required. Handles length, weight/mass,
temperature, speed, volume and data sizes.

Understands free-text input like "5 miles in km", "100 fahrenheit to celsius",
or structured parameters {value, from, to, category}.
"""

import re

# Base-unit factors per category (SI base where possible)
_LENGTH = {
    "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001, "millimetre": 0.001,
    "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01, "centimetre": 0.01,
    "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0,
    "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0, "kilometre": 1000.0,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
    "yd": 0.9144, "yard": 0.9144, "yards": 0.9144,
    "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
    "nm": 0.000000001, "nautical mile": 1852.0, "nautical miles": 1852.0,
}

_WEIGHT = {
    "mg": 0.000001, "milligram": 0.000001, "milligrams": 0.000001,
    "g": 0.001, "gram": 0.001, "grams": 0.001, "gramme": 0.001,
    "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0, "kilo": 1.0, "kilos": 1.0,
    "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0, "metric ton": 1000.0,
    "oz": 0.028349523125, "ounce": 0.028349523125, "ounces": 0.028349523125,
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
    "stone": 6.35029318, "stones": 6.35029318,
}

_SPEED = {
    "m/s": 1.0, "meter per second": 1.0, "meters per second": 1.0,
    "km/h": 0.2777778, "kph": 0.2777778, "kilometer per hour": 0.2777778,
    "kilometers per hour": 0.2777778, "kilometres per hour": 0.2777778,
    "mph": 0.44704, "mile per hour": 0.44704, "miles per hour": 0.44704,
    "knot": 0.514444, "knots": 0.514444, "kt": 0.514444,
    "ft/s": 0.3048, "foot per second": 0.3048, "fps": 0.3048,
}

_VOLUME = {
    "ml": 0.001, "milliliter": 0.001, "milliliters": 0.001, "millilitre": 0.001,
    "l": 1.0, "liter": 1.0, "liters": 1.0, "litre": 1.0, "litres": 1.0,
    "m3": 1000.0, "cubic meter": 1000.0, "cubic meters": 1000.0,
    "gal": 3.785411784, "gallon": 3.785411784, "gallons": 3.785411784,
    "qt": 0.946352946, "quart": 0.946352946, "quarts": 0.946352946,
    "pt": 0.473176473, "pint": 0.473176473, "pints": 0.473176473,
    "cup": 0.2365882365, "cups": 0.2365882365,
    "fl oz": 0.0295735295625, "fluid ounce": 0.0295735295625,
    "tbsp": 0.01478676478125, "tablespoon": 0.01478676478125,
    "tsp": 0.00492892159375, "teaspoon": 0.00492892159375,
}

_DATA = {
    "b": 1.0, "bit": 1.0, "bits": 1.0,
    "kb": 1000.0, "kilobit": 1000.0, "kilobits": 1000.0,
    "mb": 1000000.0, "megabit": 1000000.0, "megabits": 1000000.0,
    "gb": 1000000000.0, "gigabit": 1000000000.0, "gigabits": 1000000000.0,
    "tb": 1000000000000.0, "terabit": 1000000000000.0, "terabits": 1000000000000.0,
    "B": 8.0, "byte": 8.0, "bytes": 8.0,
    "KB": 8000.0, "kilobyte": 8000.0, "kilobytes": 8000.0,
    "MB": 8000000.0, "megabyte": 8000000.0, "megabytes": 8000000.0,
    "GB": 8000000000.0, "gigabyte": 8000000000.0, "gigabytes": 8000000000.0,
    "TB": 8000000000000.0, "terabyte": 8000000000000.0, "terabytes": 8000000000000.0,
    "KiB": 8192.0, "kibibyte": 8192.0,
    "MiB": 8388608.0, "mebibyte": 8388608.0,
    "GiB": 8589934592.0, "gibibyte": 8589934592.0,
    "TiB": 8796093022208.0, "tebibyte": 8796093022208.0,
}

_TEMP = {
    "c": ("C", "celsius", "celsius degree", "degrees celsius", "centigrade"),
    "f": ("F", "fahrenheit", "degrees fahrenheit"),
    "k": ("K", "kelvin"),
}


def _match(data: dict, token: str):
    """Return the canonical key for a token in a unit dict, or None.

    Prefers an exact (case-sensitive) hit first so case-sensitive unit
    pairs like 'KB' (bytes) vs 'kb' (bits) resolve correctly; only falls
    back to a case-insensitive match when no exact key exists.
    """
    t = (token or "").strip()
    if not t:
        return None
    if t in data:
        return t
    lower = t.lower()
    for key in data:
        if lower == key.lower():
            return key
    return None


def _convert_temp(value: float, frm: str, to: str):
    frm, to = frm.upper(), to.upper()
    if frm == "C":
        celsius = value
    elif frm == "F":
        celsius = (value - 32) * 5 / 9
    else:
        celsius = value - 273.15
    if to == "C":
        return celsius
    if to == "F":
        return celsius * 9 / 5 + 32
    return celsius + 273.15


def _parse_num(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group(0)) if m else None


def _parse_text(text: str):
    """Parse free text like '5 miles in km' -> (value, from_key, to_key, cat)."""
    lower = text.lower().strip()
    m = re.match(
        r"(?:convert\s+|what is\s+|how (?:many|much) is\s+)?"
        r"(-?\d+(?:\.\d+)?)\s*([a-z0-9 /]+?)\s*"
        r"(?:in|to|into|as)\s+([a-z0-9 /]+?)\s*$",
        lower,
    )
    if not m:
        # 'X into Y' — allow the separator to also bind directly (e.g. '5 miles into km')
        m = re.match(
            r"(?:convert\s+)?(-?\d+(?:\.\d+)?)\s*([a-z0-9 /]+?)\s*into\s+([a-z0-9 /]+?)\s*$",
            lower,
        )
    if not m:
        return None, None, None, None
    value = float(m.group(1))
    frm, to = m.group(2).strip(), m.group(3).strip()
    for cat, table in (
        ("length", _LENGTH), ("weight", _WEIGHT), ("speed", _SPEED),
        ("volume", _VOLUME), ("data", _DATA),
    ):
        k1 = _match(table, frm)
        k2 = _match(table, to)
        if k1 and k2:
            return value, k1, k2, cat
    def _temp_code(s: str):
        s = s.strip().lower()
        for code, names in _TEMP.items():
            if s == code.lower() or s in names:
                return code
        return None

    c1 = _temp_code(frm)
    c2 = _temp_code(to)
    if c1 and c2:
        return value, c1, c2, "temperature"
    return None, None, None, None


def _fmt(num: float) -> str:
    if abs(num) >= 1e9 or (abs(num) < 1e-6 and num != 0):
        return f"{num:.3e}"
    if abs(num) >= 1000:
        return f"{num:,.1f}"
    if abs(num) >= 100:
        return f"{num:,.1f}"
    return f"{num:.4f}".rstrip("0").rstrip(".")


def _convert_linear(value: float, table: dict, k1: str, k2: str) -> float:
    return value * table[k1] / table[k2]


def unit_converter(parameters=None, response=None, player=None,
                   session_memory=None) -> str:
    params = parameters or {}
    value  = params.get("value")
    frm    = params.get("from", "")
    to     = params.get("to", "")
    text   = params.get("text") or params.get("query") or ""

    if text:
        value, k1, k2, cat = _parse_text(text)
        if value is not None and k1 and k2:
            if cat == "temperature":
                return (
                    f"{_fmt(value)} {k1} = "
                    f"{_fmt(_convert_temp(value, k1, k2))} {k2}"
                )
            table = {"length": _LENGTH, "weight": _WEIGHT, "speed": _SPEED,
                     "volume": _VOLUME, "data": _DATA}[cat]
            return f"{_fmt(value)} {k1} = {_fmt(_convert_linear(value, table, k1, k2))} {k2}"

    if value is None or not frm or not to:
        return (
            "I need a value and two units. "
            "Try 'convert 5 miles to km' or '100 fahrenheit to celsius'."
        )
    value = float(value)

    # temperature
    frm_key = None
    for code, names in _TEMP.items():
        if frm.strip().lower() in names or frm.strip().lower() == code.lower():
            frm_key = code
    to_key = None
    for code, names in _TEMP.items():
        if to.strip().lower() in names or to.strip().lower() == code.lower():
            to_key = code
    if frm_key and to_key:
        return f"{_fmt(value)} {frm_key} = {_fmt(_convert_temp(value, frm_key, to_key))} {to_key}"

    # linear units
    for cat, table in (
        ("length", _LENGTH), ("weight", _WEIGHT), ("speed", _SPEED),
        ("volume", _VOLUME), ("data", _DATA),
    ):
        k1 = _match(table, frm)
        k2 = _match(table, to)
        if k1 and k2:
            return (
                f"{_fmt(value)} {k1} = {_fmt(_convert_linear(value, table, k1, k2))} {k2} "
                f"({cat})"
            )

    return (
        "I couldn't match those units. I handle length, weight, temperature, "
        "speed, volume and data sizes - e.g. '5 miles in km'."
    )
