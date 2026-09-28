"""Transparent calculation audit: formulas + document references."""

from __future__ import annotations

from typing import Any, Optional


def build_estimation_audit(
    *,
    source_meta: Optional[dict],
    workbook: Optional[dict],
    takeoff: Optional[dict],
    estimation: Optional[dict],
    context: Optional[dict],
    quantity_schedule: Optional[dict],
) -> dict[str, Any]:
    wb = workbook or {}
    takeoff = takeoff or {}
    estimation = estimation or {}
    context = context or {}
    qs = quantity_schedule or {}
    meta = source_meta or {}

    document_references = _document_references(meta, wb, takeoff, context)
    count_block = _count_calculation(wb, takeoff, qs)
    price_block = _price_calculation(estimation, qs, wb)
    line_audits = _line_audits(qs.get("lines") or [])

    return {
        "documentReferences": document_references,
        "countCalculation": count_block,
        "priceCalculation": price_block,
        "lineCalculations": line_audits,
        "referencePricing": wb.get("referencePricing") or {},
        "summary": _audit_summary(count_block, price_block, wb),
    }


def _document_references(
    meta: dict,
    wb: dict,
    takeoff: dict,
    context: dict,
) -> list[dict[str, Any]]:
    # An audit that overstates what read the file is worse than no audit, so say which
    # route actually ran: the workbook path never sends the file to a model.
    if (takeoff.get("dataSource") or "") == "workbook_excel":
        upload_used_for = "Text extraction, workbook parsing (no model read this file)"
    else:
        upload_used_for = "Text extraction, workbook parsing, AI take-off"

    refs: list[dict[str, Any]] = []
    for name in meta.get("fileNames") or ([meta.get("fileName")] if meta.get("fileName") else []):
        if name:
            refs.append(
                {
                    "role": "Uploaded file",
                    "location": name,
                    "usedFor": upload_used_for,
                }
            )

    for f in wb.get("files") or []:
        kind = (f.get("kind") or "workbook").replace("_", " ")
        sheets = ", ".join(f.get("sheets") or [])[:200]
        refs.append(
            {
                "role": kind.title(),
                "location": f"{f.get('name')} — sheets: {sheets}" if sheets else f.get("name"),
                "usedFor": _kind_used_for(f.get("kind")),
            }
        )

    auth = wb.get("authoritativeTotalShades")
    if auth is not None:
        matrix_name = next(
            (f.get("name") for f in (wb.get("files") or []) if f.get("kind") == "window_matrix"),
            "Window Matrix workbook",
        )
        refs.append(
            {
                "role": "Count authority",
                "location": f"{matrix_name} → WINDOW MATRIX sheet → TOTAL row → TOTAL SHADES",
                "usedFor": f"Project shade count = {auth} EA (master total for estimate)",
            }
        )

    blind_lines = wb.get("blindQtyLines") or []
    if blind_lines:
        sheets = sorted({str(l.get("sheet") or "") for l in blind_lines if l.get("sheet")})
        matrix_name = next(
            (f.get("name") for f in (wb.get("files") or []) if f.get("kind") == "window_matrix"),
            "Window Matrix workbook",
        )
        refs.append(
            {
                "role": "Blind QTY take-off",
                "location": f"{matrix_name} → {', '.join(sheets[:6])}"
                + ("…" if len(sheets) > 6 else ""),
                "usedFor": f"{len(blind_lines)} parsed rows — tag, QTY, W×H, system, room",
            }
        )

    if takeoff.get("primarySource"):
        refs.append(
            {
                "role": "Primary take-off source",
                "location": str(takeoff["primarySource"]),
                "usedFor": "Line quantities and tags",
            }
        )
    if context.get("windowScheduleLocation"):
        refs.append(
            {
                "role": "Window schedule (context)",
                "location": str(context["windowScheduleLocation"]),
                "usedFor": "Reading guide / schedule location",
            }
        )

    measure = wb.get("measurementReference") or {}
    if measure.get("note"):
        refs.append(
            {
                "role": "Measurement reference",
                "location": _measurement_location(measure),
                "usedFor": measure["note"],
            }
        )
    return refs


def _measurement_location(measure: dict) -> str:
    parts: list[str] = []
    for point in measure.get("points") or []:
        width = ((point.get("width") or {}).get("used")) or ""
        height = ((point.get("height") or {}).get("used")) or ""
        columns = " × ".join(name for name in (width, height) if name)
        sheet = point.get("sheet") or "quantity sheet"
        if columns:
            parts.append(f"{sheet}: {columns}")
    if measure.get("bidOffset"):
        parts.append("bid width and height compared with these openings")
    return "; ".join(parts)[:500] or "Quantity-sheet width and height columns"


def _kind_used_for(kind: Optional[str]) -> str:
    if kind == "window_matrix":
        return "Shade counts by marking; TOTAL row; Blind QTY unit dimensions"
    if kind == "material_summary":
        return "Fabrics, systems, material lines"
    if kind == "bid_summary":
        return "Pricing cross-check (bid tabs)"
    return "Supporting project data"


