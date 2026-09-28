"""
Turn parsed Direct Shades workbooks into a take-off (precision over LLM guesses).

Authority order used everywhere in this module:

  1. WINDOW MATRIX → TOTAL row → TOTAL SHADES  — the project shade count
  2. Blind QTY UNITS rows                       — per-line qty, W×H, system, room
  3. WINDOW MATRIX marking rows                 — fills tags the Blind QTY sheets omit
  4. LLM take-off lines                         — only for tags the workbooks never mention

A workbook row never loses to an LLM row. Conversely, LLM rows are never silently
dropped unless the workbooks already enumerate that tag in more detail.
"""

from __future__ import annotations

from typing import Any, Optional

from domain.direct_shades_workbooks import areas_match


def blind_line_to_takeoff(line: dict[str, Any]) -> dict[str, Any]:
    """Convert one parsed Blind QTY row into a take-off line item."""
    tag = line.get("windowTag") or ""
    qty = line.get("quantity") or 1
    sheet = line.get("sheet") or ""
    row = line.get("row")
    return {
        "windowTag": tag,
        "quantity": qty,
        "width": line.get("width"),
        "height": line.get("height"),
        "widthInches": line.get("widthInches"),
        "heightInches": line.get("heightInches"),
        "dimensionWarning": line.get("dimensionWarning"),
        "floor": line.get("area"),
        "room": line.get("room") or line.get("section"),
        "areaSection": line.get("section"),
        "category": line.get("systemType") or "Window shade",
        "item": line.get("systemType") or "Window shade",
        "productKind": "Shade",
        "motorized": bool(line.get("motorized")),
        "sourceLocation": line.get("sourceLocation")
        or (f"Sheet '{sheet}' — row '{tag}'" if sheet else f"Workbook row '{tag}'"),
        "calculationBasis": (
            f"{qty} EA read from Blind QTY row '{tag}'"
            + (f" on sheet '{sheet}'" if sheet else "")
            + (f" (row {row})" if row else "")
        ),
        "referenceFile": line.get("file") or line.get("referenceFile"),
        "referenceSheet": sheet,
        "referenceRow": row,
        "dataSource": "workbook_excel",
    }


def matrix_row_to_takeoff(row: dict[str, Any], matrix_file: Optional[str] = None) -> dict[str, Any]:
    """Convert one WINDOW MATRIX marking row into a take-off line item."""
    marking = row.get("marking") or ""
    qty = row.get("totalShades") or 1
    section = row.get("section") or ""
    return {
        "windowTag": marking,
        "quantity": qty,
        "areaSection": section,
        "category": "Window shade (matrix marking)",
        "item": f"Shades — marking {marking}",
        "productKind": "Shade",
        "sourceLocation": "WINDOW MATRIX sheet — marking row",
        "calculationBasis": (
            f"{qty} EA from WINDOW MATRIX marking '{marking}'"
            + (f" ({section})" if section else "")
            + " — TOTAL SHADES column"
        ),
        "referenceFile": matrix_file,
        "referenceSheet": "WINDOW MATRIX",
        "dataSource": "window_matrix",
    }


def _matrix_file_name(workbook: dict) -> Optional[str]:
    for f in workbook.get("files") or []:
        if f.get("kind") == "window_matrix":
            return f.get("name")
    return None


def _tag_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _line_key(item: dict[str, Any]) -> str:
    """
    Stable identity for a take-off line.

    Blind QTY sheets legitimately repeat the same window tag in different areas
    (A-101 in LIVINGS and A-101 in BEDROOMS), so the sheet and row must be part of
    the key. Keying on the tag alone silently deleted those lines.
    """
    sheet = str(item.get("referenceSheet") or "").strip().upper()
    row = item.get("referenceRow")
    tag = _tag_key(item.get("windowTag") or item.get("id"))
    if sheet and row is not None:
        return f"{sheet}|{row}|{tag}"
    if sheet:
        return f"{sheet}|{tag}"
    return tag


#: Fields a workbook row overrides on a matching LLM line.
_WORKBOOK_WINS = (
    "quantity",
    "width",
    "height",
    "widthInches",
    "heightInches",
    "sourceLocation",
    "calculationBasis",
    "referenceFile",
    "referenceSheet",
    "referenceRow",
    "dataSource",
    "floor",
    "room",
    "areaSection",
    "category",
    "item",
)


def _blind_covers_marking(marking: str, section: str, lines: list[dict]) -> bool:
    """True when a quantity-sheet row already lists this marking in the same area."""
    for line in lines:
        if _tag_key(line.get("windowTag")) != marking:
            continue
        if not section:
            return True
        if areas_match(section, line.get("section") or "") or areas_match(section, line.get("area") or ""):
            return True
    return False


