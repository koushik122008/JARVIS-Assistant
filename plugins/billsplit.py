"""
MARK XLIX — Bill Split Calculator plugin

Fair bill splitting with optional tax, tip and skipped items (someone who
skipped the dessert doesn't pay for it). Pure calculation - no storage.
Tool name: bill_split
"""

import re

PLUGIN = {
    "name": "bill_split",
    "description": (
        "Splits a bill fairly between people. Use when the user wants to "
        "split a restaurant or group payment: 'split 86.40 between 4 "
        "people', 'split 50 with Ana, Ben and Cal', 'add 10 percent tip', "
        "'Ana skipped the dessert' (pass the dessert cost and the names who "
        "skipped it). Returns each person's share."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "total": {
                "type": "NUMBER",
                "description": "Bill subtotal before tax/tip",
            },
            "people": {
                "type": "NUMBER",
                "description": "Number of people (used when no names given)",
            },
            "names": {
                "type": "STRING",
                "description": "Optional list of names, e.g. 'Ana, Ben, Cal'",
            },
            "tax_percent": {
                "type": "NUMBER",
                "description": "Tax percentage, e.g. 8.25 (default 0)",
            },
            "tip_percent": {
                "type": "NUMBER",
                "description": "Tip percentage, e.g. 15 (default 0)",
            },
            "item_cost": {
                "type": "NUMBER",
                "description": "Cost of an item only some people had, e.g. the dessert (default 0)",
            },
            "skip": {
                "type": "STRING",
                "description": "Names who did NOT have that item, e.g. 'Ana'",
            },
        },
        "required": ["total"],
    },
}


def _names_list(raw: str) -> list:
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,;]|\s+and\s+", raw)
    names = [p.strip() for p in parts if p.strip()]
    # remove duplicate names preserving order
    seen = set()
    out = []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def handle(args: dict, ctx: dict) -> str:
    try:
        total = float((args or {}).get("total", 0))
    except (TypeError, ValueError):
        return "What's the bill total? Say 'split 86.40 between 4 people'."
    if total <= 0:
        return "The bill total needs to be a positive number."

    names = _names_list((args or {}).get("names", ""))
    if names:
        n = len(names)
    else:
        try:
            n = int((args or {}).get("people", 0))
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return "How many people? Say 'split 86.40 between 4 people' or name them: 'split 50 with Ana and Ben'."
        names = [f"Person {i}" for i in range(1, n + 1)]

    try:
        tax_pct = float((args or {}).get("tax_percent", 0) or 0)
        tip_pct = float((args or {}).get("tip_percent", 0) or 0)
        item_cost = float((args or {}).get("item_cost", 0) or 0)
    except (TypeError, ValueError):
        return "I couldn't read one of the numbers - give me totals and percentages."

    skip_raw = _names_list((args or {}).get("skip", ""))
    skip_set = {s.lower() for s in skip_raw}

    tax = total * tax_pct / 100.0
    tip = total * tip_pct / 100.0
    grand = total + tax + tip

    # Fair split: everyone pays the shared base; only non-skippers split
    # the skipped item among themselves.
    if item_cost > 0 and skip_set:
        payers = [nm for nm in names if nm.lower() not in skip_set]
        if not payers:
            return "Everyone skipped that item, so it shouldn't be on the bill."
        base = (grand - item_cost) / len(names)
        extra = item_cost / len(payers)
        # skippers pay only the shared base; everyone else also splits the item
        shares = {nm: base + (extra if nm.lower() not in skip_set else 0) for nm in names}
    else:
        base = grand / len(names)
        shares = {nm: base for nm in names}

    # Round to cents while keeping the sum exact (largest remainder method).
    rounded = {nm: int(v * 100) for nm, v in shares.items()}
    total_cents = sum(rounded.values())
    target = int(round(grand * 100))
    diff = target - total_cents
    for nm in sorted(shares, key=lambda k: shares[k], reverse=True):
        if diff == 0:
            break
        rounded[nm] += 1
        diff -= 1

    parts = []
    for nm in names:
        amt = rounded[nm] / 100.0
        label = nm
        if skip_set and nm.lower() in skip_set and item_cost > 0:
            label += " (skipped the item)"
        parts.append(f"{label}: ${amt:.2f}")
    parts.append(f"Total with tax {tax_pct:g}% + tip {tip_pct:g}%: ${grand:.2f}")

    result = "\n".join(parts)
    ui = (ctx or {}).get("ui")
    if ui and hasattr(ui, "show_content"):
        try:
            ui.show_content("BILL SPLIT", result)
        except Exception:
            pass
    return (
        f"Each of the {len(names)} people pays their share. "
        + " ".join(parts)
    )