def _count_calculation(wb: dict, takeoff: dict, qs: dict) -> dict[str, Any]:
    auth = wb.get("authoritativeTotalShades")
    totals = qs.get("totals") or {}
    line_sum = totals.get("unitQuantity")
    takeoff_total = takeoff.get("totalShadeCount")

    steps: list[dict[str, str]] = []

    additional = wb.get("additionalShadeCount") or 0
    project = wb.get("projectShadeCount")
    if auth is not None:
        steps.append(
            {
                "label": "Matrix total shades",
                "value": str(auth),
                "formula": "WINDOW MATRIX sheet — TOTAL row — TOTAL SHADES column",
                "reference": _matrix_ref(wb),
            }
        )
        if additional:
            areas = ", ".join(wb.get("additionalAreas") or []) or "areas not listed on the matrix"
            steps.append(
                {
                    "label": "Shades outside the matrix",
                    "value": str(additional),
                    "formula": "Quantity-sheet rows whose area is not a section on the matrix",
                    "reference": areas,
                }
            )
        if project is not None:
            steps.append(
                {
                    "label": "Project total shades",
                    "value": str(project),
                    "formula": (
                        f"Matrix TOTAL {auth} + {additional} from areas the matrix does not list"
                        if additional
                        else "Matrix TOTAL is the project total"
                    ),
                    "reference": _matrix_ref(wb),
                }
            )
        steps.append(
            {
                "label": "Take-off total applied",
                "value": str(takeoff_total or auth),
                "formula": "Matrix TOTAL plus shades in areas the matrix does not list",
                "reference": takeoff.get("workbookAuthority") or "WINDOW MATRIX TOTAL row",
            }
        )
    else:
        steps.append(
            {
                "label": "Take-off total",
                "value": str(takeoff_total or line_sum or "?"),
                "formula": "Sum of take-off line quantities (EA)",
                "reference": takeoff.get("primarySource") or "Schedule / RAG sections",
            }
        )

    if line_sum is not None:
        steps.append(
            {
                "label": "Sum of schedule lines",
                "value": str(line_sum),
                "formula": "Σ (quantity) for each row in shade list",
                "reference": "Quantity schedule lines below",
            }
        )

    counts = takeoff.get("workbookCounts") or {}
    if counts.get("markingSum"):
        steps.append(
            {
                "label": "Matrix markings check",
                "value": str(counts["markingSum"]),
                "formula": "Σ TOTAL SHADES across every marking row (excludes TOTAL row)",
                "reference": _matrix_ref(wb),
            }
        )
    if counts.get("blindQtySum"):
        sheet_count = len(wb.get("blindQtySections") or []) or 1
        steps.append(
            {
                "label": "Blind QTY rows check",
                "value": str(counts["blindQtySum"]),
                "formula": f"Σ QTY column across {sheet_count} Blind QTY sheet(s)",
                "reference": "Blind QTY UNITS sheets — see source column per line",
            }
        )

    if takeoff.get("countMethodology"):
        steps.append(
            {
                "label": "Method notes",
                "value": "See narrative",
                "formula": takeoff["countMethodology"],
                "reference": "Take-off agent",
            }
        )

    notes = list(takeoff.get("reconciliationNotes") or [])
    if totals.get("matchesMatrix") is False and auth is not None:
        notes.append(
            f"Schedule line sum ({line_sum or '?'}) differs from Matrix TOTAL ({auth}); "
            "take-off total follows Matrix authority."
        )

    return {
        "steps": steps,
        "matchesMatrix": totals.get("matchesMatrix"),
        "matrixMarkingSum": totals.get("matrixMarkingSum"),
        "reconciliationNotes": notes,
    }


def _price_calculation(estimation: dict, qs: dict, wb: Optional[dict] = None) -> dict[str, Any]:
    """
    Price steps, newest-first in terms of trust.

    When pricing was computed deterministically the engine already produced exact
    steps (catalogue basis → bid calibration → total); those are used verbatim so the
    audit shows the same arithmetic the estimate used rather than a paraphrase.
    """
    totals = qs.get("totals") or {}
    wb = wb or {}
    reference = wb.get("referencePricing") or {}

    sub = estimation.get("subtotal") or totals.get("subtotal")
    oh = estimation.get("overhead")
    prof = estimation.get("profit")
    grand = estimation.get("totalEstimate") or totals.get("grandTotal")

    deterministic_steps = estimation.get("pricingSteps") or []
    if deterministic_steps:
        steps = [dict(s) for s in deterministic_steps]
        steps.extend(_reference_price_steps(reference, grand))
        return {
            "steps": steps,
            "currency": estimation.get("currency") or "USD",
            "priceSource": estimation.get("priceSource"),
            "policy": estimation.get("pricingPolicy"),
        }

    steps: list[dict[str, str]] = []
    steps.append(
        {
            "label": "Line items",
            "value": fmt_money(sub) if sub else "—",
            "formula": "Σ (extended price per shade line) from estimation agent",
            "reference": "Catalogue + size/type tiers; see line price calc column",
        }
    )
    if oh is not None:
        steps.append(
            {
                "label": "Overhead",
                "value": fmt_money(oh),
                "formula": "Applied to subtotal per estimation rules",
                "reference": "Estimation agent assumptions",
            }
        )
    if prof is not None:
        steps.append(
            {
                "label": "Profit",
                "value": fmt_money(prof),
                "formula": "Applied after overhead",
                "reference": "Estimation agent assumptions",
            }
        )
    steps.append(
        {
            "label": "Grand total",
            "value": fmt_money(grand) if grand else "—",
            "formula": "Subtotal + overhead + profit (USD unless noted)",
            "reference": "Client offer total",
        }
    )

    offer = estimation.get("clientOffer") or {}
    if offer.get("pricePerShade") and offer.get("totalShades"):
        steps.append(
            {
                "label": "Average per shade",
                "value": fmt_money(offer["pricePerShade"]),
                "formula": f"Total price ÷ {offer['totalShades']} shades",
                "reference": "Client offer package",
            }
        )

    steps.extend(_reference_price_steps(reference, grand))

    return {
        "steps": steps,
        "currency": estimation.get("currency") or "USD",
        "priceSource": estimation.get("priceSource"),
        "policy": estimation.get("pricingPolicy"),
    }


