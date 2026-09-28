"""
Deterministic shade pricing — every number carries the formula that produced it.

Two stages, both auditable:

  1. Catalogue basis — pick a SKU per line from its system type, motorization, and
     size, then price it as `list × size factor + labor`.
  2. Bid Summary calibration — when the Bid Summary workbook gives a grand total,
     scale the catalogue basis so the project subtotal equals the bid. The scale
     factor is reported on every line instead of being hidden in the total.

Nothing here calls an LLM, so the same inputs always produce the same estimate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from domain.catalogue import load_catalogue
from domain.direct_shades_workbooks import areas_match
from utils.dimensions import square_feet

#: Reference opening used as the pricing baseline: 4'0" × 6'0" = 24 sq ft.
DEFAULT_NOMINAL_SQFT = 24.0


@dataclass(frozen=True)
class PricingPolicy:
    labor_per_unit: float = 65.0
    overhead_pct: float = 10.0
    profit_pct: float = 8.0
    nominal_sqft: float = DEFAULT_NOMINAL_SQFT

    def describe(self) -> str:
        return (
            f"${self.labor_per_unit:,.2f} install labor per shade; "
            f"{self.overhead_pct:g}% overhead; {self.profit_pct:g}% profit; "
            f"size factor referenced to {self.nominal_sqft:g} sq ft"
        )


def get_pricing_policy() -> PricingPolicy:
    """Pricing knobs from the environment so a branch office can retune without code."""
    return PricingPolicy(
        labor_per_unit=_env_float("SHADE_LABOR_PER_UNIT", 65.0),
        overhead_pct=_env_float("ESTIMATE_OVERHEAD_PCT", 10.0),
        profit_pct=_env_float("ESTIMATE_PROFIT_PCT", 8.0),
        nominal_sqft=_env_float("SHADE_NOMINAL_SQFT", DEFAULT_NOMINAL_SQFT),
    )


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_CATEGORY_KEYWORDS = (
    ("Blackout", ("blackout", "black out", "black-out", "room darken", "bo ")),
    ("Dual", ("dual", "day/night", "day night", "combo")),
    ("Cellular", ("cellular", "honeycomb", "cell ")),
    ("Solar / Screen", ("solar", "screen", "sheer", "sunscreen", "roller", "shade")),
)

_MOTOR_KEYWORDS = ("motor", "motorized", "motorised", "rts", "battery", "hardwire", "somfy")


def category_for_system(system_type: Optional[str]) -> str:
    """Map a free-text system/fabric description to a catalogue category."""
    text = (system_type or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(k in text for k in keywords):
            return category
    return "Solar / Screen"


def looks_motorized(*values: Any) -> bool:
    for value in values:
        if value is True:
            return True
        text = str(value or "").lower()
        if any(k in text for k in _MOTOR_KEYWORDS):
            return True
    return False


def select_catalogue_item(
    *,
    system_type: Optional[str] = None,
    motorized: bool = False,
    width_in: Optional[float] = None,
    height_in: Optional[float] = None,
) -> tuple[dict[str, Any], str]:
    """
    Pick the closest catalogue SKU and explain the choice.

    Scoring prefers the matching category, then the matching operation (manual vs
    motorized), then a SKU whose published size range actually covers the opening.
    """
    catalogue = load_catalogue()
    if not catalogue:
        raise ValueError("Shade catalogue is empty — check data/catalogue.json")

    target_category = category_for_system(system_type)
    best: Optional[dict[str, Any]] = None
    best_score = float("-inf")

    for item in catalogue:
        score = 0.0
        if str(item.get("category") or "") == target_category:
            score += 10
        if bool(item.get("motorized")) == bool(motorized):
            score += 6
        if _size_fits(item, width_in, height_in):
            score += 3
        # Cheaper SKU breaks ties so estimates stay conservative.
        score -= float(item.get("listPriceUsd") or 0) / 10000.0
        if score > best_score:
            best, best_score = item, score

    assert best is not None

    # The reason has to describe the SKU that was actually picked. Claiming a category
    # match that the catalogue could not supply hides a substitution inside a number
    # the estimator will send to a client.
    category_matched = str(best.get("category") or "") == target_category
    operation_matched = bool(best.get("motorized")) == bool(motorized)
    if category_matched:
        reason_parts = [f"category '{target_category}'"]
    else:
        reason_parts = [
            f"no '{target_category}' SKU in the catalogue — substituted "
            f"'{best.get('category')}'; confirm the product before sending"
        ]
    if operation_matched:
        reason_parts.append("motorized" if motorized else "manual operation")
    else:
        reason_parts.append(
            f"catalogue has no {'motorized' if motorized else 'manual'} SKU in this "
            f"category — priced as {'motorized' if best.get('motorized') else 'manual'}"
        )
    if width_in and height_in and not _size_fits(best, width_in, height_in):
        reason_parts.append("size outside published range — field verify")
    return best, f"SKU {best['sku']} chosen for " + ", ".join(reason_parts)


def _absorb_rounding(priced: list[dict[str, Any]], target_total: float) -> None:
    """
    Make the lines foot to the project total exactly.

    Rounding each calibrated line to the cent leaves a few cents of drift, and a
    schedule whose lines do not add up to the total it is printed next to reads as a
    mistake. The residual goes on the largest line, where it is proportionally smallest.
    """
    adjustable = [p for p in priced if p.get("extendedPrice") is not None]
    if not adjustable:
        return
    residual = round(target_total - sum(float(p["extendedPrice"]) for p in adjustable), 2)
    if residual == 0:
        return
    largest = max(adjustable, key=lambda p: float(p["extendedPrice"]))
    largest["extendedPrice"] = round(float(largest["extendedPrice"]) + residual, 2)
    largest["pricingFormula"] = (
        f"{largest['pricingFormula']} {'+' if residual > 0 else '−'} "
        f"${abs(residual):,.2f} rounding to foot to the project total "
        f"= ${largest['extendedPrice']:,.2f}"
    )


def catalogue_match_quality(item: dict, *, system_type: Optional[str], motorized: bool) -> str:
    """"exact" when category and operation both matched, otherwise "substituted"."""
    if str(item.get("category") or "") != category_for_system(system_type):
        return "substituted"
    if bool(item.get("motorized")) != bool(motorized):
        return "substituted"
    return "exact"


def _size_fits(item: dict, width_in: Optional[float], height_in: Optional[float]) -> bool:
    if not width_in or not height_in:
        return False
    w_range = item.get("widthRangeIn") or []
    h_range = item.get("heightRangeIn") or []
    if len(w_range) < 2 or len(h_range) < 2:
        return False
    return w_range[0] <= width_in <= w_range[-1] and h_range[0] <= height_in <= h_range[-1]


def size_factor(sq_ft: Optional[float], policy: PricingPolicy) -> float:
    """Linear uplift for openings larger than the nominal reference opening."""
    if not sq_ft or sq_ft <= 0 or policy.nominal_sqft <= 0:
        return 1.0
    return round(max(1.0, sq_ft / policy.nominal_sqft), 2)


def price_line(
    line: dict[str, Any],
    policy: Optional[PricingPolicy] = None,
) -> dict[str, Any]:
    """Price one take-off/schedule line from the catalogue, showing every step."""
    policy = policy or get_pricing_policy()
    qty = max(1, int(line.get("quantity") or 1))
    width_in = _as_float(line.get("widthInches"))
    height_in = _as_float(line.get("heightInches"))
    sq_ft = _as_float(line.get("squareFeetEach")) or square_feet(width_in, height_in)

    system_type = line.get("systemType") or line.get("category") or line.get("item")
    motorized = looks_motorized(line.get("motorized"), system_type)
    item, reason = select_catalogue_item(
        system_type=system_type,
        motorized=motorized,
        width_in=width_in,
        height_in=height_in,
    )

    list_price = float(item.get("listPriceUsd") or 0)
    factor = size_factor(sq_ft, policy)
    material = round(list_price * factor, 2)
    labor = round(policy.labor_per_unit, 2)
    unit_price = round(material + labor, 2)
    extended = round(unit_price * qty, 2)

    formula = (
        f"{qty} EA × (${list_price:,.2f} list × {factor:g} size factor "
        f"+ ${labor:,.2f} labor) = ${extended:,.2f}"
    )
    if factor == 1.0:
        formula = (
            f"{qty} EA × (${list_price:,.2f} list + ${labor:,.2f} labor) = ${extended:,.2f}"
        )

    return {
        "catalogueSku": item.get("sku"),
        "catalogueName": item.get("name"),
        "catalogueReason": reason,
        "catalogueMatch": catalogue_match_quality(
            item, system_type=system_type, motorized=motorized
        ),
        "sizeFactor": factor,
        "squareFeetEach": sq_ft,
        "unitMaterial": material,
        "unitLabor": labor,
        "unitPrice": unit_price,
        "extendedPrice": extended,
        "pricingFormula": formula,
        "priceSource": "catalogue",
    }


def price_from_bid_line(line: dict[str, Any], bid: dict[str, Any]) -> dict[str, Any]:
    """Use the bid sheet's sales price for this opening. That figure is already a sell price."""
    qty = max(1, int(line.get("quantity") or 1))
    sales = _as_float(bid.get("salesPrice"))
    extended = _as_float(bid.get("totalPrice"))
    if sales is not None and int(bid.get("quantity") or qty) != qty:
        extended = round(sales * qty, 2)
    elif extended is None and sales is not None:
        extended = round(sales * qty, 2)
    if sales is None and extended is not None:
        sales = round(extended / qty, 2)
    cost_each = _as_float(bid.get("costEach"))
    source = bid.get("sourceLocation") or bid.get("sheet") or "Bid sheet"
    formula = (
        f"{qty} EA × ${sales:,.2f} sales price = ${extended:,.2f}"
        if sales is not None and extended is not None
        else ""
    )
    return {
        "catalogueSku": None,
        "catalogueName": line.get("item") or line.get("category"),
        "catalogueReason": f"Sales price from {source}",
        "sizeFactor": None,
        "squareFeetEach": _as_float(line.get("squareFeet")) or _as_float(bid.get("squareFeet")),
        "unitMaterial": cost_each,
        "unitLabor": None,
        "unitPrice": sales,
        "extendedPrice": extended or 0.0,
        "pricingFormula": formula,
        "priceSource": "bid_line",
    }


