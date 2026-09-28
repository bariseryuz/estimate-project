"""Parse construction dimensions and compute square footage for shades."""

from __future__ import annotations

import re
from typing import Any, Optional

_INCHES_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:\"|''|in\b|inch|inches)",
    re.IGNORECASE,
)
_FEET_IN_RE = re.compile(
    r"(\d+)\s*(?:'|ft|feet)\s*-?\s*(\d+(?:\.\d+)?)?",
    re.IGNORECASE,
)
_MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\b", re.IGNORECASE)
_PLAIN_NUM = re.compile(r"^(\d+(?:\.\d+)?)$")


def parse_length_inches(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("tbd", "n/a", "unknown", "—"):
        return None

    m = _FEET_IN_RE.search(s)
    if m:
        feet = float(m.group(1))
        inches = float(m.group(2) or 0)
        return feet * 12 + inches

    m = _INCHES_RE.search(s)
    if m:
        return float(m.group(1))

    m = _MM_RE.search(s)
    if m:
        return float(m.group(1)) / 25.4

    m = _PLAIN_NUM.match(s.replace(",", ""))
    if m:
        n = float(m.group(1))
        # Heuristic: values under 20 treated as feet (common in drawings)
        if n <= 20:
            return n * 12
        return n

    if "×" in s or "x" in s.lower():
        parts = re.split(r"[×x]", s, maxsplit=1)
        if parts:
            return parse_length_inches(parts[0].strip())

    return None


def square_feet(width_in: Optional[float], height_in: Optional[float]) -> Optional[float]:
    if width_in is None or height_in is None or width_in <= 0 or height_in <= 0:
        return None
    return round((width_in * height_in) / 144.0, 2)


def format_dim_inches(inches: Optional[float]) -> str:
    if inches is None:
        return "TBD"
    if abs(inches - round(inches)) < 0.05:
        return f'{int(round(inches))}"'
    return f'{inches:.1f}"'


def format_feet_and_inches(inches: Optional[float]) -> str:
    """Construction display: 41 inches → 3'-5\"."""
    if inches is None:
        return ""
    sign = "-" if inches < 0 else ""
    total = abs(float(inches))
    feet = int(total // 12)
    rem = total - feet * 12
    if abs(rem - round(rem)) < 0.05:
        rem_i = int(round(rem))
        if rem_i == 12:
            feet += 1
            rem_i = 0
        return f'{sign}{feet}\'-{rem_i}"'
    return f'{sign}{feet}\'-{rem:.1f}"'


def enrich_takeoff_item(item: dict[str, Any]) -> None:
    w_in = item.get("widthInches")
    h_in = item.get("heightInches")
    if w_in is None:
        w_in = parse_length_inches(item.get("width"))
    if h_in is None:
        h_in = parse_length_inches(item.get("height"))
    if w_in is not None:
        item["widthInches"] = round(w_in, 2)
    if h_in is not None:
        item["heightInches"] = round(h_in, 2)
    sq = square_feet(w_in, h_in)
    if sq is not None:
        item["squareFeet"] = sq

    qty = int(item.get("quantity") or 1)
    if not item.get("calculationBasis"):
        tag = item.get("windowTag") or item.get("id") or "Opening"
        loc = item.get("location") or item.get("room") or ""
        w = item.get("width") or format_dim_inches(w_in)
        h = item.get("height") or format_dim_inches(h_in)
        src = item.get("sourceLocation") or "schedule"
        item["calculationBasis"] = (
            f"{qty} EA for tag {tag}"
            + (f" @ {loc}" if loc else "")
            + f"; size {w} × {h} (W×H); counted from {src}"
        )