def _reference_price_steps(reference: dict, grand: Any) -> list[dict[str, str]]:
    """Cross-check rows comparing our total against the Bid Summary workbook."""
    if not reference:
        return []

    steps: list[dict[str, str]] = []
    ref_total = reference.get("grandTotal")
    if ref_total is not None:
        steps.append(
            {
                "label": "Bid Summary cross-check",
                "value": fmt_money(ref_total),
                "formula": reference.get("grandTotalBasis") or "Σ Bid Summary tab totals",
                "reference": f"{reference.get('source')} — bid tabs",
            }
        )
        try:
            delta = float(grand) - float(ref_total)
            steps.append(
                {
                    "label": "Variance vs bid",
                    "value": fmt_money(delta),
                    "formula": f"{fmt_money(grand)} estimate − {fmt_money(ref_total)} bid",
                    "reference": "Zero means the estimate matches the bid workbook exactly",
                }
            )
        except (TypeError, ValueError):
            pass

    if reference.get("unitRate"):
        steps.append(
            {
                "label": "Bid unit rate",
                "value": fmt_money(reference["unitRate"]),
                "formula": reference.get("unitRateBasis") or "Bid total ÷ shade count",
                "reference": f"{reference.get('source')} — reference only, not the count",
            }
        )
    return steps


def _line_audits(lines: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        w = line.get("widthInches")
        h = line.get("heightInches")
        sq = line.get("squareFeetEach")
        dim_formula = ""
        if w and h and sq:
            dim_formula = f"({w} in × {h} in) ÷ 144 = {sq} sq ft per shade"
        elif line.get("width") and line.get("height"):
            dim_formula = f"Size {line.get('width')} × {line.get('height')} (parse for sq ft)"

        out.append(
            {
                "lineNumber": line.get("lineNumber"),
                "windowTag": line.get("windowTag"),
                "location": ", ".join(
                    x for x in [line.get("floor"), line.get("room")] if x
                ),
                "countFormula": line.get("countBasis") or f"{line.get('quantity')} EA",
                "dimensionFormula": dim_formula or "Dimensions not in source — field verify",
                "priceFormula": line.get("pricingFormula") or "",
                # A SKU the catalogue could not match exactly is a review item, so it
                # travels with the line instead of only living in the pricing engine.
                "productBasis": (
                    line.get("catalogueReason") or ""
                    if line.get("catalogueMatch") == "substituted"
                    else ""
                ),
                "reference": line.get("sourceLocation") or "",
                "referenceDetail": line.get("references") or [],
                "quantity": line.get("quantity"),
                "extendedPrice": line.get("extendedPrice"),
            }
        )
    return out


def _matrix_ref(wb: dict) -> str:
    name = next(
        (f.get("name") for f in (wb.get("files") or []) if f.get("kind") == "window_matrix"),
        "Window Matrix file",
    )
    return f"{name} / WINDOW MATRIX / TOTAL row"


_PRICE_SOURCE_LABELS = {
    "bid_summary": "Dollars are anchored to the Bid Summary grand total.",
    "catalogue": "Dollars are computed from the shade catalogue with published labor and markup.",
}


def _audit_summary(count: dict, price: dict, wb: dict) -> str:
    auth = wb.get("authoritativeTotalShades")
    price_note = _PRICE_SOURCE_LABELS.get(
        str(price.get("priceSource") or ""),
        "Each line shows the price math used.",
    )
    if auth is not None:
        return (
            f"Count is anchored to WINDOW MATRIX TOTAL ({auth} shades). {price_note} "
            "Each line shows where qty, size, and price come from."
        )
    return (
        f"No Window Matrix TOTAL row was found, so the count comes from the take-off. "
        f"{price_note} Each line shows count basis, dimension math, price math, and source."
    )


def fmt_money(val: Any) -> str:
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return str(val)
