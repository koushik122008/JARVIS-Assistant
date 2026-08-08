"""
MARK XLIX - Live Crypto Prices

Fetches REAL prices from the CoinGecko public API (free, no API key
required) and returns a spoken-friendly summary with 24h change.

If the network is unavailable it falls back to a small built-in table
(clearly labelled as approximate).

API used:
  - https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd
"""

import requests

API_URL = "https://api.coingecko.com/api/v3/simple/price"
_TIMEOUT = 10.0

# alias -> coingecko id
_CRYPTO = {
    "bitcoin":  "bitcoin",     "btc": "bitcoin",
    "ethereum": "ethereum",    "eth": "ethereum",
    "solana":   "solana",      "sol": "solana",
    "dogecoin": "dogecoin",    "doge": "dogecoin",
    "cardano":  "cardano",     "ada": "cardano",
    "ripple":   "ripple",      "xrp": "ripple",
    "litecoin": "litecoin",    "ltc": "litecoin",
    "polkadot": "polkadot",    "dot": "polkadot",
    "binance coin": "binancecoin", "bnb": "binancecoin",
    "chainlink": "chainlink",  "link": "chainlink",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "tether":   "tether",      "usdt": "tether",
    "usd coin": "usd-coin",    "usdc": "usd-coin",
    "toncoin":  "the-open-network", "ton": "the-open-network",
    "polygon":  "matic-network", "matic": "matic-network",
    "shiba":    "shiba-inu",   "shib": "shiba-inu",
    "uniswap":  "uniswap",     "uni": "uniswap",
    "near":     "near",        "near protocol": "near",
    "cosmos":   "cosmos",      "atom": "cosmos",
}

_OFFLINE = {
    "bitcoin": 61000.0, "ethereum": 3300.0, "solana": 145.0,
    "dogecoin": 0.11, "cardano": 0.38, "ripple": 0.52,
    "litecoin": 85.0, "polkadot": 6.1, "binancecoin": 580.0,
    "chainlink": 13.5, "avalanche-2": 27.0, "tether": 1.0,
    "usd-coin": 1.0, "the-open-network": 5.4, "matic-network": 0.55,
    "shiba-inu": 0.000013, "uniswap": 7.2, "near": 4.8, "cosmos": 6.5,
}

_NAMES = {
    "bitcoin": "Bitcoin", "ethereum": "Ethereum", "solana": "Solana",
    "dogecoin": "Dogecoin", "cardano": "Cardano", "ripple": "XRP",
    "litecoin": "Litecoin", "polkadot": "Polkadot", "binancecoin": "BNB",
    "chainlink": "Chainlink", "avalanche-2": "Avalanche", "tether": "Tether",
    "usd-coin": "USD Coin", "the-open-network": "Toncoin",
    "matic-network": "Polygon", "shiba-inu": "Shiba Inu",
    "uniswap": "Uniswap", "near": "NEAR", "cosmos": "Cosmos",
}

_TOP = [
    "bitcoin", "ethereum", "solana", "binancecoin", "ripple",
    "dogecoin", "cardano", "the-open-network",
]


def _resolve_id(token: str):
    t = (token or "").strip().lower()
    if not t:
        return None
    return _CRYPTO.get(t)


def _fetch_price(coin_id: str, vs: str = "usd"):
    resp = requests.get(
        API_URL,
        params={
            "ids": coin_id,
            "vs_currencies": vs,
            "include_24hr_change": "true",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json().get(coin_id)
    if not data:
        raise ValueError(f"no data for {coin_id}")
    return {
        "price": float(data.get(vs, 0)),
        "change_24h": float(data.get(f"{vs}_24h_change") or 0),
    }


def _fmt_price(num: float) -> str:
    if num >= 1000:
        return f"${num:,.0f}"
    if num >= 1:
        return f"${num:,.2f}"
    return f"${num:.6f}".rstrip("0").rstrip(".")


def _describe(coin_id: str, price: float, change: float) -> str:
    name = _NAMES.get(coin_id, coin_id)
    if change >= 0:
        return f"{name} is {_fmt_price(price)}, up {change:.1f}% in 24 hours."
    return f"{name} is {_fmt_price(price)}, down {abs(change):.1f}% in 24 hours."


def crypto_prices(parameters=None, response=None, player=None,
                  session_memory=None) -> str:
    params = parameters or {}
    asset  = (params.get("asset") or params.get("coin")
              or params.get("crypto") or "").strip()
    vs     = (params.get("currency") or "usd").strip().lower()
    if vs not in ("usd", "eur", "gbp", "try"):
        vs = "usd"

    coin_id = _resolve_id(asset)

    try:
        if coin_id:
            data = _fetch_price(coin_id, vs)
            return _describe(coin_id, data["price"], data["change_24h"])
        # no specific coin -> show the top watchlist
        parts = []
        for cid in _TOP:
            try:
                d = _fetch_price(cid, vs)
                parts.append(_describe(cid, d["price"], d["change_24h"]))
            except Exception:
                continue
        if parts:
            return " ".join(parts[:6])
        raise RuntimeError("no live data")
    except Exception as e:
        print(f"[Crypto] live fetch failed: {e}")
        if coin_id and coin_id in _OFFLINE:
            return (
                f"{_describe(coin_id, _OFFLINE[coin_id], 0.0)} "
                "(approximate offline price - no internet)."
            )
        if coin_id:
            return f"I couldn't fetch a live price for {asset} right now."
        return (
            "I couldn't fetch crypto prices right now - no internet connection. "
            "Try naming one coin, like 'what is bitcoin at?'"
        )