def _match_bid_line(item: dict, lines: list[dict], used: set[int]) -> Optional[dict]:
    """Match a take-off row to one quoted bid row by tag, then by area."""
    tag = _tag_key(item.get("windowTag"))
    section = item.get("areaSection") or item.get("floor") or ""
    candidates = [
        i
        for i, line in enumerate(lines)
        if i not in used and _tag_key(line.get("description")) == tag
    ]
    if not candidates:
        return None

    def take(index: int) -> dict:
        used.add(index)
        return lines[index]

    if section:
        scoped = [i for i in candidates if areas_match(section, lines[i].get("section") or "")]
        if len(scoped) == 1:
            return take(scoped[0])
        if len(scoped) > 1:
            qty = int(item.get("quantity") or 0)
            same_qty = [i for i in scoped if int(lines[i].get("quantity") or 0) == qty]
            return take((same_qty or scoped)[0])
    if len(candidates) == 1:
        return take(candidates[0])
    return None


def _tag_key(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def price_takeoff(
    lines: list[dict[str, Any]],
    *,
    workbook: Optional[dict] = None,
    policy: Optional[PricingPolicy] = None,
) -> dict[str, Any]:
    """
    Price a whole take-off and roll it up to a project total.

    A matching bid row's sales price wins. Catalogue math is only the fallback.
    When the bid workbook has a quoted total and most lines matched, that total
    is the sell price — alternate tabs are already excluded from it.
    """
    policy = policy or get_pricing_policy()
    reference = (workbook or {}).get("referencePricing") or {}
    reference_total = _as_float(reference.get("grandTotal"))
    bid_lines = list(reference.get("lines") or [])
    used: set[int] = set()

    priced: list[dict[str, Any]] = []
    bid_qty = 0
    for line in lines:
        match = _match_bid_line(line, bid_lines, used) if bid_lines else None
        if match and (_as_float(match.get("salesPrice")) is not None or _as_float(match.get("totalPrice")) is not None):
            merged = {**line, **price_from_bid_line(line, match)}
            bid_qty += max(1, int(line.get("quantity") or 1))
        else:
            merged = {**line, **price_line(line, policy)}
        priced.append(merged)

    catalogue_subtotal = round(
        sum(p["extendedPrice"] for p in priced if p.get("priceSource") != "bid_line"),
        2,
    )
    bid_subtotal = round(
        sum(p["extendedPrice"] for p in priced if p.get("priceSource") == "bid_line"),
        2,
    )
    traced_total = round(bid_subtotal + catalogue_subtotal, 2)
    takeoff_qty = sum(max(1, int(p.get("quantity") or 1)) for p in priced) or 0
    bid_coverage = (bid_qty / takeoff_qty) if takeoff_qty else 0
    steps: list[dict[str, str]] = []
    if bid_qty:
        steps.append(
            {
                "label": "Bid line prices",
                "value": _money(bid_subtotal),
                "formula": f"{bid_qty} shades priced from the Sales Price on the matching bid row",
                "reference": reference.get("source") or "Bid sheets",
            }
        )
    if catalogue_subtotal:
        steps.append(
            {
                "label": "Catalogue basis",
                "value": _money(catalogue_subtotal),
                "formula": "Unmatched lines: list × size factor + labor",
                "reference": f"Shade catalogue · {policy.describe()}",
            }
        )

    calibration: Optional[float] = None
    quoted_sheets = ", ".join(reference.get("quotedSheets") or []) or "Quoted bid tabs"
    alternates = ", ".join(reference.get("alternateSheets") or [])

    if bid_coverage >= 0.8:
        subtotal = traced_total
        overhead = 0.0
        profit = 0.0
        total = traced_total
        price_source = "bid_line"
        client_total = _as_float(reference.get("clientTotal"))
        if reference_total and abs(reference_total - traced_total) > 1:
            steps.append(
                {
                    "label": "Bid sheet total",
                    "value": _money(reference_total),
                    "formula": "Total Price on the quoted tabs, for comparison with the line sum",
                    "reference": reference.get("grandTotalBasis") or quoted_sheets,
                }
            )
        if client_total and abs(client_total - traced_total) > 1:
            steps.append(
                {
                    "label": "Product sales price",
                    "value": _money(traced_total),
                    "formula": "Sum of Sales Price × quantity on the quoted tabs",
                    "reference": quoted_sheets,
                }
            )
            total = client_total
            formula = (
                "Total Bid printed on the quoted tabs. That number includes installation, "
                "other charges, and tax. A tab that lists motor quantities uses its "
                "motorization grand total. The accounting grand total is not added."
            )
        else:
            formula = "Sum of sales price × quantity. Tabs that repeat the same markings are not added."
        if alternates:
            formula += f" Left out: {alternates}."
        steps.append(
            {
                "label": "Project total",
                "value": _money(total),
                "formula": formula,
                "reference": quoted_sheets,
            }
        )
    elif reference_total and catalogue_subtotal > 0:
        calibration = round(reference_total / max(traced_total, 0.01), 4)
        for p in priced:
            if p.get("priceSource") == "bid_line" or p.get("unitPrice") is None:
                continue
            p["unitPrice"] = round(p["unitPrice"] * calibration, 2)
            if p.get("unitMaterial") is not None:
                p["unitMaterial"] = round(p["unitMaterial"] * calibration, 2)
            p["extendedPrice"] = round(p["extendedPrice"] * calibration, 2)
            p["pricingFormula"] = (
                f"{p['pricingFormula']} × {calibration:g} Bid Summary calibration "
                f"= ${p['extendedPrice']:,.2f}"
            )
            p["priceSource"] = "bid_summary_calibrated"
        _absorb_rounding(priced, reference_total)
        subtotal = reference_total
        overhead = 0.0
        profit = 0.0
        total = reference_total
        steps.append(
            {
                "label": "Bid Summary calibration",
                "value": f"× {calibration:g}",
                "formula": (
                    f"{_money(reference_total)} quoted bid total ÷ "
                    f"{_money(traced_total)} line basis"
                ),
                "reference": reference.get("source") or "Bid Summary workbook",
            }
        )
        steps.append(
            {
                "label": "Project total",
                "value": _money(total),
                "formula": "Quoted bid total used as the sell price (alternate tabs excluded)",
                "reference": reference.get("grandTotalBasis") or quoted_sheets,
            }
        )
        price_source = "bid_summary"
    elif not bid_qty:
        subtotal = catalogue_subtotal
        overhead = round(subtotal * policy.overhead_pct / 100.0, 2)
        profit = round((subtotal + overhead) * policy.profit_pct / 100.0, 2)
        total = round(subtotal + overhead + profit, 2)
        steps.append(
            {
                "label": "Overhead",
                "value": _money(overhead),
                "formula": f"{_money(subtotal)} subtotal × {policy.overhead_pct:g}%",
                "reference": "Pricing policy (ESTIMATE_OVERHEAD_PCT)",
            }
        )
        steps.append(
            {
                "label": "Profit",
                "value": _money(profit),
                "formula": f"({_money(subtotal)} + {_money(overhead)}) × {policy.profit_pct:g}%",
                "reference": "Pricing policy (ESTIMATE_PROFIT_PCT)",
            }
        )
        steps.append(
            {
                "label": "Project total",
                "value": _money(total),
                "formula": "Subtotal + overhead + profit",
                "reference": "Deterministic catalogue pricing",
            }
        )
        price_source = "catalogue"
    else:
        subtotal = traced_total
        overhead = 0.0
        profit = 0.0
        total = traced_total
        price_source = "bid_line"
        steps.append(
            {
                "label": "Project total",
                "value": _money(total),
                "formula": "Sum of matched bid sales prices plus catalogue prices for the rest",
                "reference": quoted_sheets,
            }
        )

    unit_count = sum(max(1, int(p.get("quantity") or 1)) for p in priced) or None
    return {
        "lines": priced,
        "subtotal": subtotal,
        "overhead": overhead,
        "profit": profit,
        "total": total,
        "currency": "USD",
        "priceSource": price_source,
        "catalogueSubtotal": catalogue_subtotal,
        "calibrationFactor": calibration,
        "pricePerUnit": round(total / unit_count, 2) if unit_count else None,
        "policy": policy.describe(),
        "steps": steps,
    }


def build_estimate_from_takeoff(
    takeoff: dict[str, Any],
    workbook: Optional[dict] = None,
    policy: Optional[PricingPolicy] = None,
) -> dict[str, Any]:
    """
    Build a full estimation payload (same shape as the LLM agent) from a take-off.

    Used on the Excel fast path so counts and dollars are reproducible; the LLM is
    left to write prose, not arithmetic.
    """
    items = takeoff.get("takeoffItems") or []
    priced = price_takeoff(items, workbook=workbook, policy=policy)
    total_shades = takeoff.get("totalShadeCount") or sum(
        max(1, int(i.get("quantity") or 1)) for i in items
    )

    estimates = [
        {
            "id": line.get("windowTag") or f"LINE-{idx + 1}",
            "windowTag": line.get("windowTag"),
            "category": line.get("category") or line.get("catalogueName"),
            "item": line.get("catalogueName") or line.get("item"),
            "quantity": max(1, int(line.get("quantity") or 1)),
            "unit": "EA",
            "width": line.get("width"),
            "height": line.get("height"),
            "unitCost": line.get("unitPrice"),
            "laborCost": line.get("unitLabor"),
            "materialCost": line.get("unitMaterial"),
            "totalCost": line.get("extendedPrice"),
            "calculationFormula": line.get("pricingFormula"),
            "catalogueSku": line.get("catalogueSku"),
            "catalogueMatch": line.get("catalogueMatch"),
            "catalogueReason": line.get("catalogueReason") or "",
            "priceSource": line.get("priceSource"),
            "notes": line.get("catalogueReason") or "",
        }
        for idx, line in enumerate(priced["lines"])
    ]

    total_sqft = round(
        sum(
            (_as_float(l.get("squareFeetEach")) or 0) * max(1, int(l.get("quantity") or 1))
            for l in priced["lines"]
        ),
        2,
    ) or None

    return {
        "estimates": estimates,
        "shadeSummary": {
            "totalShades": total_shades,
            "motorizedCount": sum(1 for l in priced["lines"] if looks_motorized(l.get("motorized"))),
            "byType": _by_type(priced["lines"]),
            "totalSquareFeet": total_sqft,
        },
        "subtotal": priced["subtotal"],
        "overhead": priced["overhead"],
        "profit": priced["profit"],
        "totalEstimate": priced["total"],
        "currency": priced["currency"],
        "priceSource": priced["priceSource"],
        "pricingSteps": priced["steps"],
        "pricingPolicy": priced["policy"],
        "calibrationFactor": priced["calibrationFactor"],
        "catalogueSubtotal": priced["catalogueSubtotal"],
        "assumptions": _assumptions(priced, workbook),
        "executiveSummary": _executive_summary(total_shades, priced, workbook),
    }


def _by_type(lines: list[dict]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for line in lines:
        key = str(line.get("catalogueName") or line.get("category") or "Window shade")
        entry = totals.setdefault(key, {"type": key, "count": 0, "subtotal": 0.0})
        entry["count"] += max(1, int(line.get("quantity") or 1))
        entry["subtotal"] = round(entry["subtotal"] + (line.get("extendedPrice") or 0), 2)
    return list(totals.values())


def _assumptions(priced: dict, workbook: Optional[dict]) -> list[str]:
    out = [
        f"Pricing policy: {priced['policy']}.",
        "Catalogue SKU per line selected from system type, motorization, and opening size.",
        "Openings without dimensions in the source are priced at the nominal size — field verify.",
    ]
    reference = (workbook or {}).get("referencePricing") or {}
    alternates = reference.get("alternateSheets") or []
    if priced.get("priceSource") == "bid_line":
        out.insert(0, "Each matched line uses the Sales Price from the quoted bid tab.")
        client_total = _as_float(reference.get("clientTotal"))
        if client_total and abs(client_total - (priced.get("subtotal") or 0)) > 1:
            out.insert(
                1,
                "The offer total is the sheet Total Bid (installation, charges, and tax included). "
                "When a quoted tab lists motor quantities, its motorization grand total is used. "
                "The accounting grand total is not the client price.",
            )
        if alternates:
            out.insert(1, "Not added, because those tabs price the same markings: " + ", ".join(alternates) + ".")
    elif priced.get("calibrationFactor"):
        out.insert(
            0,
            f"Project total anchored to the quoted bid total "
            f"({_money(reference.get('grandTotal'))}); unmatched lines calibrated by "
            f"× {priced['calibrationFactor']:g}.",
        )
    else:
        out.append("No Bid Summary dollars were available, so pricing is catalogue-based.")
    return out


def _executive_summary(total_shades: Any, priced: dict, workbook: Optional[dict]) -> str:
    project = (workbook or {}).get("projectName") or "this project"
    reference = (workbook or {}).get("referencePricing") or {}
    client_total = _as_float(reference.get("clientTotal"))
    product = _as_float(priced.get("subtotal"))
    if client_total and product is not None and abs(client_total - product) > 1:
        anchor = (
            f"Shade sales price is {_money(product)}. "
            f"The sheet Total Bid is {_money(client_total)}, including installation, charges, and tax."
        )
    elif priced.get("calibrationFactor"):
        anchor = "Dollars are anchored to the Bid Summary grand total."
    else:
        anchor = "Dollars are computed from the shade catalogue with published labor and markup."
    return (
        f"{total_shades} window shades for {project}, priced at {_money(priced['total'])} "
        f"({_money(priced['pricePerUnit'])} average per unit). {anchor} "
        "Every line shows its count source, size math, and price formula."
    )


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "—"
    return f"${number:,.2f}"
