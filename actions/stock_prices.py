"""
MARK XLIX - Live Stock Prices

Fetches REAL quotes from the Stooq free CSV endpoint (no API key required)
and returns a spoken-friendly summary with intraday change.

If the network is unavailable it returns a clear error (no fake prices).

API used:
  - https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv
"""

import io
import csv

import requests

API_URL = "https://stooq.com/q/l/"
_TIMEOUT = 10.0

_NAMES = {
    "aapl": "Apple", "msft": "Microsoft", "googl": "Alphabet", "goog": "Alphabet",
    "amzn": "Amazon", "nvda": "NVIDIA", "meta": "Meta", "tsla": "Tesla",
    "brk-b": "Berkshire Hathaway", "jpm": "JPMorgan", "v": "Visa",
    "unh": "UnitedHealth", "xom": "Exxon Mobil", "jnj": "Johnson & Johnson",
    "wmt": "Walmart", "ma": "Mastercard", "pg": "Procter & Gamble",
    "hd": "Home Depot", "cvv": "CVS", "dis": "Disney",
    "intc": "Intel", "csco": "Cisco", "qcom": "Qualcomm", "amd": "AMD",
    "nflx": "Netflix", "adbe": "Adobe", "pypl": "PayPal", "orcl": "Oracle",
    "sap": "SAP", "crm": "Salesforce", "ibm": "IBM", "t": "AT&T",
    "vz": "Verizon", "ko": "Coca-Cola", "pep": "PepsiCo", "mcd": "McDonald's",
    "sbux": "Starbucks", "nke": "Nike", "ba": "Boeing", "cat": "Caterpillar",
    "ge": "GE Aerospace", "ford": "Ford", "gm": "GM", "f": "Ford",
    "tlry": "Tilray", "pltr": "Palantir", "coin": "Coinbase", "shop": "Shopify",
}


def _normalize_ticker(ticker: str) -> str:
    t = (ticker or "").strip().lower()
    if not t:
        return ""
    if "." in t:          # already has an exchange suffix (aapl.us, 005930.ks)
        return t
    if len(t) <= 4:       # US tickers are 1-4 letters + optional BRK-B style
        return f"{t}.us"
    if t in ("brk-b",):
        return "brk-b.us"
    return t              # longer tickers: leave as-is (may fail cleanly)


def _fetch_quote(ticker: str) -> dict:
    resp = requests.get(
        API_URL,
        params={"s": ticker, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        close = row.get("Close", "")
        if close in ("", "N/D"):
            raise ValueError(f"no data for {ticker}")
        return {
            "ticker": row.get("Symbol", ticker),
            "close": float(close),
            "open": float(row.get("Open") or 0),
            "high": float(row.get("High") or 0),
            "low": float(row.get("Low") or 0),
            "date": row.get("Date", ""),
        }
    raise ValueError(f"no data for {ticker}")


def _fmt(num: float) -> str:
    if num >= 1000:
        return f"${num:,.2f}"
    return f"${num:,.2f}"


def _describe(quote: dict) -> str:
    name = _NAMES.get(quote["ticker"].split(".")[0], quote["ticker"].split(".")[0].upper())
    change = quote["close"] - quote["open"]
    pct = (change / quote["open"] * 100) if quote["open"] else 0.0
    direction = "up" if change >= 0 else "down"
    return (
        f"{name} is trading at {_fmt(quote['close'])}, "
        f"{direction} {abs(change):.2f} ({abs(pct):.2f}%) on the day. "
        f"Day range {_fmt(quote['low'])} to {_fmt(quote['high'])}."
    )


def stock_prices(parameters=None, response=None, player=None,
                 session_memory=None) -> str:
    params = parameters or {}
    raw = (params.get("ticker") or params.get("symbol") or params.get("stock")
           or "").strip()
    ticker = _normalize_ticker(raw)

    if not ticker:
        return (
            "I need a ticker symbol. Try 'check AAPL' or 'what is TSLA at?' "
            "I cover US stocks by default."
        )

    try:
        quote = _fetch_quote(ticker)
        return _describe(quote)
    except Exception as e:
        print(f"[Stock] live fetch failed: {e}")
        return (
            f"I couldn't fetch a quote for {raw.upper()} right now. "
            f"Check the ticker - e.g. 'AAPL', 'MSFT', 'NVDA' - or try again later."
        )
