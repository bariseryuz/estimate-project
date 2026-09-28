"""
Default shade product catalogue for vision + estimation mapping.

Replace or extend via CATALOGUE_JSON_PATH pointing to a JSON file with the same shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CATALOGUE: list[dict[str, Any]] = [
    {
        "sku": "SR-M-STD",
        "name": "Solar Roller Shade — Manual",
        "category": "Solar / Screen",
        "mount": "Inside or Outside",
        "motorized": False,
        "widthRangeIn": [24, 96],
        "heightRangeIn": [24, 120],
        "listPriceUsd": 285,
        "notes": "Standard commercial solar fabric, chain operation",
    },
    {
        "sku": "SR-MOT-STD",
        "name": "Solar Roller Shade — Motorized",
        "category": "Solar / Screen",
        "mount": "Inside or Outside",
        "motorized": True,
        "widthRangeIn": [24, 120],
        "heightRangeIn": [24, 120],
        "listPriceUsd": 520,
        "notes": "Hard-wired or battery motor per spec",
    },
    {
        "sku": "BB-M-STD",
        "name": "Blackout Roller Shade — Manual",
        "category": "Blackout",
        "mount": "Inside or Outside",
        "motorized": False,
        "widthRangeIn": [24, 84],
        "heightRangeIn": [24, 96],
        "listPriceUsd": 340,
        "notes": "Room-darkening fabric",
    },
    {
        "sku": "BB-MOT-STD",
        "name": "Blackout Roller Shade — Motorized",
        "category": "Blackout",
        "mount": "Inside or Outside",
        "motorized": True,
        "widthRangeIn": [24, 96],
        "heightRangeIn": [24, 108],
        "listPriceUsd": 595,
        "notes": "Blackout + motor package",
    },
    {
        "sku": "CELL-M-STD",
        "name": "Cellular / Honeycomb — Manual",
        "category": "Cellular",
        "mount": "Inside Mount typical",
        "motorized": False,
        "widthRangeIn": [18, 72],
        "heightRangeIn": [24, 84],
        "listPriceUsd": 310,
        "notes": "Energy-efficient honeycomb",
    },
    {
        "sku": "CELL-MOT-STD",
        "name": "Cellular / Honeycomb — Motorized",
        "category": "Cellular",
        "mount": "Inside Mount typical",
        "motorized": True,
        "widthRangeIn": [18, 72],
        "heightRangeIn": [24, 84],
        "listPriceUsd": 565,
        "notes": "Honeycomb + motor package",
    },
    {
        "sku": "DUAL-M",
        "name": "Dual Shade (Day/Night) — Manual",
        "category": "Dual",
        "mount": "Inside Mount",
        "motorized": False,
        "widthRangeIn": [30, 84],
        "heightRangeIn": [36, 96],
        "listPriceUsd": 625,
        "notes": "Two bands in one opening: solar 285 + blackout 340",
    },
    {
        "sku": "DUAL-MOT",
        "name": "Dual Shade (Day/Night) — Motorized",
        "category": "Dual",
        "mount": "Inside Mount",
        "motorized": True,
        "widthRangeIn": [30, 84],
        "heightRangeIn": [36, 96],
        "listPriceUsd": 780,
        "notes": "Solar + blackout combo system",
    },
]


def load_catalogue() -> list[dict[str, Any]]:
    path = os.getenv("CATALOGUE_JSON_PATH", "").strip()
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "data" / "catalogue.json")
    for p in candidates:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    return DEFAULT_CATALOGUE


def catalogue_for_prompt() -> str:
    """Compact catalogue block for LLM prompts."""
    lines = []
    for item in load_catalogue():
        w = item.get("widthRangeIn") or []
        h = item.get("heightRangeIn") or []
        lines.append(
            f"- {item['sku']}: {item['name']} | {item['category']} | "
            f"motorized={item.get('motorized')} | "
            f"size {w[0] if w else '?'}-{w[-1] if w else '?'}\" W × "
            f"{h[0] if h else '?'}-{h[-1] if h else '?'}\" H | "
            f"list ${item.get('listPriceUsd', 0)}"
        )
    return "\n".join(lines)
