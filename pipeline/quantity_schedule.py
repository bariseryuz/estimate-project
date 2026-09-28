"""
Contractor-grade quantity schedule: locations, dimensions, count logic, pricing math.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.dimensions import enrich_takeoff_item, format_dim_inches, parse_length_inches, square_feet


def build_quantity_schedule(
    takeoff: Optional[dict],
    estimation: Optional[dict],
    workbook: Optional[dict],
    context: Optional[dict],
) -> dict[str, Any]:
    takeoff = takeoff or {}
    estimation = estimation or {}
    workbook = workbook or {}
    context = context or {}

    estimates = estimation.get("estimates") or []
    wb_dim_index = _workbook_dimension_index(workbook)
    items = list(takeoff.get("takeoffItems") or [])
    if not items:
        items = _items_from_workbook_lines(workbook)

    # Window tags repeat across Blind QTY sheets, so a tag lookup would join the wrong
    # price to the wrong line. The estimation agent emits one estimate per take-off
    # item in order, so join positionally whenever the counts line up.
    positional = len(estimates) == len(items) and bool(items)
    est_by_tag: dict[str, dict] = {}
    if not positional:
        for e in estimates:
            tag = (e.get("windowTag") or "").strip().upper()
            if tag:
                est_by_tag.setdefault(tag, e)

    lines: list[dict[str, Any]] = []
    total_sqft = 0.0
    for i, raw in enumerate(items):
        item = dict(raw)
        enrich_takeoff_item(item)
        tag = (item.get("windowTag") or item.get("id") or f"LINE-{i + 1}").strip()
        tag_key = tag.upper()

        wb_row = wb_dim_index.get(tag_key) or {}
        if wb_row.get("width") and not item.get("width"):
            item["width"] = wb_row["width"]
        if wb_row.get("height") and not item.get("height"):
            item["height"] = wb_row["height"]
        if wb_row.get("area") and not item.get("floor"):
            item["floor"] = wb_row.get("area")
        enrich_takeoff_item(item)

        est = (estimates[i] if positional else est_by_tag.get(tag_key)) or {}
        qty = int(item.get("quantity") or est.get("quantity") or 1)
        sq = item.get("squareFeet")
        if sq:
            total_sqft += float(sq) * qty

        unit_total = est.get("totalCost")
        material = est.get("materialCost")
        labor = est.get("laborCost")
        unit_cost = est.get("unitCost")
        pricing_formula = est.get("calculationFormula") or _pricing_formula(
            qty, unit_cost, material, labor, unit_total
        )
        dim_formula = _dimension_formula(item.get("widthInches"), item.get("heightInches"), item.get("squareFeet"))
        references = _line_references(item, wb_row, workbook)

        lines.append(
            {
                "lineNumber": i + 1,
                "windowTag": tag,
                "floor": item.get("floor") or _floor_from_location(item),
                "room": item.get("room") or item.get("location") or wb_row.get("room") or "",
                "areaSection": item.get("areaSection") or wb_row.get("section") or "",
                "width": item.get("width") or format_dim_inches(item.get("widthInches")),
                "height": item.get("height") or format_dim_inches(item.get("heightInches")),
                "widthInches": item.get("widthInches"),
                "heightInches": item.get("heightInches"),
                "squareFeetEach": item.get("squareFeet"),
                "squareFeetTotal": round((item.get("squareFeet") or 0) * qty, 2)
                if item.get("squareFeet")
                else None,
                "productKind": item.get("productKind") or item.get("category"),
                "systemType": item.get("category") or item.get("item"),
                "mountType": item.get("mountType"),
                "motorized": item.get("motorized"),
                "quantity": qty,
                "unit": item.get("unit") or "EA",
                "sourceLocation": item.get("sourceLocation") or wb_row.get("sourceLocation") or "",
                "countBasis": item.get("calculationBasis") or "",
                "unitMaterial": material,
                "unitLabor": labor,
                "unitPrice": unit_cost,
                "extendedPrice": unit_total,
                "pricingFormula": pricing_formula or "",
                "priceSource": est.get("priceSource") or (None if unit_total is None else "estimation_agent"),
                "catalogueSku": est.get("catalogueSku"),
                "catalogueMatch": est.get("catalogueMatch"),
                "catalogueReason": est.get("catalogueReason") or "",
                "countFormula": item.get("calculationBasis") or f"{qty} EA",
                "dimensionFormula": dim_formula,
                "references": references,
                "dataSource": item.get("dataSource") or ("workbook_excel" if wb_row else "ai_takeoff"),
                "referenceFile": item.get("referenceFile") or references[0].get("file") if references else None,
                "referenceSheet": item.get("referenceSheet") or references[0].get("sheet") if references else None,
                "notes": item.get("notes") or "",
                "dimensionWarning": item.get("dimensionWarning") or "",
            }
        )

    methodology = _methodology(takeoff, workbook, context, estimation, lines)

    matrix_total = workbook.get("authoritativeTotalShades")
    project_total = workbook.get("projectShadeCount")
    if project_total is None:
        project_total = matrix_total
    line_count_sum = sum(l["quantity"] for l in lines)
    marking_sum = sum(
        int(r.get("totalShades") or 0)
        for r in (workbook.get("windowMatrixMarkings") or [])
        if r.get("marking")
    )
    takeoff_total = takeoff.get("totalShadeCount")
    matches_matrix = project_total is None
    if project_total is not None:
        matches_matrix = any(
            n == project_total
            for n in (line_count_sum, takeoff_total, marking_sum if not workbook.get("additionalShadeCount") else None)
            if n is not None
        )

    return {
        "methodology": methodology,
        "lines": lines,
        "totals": {
            "lineCount": len(lines),
            "unitQuantity": line_count_sum,
            "matrixAuthority": matrix_total,
            "matrixMarkingSum": marking_sum or None,
            "takeoffTotal": takeoff_total,
            "matchesMatrix": matches_matrix,
            "totalSquareFeet": round(total_sqft, 2) if total_sqft else None,
            "subtotal": estimation.get("subtotal"),
            "overhead": estimation.get("overhead"),
            "profit": estimation.get("profit"),
            "grandTotal": estimation.get("totalEstimate"),
            "currency": estimation.get("currency") or "USD",
            "priceSource": estimation.get("priceSource"),
            "referenceGrandTotal": (workbook.get("referencePricing") or {}).get("grandTotal"),
            "pricedLineCount": sum(1 for l in lines if l.get("extendedPrice") is not None),
        },
        "primarySource": takeoff.get("primarySource") or context.get("windowScheduleLocation"),
        "sourcesUsed": takeoff.get("sourcesUsed") or "",
    }


def _workbook_dimension_index(workbook: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for row in workbook.get("blindQtyLines") or []:
        tag = (row.get("windowTag") or "").strip().upper()
        if not tag:
            continue
        index[tag] = row
    for row in workbook.get("windowMatrixMarkings") or []:
        tag = (row.get("marking") or "").strip().upper()
        if not tag:
            continue
        entry = index.setdefault(tag, {})
        entry.setdefault("section", row.get("section"))
        entry["matrixTotalShades"] = row.get("totalShades")
        entry.setdefault("sourceLocation", "WINDOW MATRIX sheet")
    return index


def _items_from_workbook_lines(workbook: dict) -> list[dict]:
    """
    Build schedule lines straight from the workbook when no take-off reached us.

    Iterates the parsed rows rather than a tag-keyed index so repeated tags in
    different areas stay as separate lines.
    """
    items: list[dict] = []
    for row in workbook.get("blindQtyLines") or []:
        items.append(
            {
                "windowTag": row.get("windowTag"),
                "quantity": row.get("quantity") or 1,
                "width": row.get("width"),
                "height": row.get("height"),
                "widthInches": row.get("widthInches"),
                "heightInches": row.get("heightInches"),
                "location": row.get("room") or row.get("area"),
                "floor": row.get("area"),
                "room": row.get("room"),
                "areaSection": row.get("section"),
                "sourceLocation": row.get("sourceLocation"),
                "referenceFile": row.get("file"),
                "referenceSheet": row.get("sheet"),
                "category": row.get("systemType") or "Window shade",
                "productKind": "Shade",
                "dataSource": "workbook_excel",
            }
        )
    if not items:
        for row in workbook.get("windowMatrixMarkings") or []:
            marking = row.get("marking")
            if not marking:
                continue
            items.append(
                {
                    "windowTag": marking,
                    "quantity": row.get("totalShades") or 1,
                    "areaSection": row.get("section"),
                    "sourceLocation": "WINDOW MATRIX sheet — marking row",
                    "referenceSheet": "WINDOW MATRIX",
                    "category": "Window shade (matrix marking)",
                    "productKind": "Shade",
                    "dataSource": "window_matrix",
                }
            )
    if not items and workbook.get("authoritativeTotalShades") is not None:
        items.append(
            {
                "windowTag": "PROJECT TOTAL",
                "quantity": workbook["authoritativeTotalShades"],
                "sourceLocation": "WINDOW MATRIX — TOTAL row",
                "calculationBasis": (
                    f"Authoritative count {workbook['authoritativeTotalShades']} shades "
                    "from WINDOW MATRIX TOTAL row (all markings summed)."
                ),
                "category": "All markings",
                "productKind": "Shade",
            }
        )
    return items


def _floor_from_location(item: dict) -> str:
    loc = (item.get("location") or "").strip()
    if not loc:
        return ""
    for part in loc.split(","):
        p = part.strip()
        if p.upper().startswith("LEVEL") or p.upper().startswith("FL"):
            return p
    return ""


def _dimension_formula(
    width_in: Optional[float],
    height_in: Optional[float],
    sq: Optional[float],
) -> str:
    if width_in and height_in and sq:
        return f"({width_in} in × {height_in} in) ÷ 144 = {sq} sq ft / shade"
    return ""


def _line_references(
    item: dict,
    wb_row: dict,
    workbook: dict,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    src = item.get("sourceLocation") or wb_row.get("sourceLocation") or ""
    sheet = item.get("referenceSheet") or wb_row.get("sheet") or ""
    file_name = item.get("referenceFile") or _matrix_file(workbook) or ""
    if file_name:
        refs.append({"type": "file", "file": file_name, "sheet": sheet, "detail": src or "Parsed Excel"})
    elif src:
        refs.append({"type": "document", "file": "", "sheet": sheet, "detail": src})
    if item.get("dataSource") == "window_matrix_total":
        refs.insert(
            0,
            {
                "type": "authority",
                "file": file_name or "Window Matrix",
                "sheet": "WINDOW MATRIX",
                "detail": "TOTAL row — TOTAL SHADES column",
            },
        )
    return refs


def _matrix_file(workbook: dict) -> Optional[str]:
    for f in workbook.get("files") or []:
        if f.get("kind") == "window_matrix":
            return f.get("name")
    return None


def _pricing_formula(
    qty: int,
    unit_cost: Any,
    material: Any,
    labor: Any,
    total: Any,
) -> str:
    parts: list[str] = []
    if qty and unit_cost is not None:
        parts.append(f"{qty} EA × ${float(unit_cost):,.2f} unit")
    if material is not None and labor is not None:
        parts.append(f"(${float(material):,.2f} material + ${float(labor):,.2f} labor) per EA")
    if total is not None:
        parts.append(f"= ${float(total):,.2f} extended")
    return "; ".join(parts)


def _pricing_rule(estimation: dict, workbook: dict) -> str:
    """One sentence naming where the dollars came from."""
    source = estimation.get("priceSource")
    reference = workbook.get("referencePricing") or {}
    if source == "bid_summary" and reference.get("grandTotal"):
        return (
            f"Pricing: project total anchored to the Bid Summary grand total "
            f"(${float(reference['grandTotal']):,.2f} from {reference.get('source')}); "
            "line prices are catalogue math calibrated to that total."
        )
    if source == "catalogue":
        return (
            "Pricing: catalogue list price × size factor + install labor per line, "
            "then overhead and profit — every factor is shown in the line formula."
        )
    return (
        "Pricing: unit material + labor per line where available; "
        "project subtotal + overhead + profit from estimation agent (see Estimate tab)."
    )


def _methodology(
    takeoff: dict,
    workbook: dict,
    context: dict,
    estimation: dict,
    lines: list[dict],
) -> dict[str, Any]:
    rules: list[str] = []
    matrix = workbook.get("authoritativeTotalShades")
    extra = workbook.get("additionalShadeCount") or 0
    project_count = workbook.get("projectShadeCount")
    if matrix is not None and extra:
        areas = ", ".join(workbook.get("additionalAreas") or []) or "areas not listed on the matrix"
        rules.append(
            f"Shade count authority: the matrix TOTAL row is {matrix} for the sections on that sheet. "
            f"{extra} more shades are in {areas}. Project total is {project_count}."
        )
    elif matrix is not None:
        rules.append(
            f"Shade count authority: WINDOW MATRIX TOTAL row = {matrix} units. "
            "Individual markings on the matrix must sum to this total."
        )
    else:
        rules.append(
            "Shade count: sum of take-off lines (1 EA per scheduled opening unless notes specify otherwise)."
        )

    measurement = (workbook.get("measurementReference") or {}).get("note")
    if measurement:
        rules.append(measurement)
    else:
        rules.append(
            "Dimensions: width × height taken from the quantity sheets (the columns headed width and height), "
            "square feet = (width in × height in) ÷ 144 per shade."
        )
    if takeoff.get("reconciliationNotes"):
        rules.extend(takeoff["reconciliationNotes"])
    rules.append(_pricing_rule(estimation, workbook))

    return {
        "projectName": workbook.get("projectName") or context.get("projectName"),
        "countRule": rules[0],
        "dimensionRule": rules[1],
        "measurementReference": workbook.get("measurementReference") or {},
        "pricingRule": rules[-1],
        "primarySource": takeoff.get("primarySource") or context.get("windowScheduleLocation"),
        "sourcesUsed": takeoff.get("sourcesUsed") or "",
        "readingGuide": (context.get("readingGuide") or "")[:600],
        "takeoffSummary": takeoff.get("summary") or "",
        "reconciliationNotes": takeoff.get("reconciliationNotes") or [],
        "rules": rules,
        "linesWithDimensions": sum(1 for l in lines if l.get("squareFeetEach")),
        "linesTotal": len(lines),
    }