def apply_workbook_precision(
    workbook: Optional[dict],
    takeoff_items: list[dict],
) -> list[dict]:
    """
    Merge workbook rows with LLM take-off rows; workbook rows win on every fact.

    Returns workbook lines in sheet/row order first, then LLM-only lines.
    """
    wb = workbook or {}
    matrix_file = _matrix_file_name(wb)

    workbook_items: dict[str, dict] = {}
    tags_from_blind: dict[str, list[str]] = {}

    for line in wb.get("blindQtyLines") or []:
        item = blind_line_to_takeoff(line)
        item["referenceFile"] = item.get("referenceFile") or matrix_file
        key = _line_key(item)
        workbook_items[key] = item
        tags_from_blind.setdefault(_tag_key(item["windowTag"]), []).append(key)

    blind_lines = list(wb.get("blindQtyLines") or [])
    for row in wb.get("windowMatrixMarkings") or []:
        marking = _tag_key(row.get("marking"))
        if not marking or marking == "TOTAL":
            continue
        if _blind_covers_marking(marking, row.get("section") or "", blind_lines):
            continue
        item = matrix_row_to_takeoff(row, matrix_file)
        workbook_items[_line_key(item)] = item

    extra_items: list[dict] = []
    for raw in takeoff_items or []:
        item = dict(raw)
        tag = _tag_key(item.get("windowTag") or item.get("id"))
        if not tag:
            continue
        matches = tags_from_blind.get(tag) or []
        if len(matches) == 1:
            base = workbook_items[matches[0]]
            for key in _WORKBOOK_WINS:
                if base.get(key) not in (None, ""):
                    item[key] = base[key]
            # Keep LLM-only enrichment (mount type, motorization, notes).
            workbook_items[matches[0]] = {**base, **item}
        elif len(matches) > 1:
            continue  # workbook enumerates this tag per area; LLM row would double-count
        elif tag in {_tag_key(i.get("windowTag")) for i in workbook_items.values()}:
            continue  # covered by a matrix marking row
        else:
            item.setdefault("dataSource", "ai_takeoff")
            extra_items.append(item)

    merged = list(workbook_items.values()) + extra_items

    if not merged and wb.get("authoritativeTotalShades") is not None:
        merged.append(project_total_line(wb, matrix_file))

    return merged


def project_total_line(workbook: dict, matrix_file: Optional[str] = None) -> dict[str, Any]:
    """Single roll-up line used when the Matrix TOTAL is all we could read."""
    auth = workbook.get("authoritativeTotalShades")
    return {
        "windowTag": "PROJECT TOTAL",
        "quantity": auth,
        "sourceLocation": "WINDOW MATRIX — TOTAL row",
        "referenceSheet": "WINDOW MATRIX",
        "referenceFile": matrix_file or _matrix_file_name(workbook),
        "calculationBasis": (
            f"{auth} EA = WINDOW MATRIX TOTAL row (sum of all marking rows)"
        ),
        "dataSource": "window_matrix_total",
        "category": "Project total",
        "item": "Window shades — all markings",
        "productKind": "Shade",
    }


def workbook_has_structured_takeoff(workbook: Optional[dict]) -> bool:
    """True when Excel parsing yielded enough data to skip the LLM take-off."""
    wb = workbook or {}
    if wb.get("authoritativeTotalShades") is None:
        return False
    if wb.get("blindQtyLines"):
        return True
    markings = wb.get("windowMatrixMarkings") or []
    return len(markings) >= 2


def should_use_workbook_fast_path(
    workbook: Optional[dict],
    *,
    has_pdf: bool = False,
    has_image: bool = False,
) -> bool:
    """
    True when the structured workbooks alone can carry the estimate.

    Drawings change the picture: if a PDF or image came along, vision still runs so
    the workbook count can be cross-checked against the sheets.
    """
    if has_pdf or has_image:
        return False
    return workbook_has_structured_takeoff(workbook)


