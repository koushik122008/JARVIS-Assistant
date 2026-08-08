"""
MARK XLIX - Live Currency Converter

Fetches REAL exchange rates from the Frankfurter API (ECB daily rates,
free, no API key required) and returns a spoken-friendly conversion.

If the network is unavailable it falls back to a small built-in rate table
(clearly labelled as approximate).

API used:
  - https://api.frankfurter.app/latest?from=USD&to=EUR&amount=100
"""

import re

import requests

API_URL = "https://api.frankfurter.app/latest"
_TIMEOUT = 10.0

# Currency code -> (code, [aliases...])  aliases are lower-case
_CURRENCIES = {
    "USD": ("USD", ["usd", "dollar", "dollars", "buck", "bucks", "$"]),
    "EUR": ("EUR", ["eur", "euro", "euros", "EUR"]),
    "GBP": ("GBP", ["gbp", "pound", "pounds", "quid", "sterling", "Pound sterling"]),
    "JPY": ("JPY", ["jpy", "yen", "yen japanese"]),
    "TRY": ("TRY", ["try", "lira", "turkish lira", "liras"]),
    "CHF": ("CHF", ["chf", "swiss franc", "franc", "francs"]),
    "CAD": ("CAD", ["cad", "canadian dollar", "canadian dollars"]),
    "AUD": ("AUD", ["aud", "australian dollar", "australian dollars"]),
    "INR": ("INR", ["inr", "indian rupee", "rupee", "rupees"]),
    "CNY": ("CNY", ["cny", "yuan", "renminbi", "chinese yuan"]),
    "BRL": ("BRL", ["brl", "brazilian real", "real"]),
    "RUB": ("RUB", ["rub", "ruble", "russian ruble", "rubles"]),
    "KRW": ("KRW", ["krw", "won", "korean won"]),
    "AED": ("AED", ["aed", "dirham", "uae dirham"]),
    "MXN": ("MXN", ["mxn", "mexican peso", "peso"]),
    "ZAR": ("ZAR", ["zar", "south african rand", "rand"]),
    "SGD": ("SGD", ["sgd", "singapore dollar", "singapore dollars"]),
    "HKD": ("HKD", ["hkd", "hong kong dollar", "hong kong dollars"]),
    "NOK": ("NOK", ["nok", "norwegian krone", "krone"]),
    "SEK": ("SEK", ["sek", "swedish krona", "krona"]),
    "DKK": ("DKK", ["dkk", "danish krone"]),
    "PLN": ("PLN", ["pln", "zloty", "polish zloty"]),
    "NZD": ("NZD", ["nzd", "new zealand dollar"]),
    "THB": ("THB", ["thb", "thai baht", "baht"]),
    "IDR": ("IDR", ["idr", "indonesian rupiah", "rupiah"]),
}

# Approximate USD-based fallback rates (per 1 USD), used only when offline.
_OFFLINE_RATES = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 149.5, "TRY": 33.5,
    "CHF": 0.88, "CAD": 1.36, "AUD": 1.52, "INR": 83.5, "CNY": 7.15,
    "BRL": 5.05, "RUB": 92.0, "KRW": 1350.0, "AED": 3.67, "MXN": 17.3,
    "ZAR": 18.2, "SGD": 1.34, "HKD": 7.8, "NOK": 10.6, "SEK": 10.5,
    "DKK": 6.9, "PLN": 4.0, "NZD": 1.64, "THB": 36.0, "IDR": 15800.0,
}

# match "100", "100.5", "100,5" or word numbers like "one hundred"
_NUM_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUM_SCALE = {"hundred": 100, "thousand": 1000, "million": 1000000}


def _resolve_code(token: str):
    """Map a currency name/code/symbol to a 3-letter code (or None)."""
    t = (token or "").strip().lower()
    if not t:
        return None
    for code, (_code, aliases) in _CURRENCIES.items():
        if t == code.lower() or t in (a.lower() for a in aliases):
            return code
    return None


def _parse_amount(text: str):
    """Extract a number from free-form text ('100', '100.5', 'one hundred')."""
    text = text.strip().replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", text)
    if m:
        return float(m.group(0))
    # word numbers with proper place-value composition ("one hundred" = 100)
    total, current = 0, 0
    for w in text.lower().split():
        if w in _NUM_SMALL:
            current += _NUM_SMALL[w]
        elif w in _NUM_SCALE:
            if current == 0:
                current = 1
            total += current * _NUM_SCALE[w]
            current = 0
    total += current
    return float(total) if total else None


def _parse_inputs(params: dict):
    """Return (amount, from_code, to_code) from structured or free-text input."""
    amount = params.get("amount")
    source = params.get("from") or params.get("source") or ""
    target = params.get("to") or params.get("target") or ""
    text   = params.get("text") or params.get("query") or ""

    if amount is not None:
        amount = float(amount)
    from_code = _resolve_code(source)
    to_code   = _resolve_code(target)

    if from_code and to_code and amount is not None:
        return amount, from_code, to_code

    # free-text fallback: "convert 100 usd to eur"
    if text and (not from_code or not to_code):
        lower = text.lower()
        # longer separators first so "into" is not swallowed by "in"
        parts = re.split(r"\s*(?:into|->|to|in)\s*", lower)
        if len(parts) >= 2:
            left, right = parts[0], parts[1]
            amt = _parse_amount(left)
            codes = re.findall(r"[a-z$]+", left)
            source_code = _resolve_code(codes[-1]) if codes else None
            target_code = _resolve_code(right.strip())
            if amt is not None and source_code and target_code:
                return amt, source_code, target_code
    return None, None, None


def _fetch_rate(from_code: str, to_code: str) -> float:
    resp = requests.get(
        API_URL,
        params={"from": from_code, "to": to_code},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return float(data["rates"][to_code])


def _offline_convert(amount: float, from_code: str, to_code: str):
    """Approximate conversion using the built-in table (offline fallback)."""
    if from_code not in _OFFLINE_RATES or to_code not in _OFFLINE_RATES:
        return None
    return amount * _OFFLINE_RATES[to_code] / _OFFLINE_RATES[from_code]


def _fmt(num: float) -> str:
    """Format a number for speech: trim trailing zeros, 2 decimals max."""
    if abs(num) >= 100:
        return f"{num:,.0f}"
    return f"{num:,.2f}".rstrip("0").rstrip(".")


def currency_converter(parameters=None, response=None, player=None,
                       session_memory=None) -> str:
    params = parameters or {}
    amount, from_code, to_code = _parse_inputs(params)

    if amount is None or not from_code or not to_code:
        return (
            "I need an amount and two currencies. "
            "Try: convert 100 dollars to euros."
        )
    if from_code == to_code:
        return f"{_fmt(amount)} {from_code} is {_fmt(amount)} {to_code}."

    try:
        rate = _fetch_rate(from_code, to_code)
        converted = amount * rate
        return (
            f"{_fmt(amount)} {from_code} is about {_fmt(converted)} {to_code} "
            f"(at {rate:.4f} per {from_code}, ECB daily rate)."
        )
    except Exception as e:
        print(f"[Currency] live fetch failed: {e}")
        approx = _offline_convert(amount, from_code, to_code)
        if approx is not None:
            return (
                f"{_fmt(amount)} {from_code} is about {_fmt(approx)} {to_code} "
                "(approximate offline rate - no internet)."
            )
        return "I couldn't fetch exchange rates right now - no internet connection."