def workbook_count_reconciliation(workbook: Optional[dict]) -> dict[str, Any]:
    """
    Every count the workbooks give us, so callers never re-derive them.

    Keys: matrixTotal, markingSum, blindQtySum, sectionQtySum, unitQuantities.
    """
    wb = workbook or {}
    marking_sum = sum(
        int(r.get("totalShades") or 0)
        for r in (wb.get("windowMatrixMarkings") or [])
        if r.get("marking")
    )
    blind_sum = sum(int(l.get("quantity") or 0) for l in (wb.get("blindQtyLines") or []))
    section_sum = sum(int(s.get("qtySum") or 0) for s in (wb.get("blindQtySections") or []))
    return {
        "matrixTotal": wb.get("authoritativeTotalShades"),
        "matrixWindows": wb.get("authoritativeTotalWindows"),
        "markingSum": marking_sum or None,
        "blindQtySum": blind_sum or None,
        "sectionQtySum": section_sum or None,
        "levelTotals": wb.get("levelTotals") or {},
    }


def build_takeoff_from_workbook(workbook: dict) -> dict:
    """
    Deterministic take-off from parsed WINDOW MATRIX + Blind QTY sheets.

    Produces the same shape as the LLM take-off so downstream agents, the quantity
    schedule, and the audit builder do not need to know which path ran.
    """
    wb = workbook or {}
    items = apply_workbook_precision(wb, [])
    counts = workbook_count_reconciliation(wb)
    auth = counts["matrixTotal"]
    marking_sum = counts["markingSum"]
    blind_sum = counts["blindQtySum"]
    matrix_file = _matrix_file_name(wb)

    sources: list[str] = []
    if matrix_file:
        sources.append(f"{matrix_file} (WINDOW MATRIX + Blind QTY)")
    for sec in wb.get("blindQtySections") or []:
        sources.append(f"{sec.get('sheet')} — {sec.get('lineCount')} lines")

    notes: list[str] = []
    additional = int(wb.get("additionalShadeCount") or 0)
    extra_areas = [str(a) for a in (wb.get("additionalAreas") or []) if a]
    if auth is not None and marking_sum and marking_sum != auth:
        notes.append(
            f"Matrix marking rows sum to {marking_sum}; the TOTAL row is {auth} EA."
        )
    if auth is not None and additional:
        where = f" ({', '.join(extra_areas)})" if extra_areas else ""
        notes.append(
            f"Matrix TOTAL is {auth} EA for the sections on that sheet. "
            f"{additional} EA are in areas the matrix does not list{where}. "
            "Those are added once."
        )
    elif auth is not None and blind_sum and blind_sum != auth:
        notes.append(
            f"Quantity-sheet lines sum to {blind_sum}; the matrix TOTAL is {auth} EA."
        )

    if wb.get("projectShadeCount") is not None:
        total = int(wb["projectShadeCount"])
    elif auth is not None:
        total = auth + additional
    else:
        total = marking_sum or blind_sum
    if total is None:
        total = sum(int(i.get("quantity") or 0) for i in items)

    return {
        "takeoffItems": items,
        "totalShadeCount": total,
        "countShades": total,
        "countBlinds": 0,
        "countScreens": 0,
        "motorizedCount": sum(int(i.get("quantity") or 0) for i in items if i.get("motorized")),
        "primarySource": "WINDOW MATRIX TOTAL row, plus quantity sheets for areas the matrix does not list",
        "sourcesUsed": "; ".join(sources) or matrix_file or "Structured workbooks",
        "countByType": _count_by(items, "category") or [{"type": "Window shade", "count": total}],
        "countByFloor": _count_by_floor(items),
        "categories": sorted({str(i.get("category") or "Window shade") for i in items}),
        "totalItemCount": len(items),
        "summary": (
            f"{total} window shades"
            + (
                f" ({auth} on the matrix"
                + (f" + {additional} outside it" if additional else "")
                + ")"
                if auth is not None
                else ""
            )
            + (f" across {len(items)} lines" if items else "")
        ),
        "countMethodology": (
            f"Shade total = matrix TOTAL ({auth if auth is not None else 'n/a'}) "
            f"+ areas that are not on the matrix ({additional} EA). "
            "The same window tag in two areas stays two lines. "
            "Opening size uses the finished inch columns, or feet plus leftover inches."
        ),
        "reconciliationNotes": notes,
        "workbookCounts": counts,
        "workbookAuthority": "WINDOW MATRIX TOTAL row",
        "dataSource": "workbook_excel",
    }


def _count_by(items: list[dict], field: str) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "Window shade").strip() or "Window shade"
        totals[key] = totals.get(key, 0) + int(item.get("quantity") or 0)
    return [{"type": k, "count": v} for k, v in sorted(totals.items())]


def _count_by_floor(items: list[dict]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for item in items:
        key = str(item.get("floor") or item.get("areaSection") or "").strip()
        if not key:
            continue
        totals[key] = totals.get(key, 0) + int(item.get("quantity") or 0)
    return [{"floor": k, "count": v} for k, v in sorted(totals.items())]
