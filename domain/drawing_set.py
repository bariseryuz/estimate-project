"""
Read an architectural drawing set (many sheet PDFs, no Excel matrix).

The sheets that decide a shade count are the unit matrix (how many of each
unit) and the unit plans (how many glazed openings in that unit get a shade).
Floor plans, notes, RCP, and elevations are indexed so the reader can say
what it opened, and they are not sent to the model page by page.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Optional

_UNIT_LINE = re.compile(
    r"UNIT\s+(\d{2,4})\s*\n\s*([A-Z][A-Z0-9\-]{0,8})\s*\n\s*([\d,]+)\s*SF",
    re.IGNORECASE,
)
_SHEET_NUMBER = re.compile(r"(?:^|[^A-Z])A-?\d", re.IGNORECASE)
_FLOOR_SKIP = (
    "rcp",
    "finish",
    "furniture",
    "elev",
    "section",
    "roof",
    "site",
    "detail",
    "note",
    "stair",
    "door",
    "window",
    "storefront",
    "cladding",
    "zoning",
    "cover",
)


def prepare_drawing_set(
    uploads: list[tuple[str, bytes, str]],
    texts: dict[str, str],
) -> Optional[dict[str, Any]]:
    """
    Return selected sheet images plus a short reading brief, or None when this
    upload is not an architectural set.
    """
    pdfs = [(name, data) for name, data, ext in uploads if (ext or "").lower() == ".pdf" or name.lower().endswith(".pdf")]
    if len(pdfs) < 3:
        return None
    sheetish = sum(1 for name, _ in pdfs if _SHEET_NUMBER.search(name))
    if sheetish < 3:
        return None

    classified: list[dict[str, Any]] = []
    for name, data in pdfs:
        role = classify_sheet(name, texts.get(name) or "")
        classified.append({"file": name, "role": role, "text": texts.get(name) or "", "data": data})

    roles = {row["role"] for row in classified}
    if "unit_plan" not in roles and "unit_matrix" not in roles and "window_schedule" not in roles:
        return None

    units = _units_from_floor_plans(classified)
    project = _project_name(classified)
    from domain.plan_openings import count_plan_openings

    sheets = []
    for row in classified:
        if row["role"] == "unit_plan":
            openings = count_plan_openings(row["data"])
            if openings:
                sheets.append(
                    {
                        "file": row["file"],
                        "role": "unit_plan",
                        "part": "measured",
                        "openings": openings,
                    }
                )
                continue
        for part, jpeg in _images_for_role(row["role"], row["data"]):
            sheets.append(
                {
                    "file": row["file"],
                    "role": row["role"],
                    "part": part,
                    "mediaType": "image/jpeg",
                    "imageBase64": base64.b64encode(jpeg).decode("ascii"),
                }
            )

    by_role: dict[str, int] = {}
    for row in classified:
        by_role[row["role"]] = by_role.get(row["role"], 0) + 1

    type_counts: dict[str, int] = {}
    for unit in units:
        type_counts[unit["type"]] = type_counts.get(unit["type"], 0) + 1

    brief_lines = [
        f"Architectural drawing set. Project: {project or 'not printed in the title block'}.",
        f"{len(pdfs)} sheets. Unit matrix sheets: {by_role.get('unit_matrix', 0)}. "
        f"Unit plans: {by_role.get('unit_plan', 0)}. "
        f"Window schedules: {by_role.get('window_schedule', 0)}. "
        f"Floor plans: {by_role.get('floor_plan', 0)}.",
        f"Floor-plan labels list {len(units)} distinct unit numbers. "
        "A typical-floor sheet may draw one floor that repeats, so this label count "
        "is not the unit-matrix total.",
    ]
    if type_counts:
        shown = ", ".join(f"{code} × {count}" for code, count in sorted(type_counts.items())[:24])
        brief_lines.append(f"Unit types labeled on the floor plans: {shown}.")
    brief_lines.append(
        "Shade count is the unit-matrix count of each unit times the window tags drawn on that unit's floor plan. "
        "Square door tags, sliding doors, and exterior sunshades are not shades. "
        "A bathroom window is a window and not a shade."
    )

    return {
        "projectName": project,
        "sheetCount": len(pdfs),
        "roles": by_role,
        "floorPlanUnits": len(units),
        "brief": "\n".join(brief_lines),
        "sheets": sheets,
    }


def classify_sheet(name: str, text: str) -> str:
    blob = f"{name}\n{(text or '')[:5000]}".lower()
    file_name = name.lower()
    if "unit matrix" in blob or "unit-matrix" in file_name:
        return "unit_matrix"
    if "window schedule" in blob or ("window" in file_name and "schedule" in file_name):
        return "window_schedule"
    if re.search(r"unit[-_ ]+[a-z0-9]", file_name) and "matrix" not in file_name:
        return "unit_plan"
    if "floor" in file_name and not any(skip in file_name for skip in _FLOOR_SKIP):
        return "floor_plan"
    return "other"


def norm_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def combine_counts(
    matrix_types: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Multiply each matrix unit count by the shade openings on that unit's plan.

    Several sheets can describe one type (A-1a, A-1b). They are alternates, not
    extras, so one opening count is used per type.
    """
    plans_by_type: dict[str, list[dict[str, Any]]] = {}
    for row in plan_rows:
        key = norm_type(str(row.get("unitType") or ""))
        if not key:
            key = norm_type(str(row.get("file") or ""))
        if key:
            plans_by_type.setdefault(key, []).append(row)

    lines = []
    unmatched = []
    total = 0
    for entry in matrix_types:
        code = str(entry.get("type") or "").strip()
        try:
            count = int(entry.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if not code or count <= 0:
            continue
        key = norm_type(code)
        plans = plans_by_type.get(key) or []
        if not plans:
            plans = [
                row
                for row in plan_rows
                if key and norm_type(type_from_filename(str(row.get("file") or ""))) == key
            ]
        if not plans:
            unmatched.append(code)
            continue
        openings = _opening_count(plans)
        shades = count * openings
        total += shades
        source = plans[0].get("file") or "unit plan"
        lines.append(
            {
                "windowTag": code,
                "quantity": shades,
                "unit": "EA",
                "floor": "",
                "room": code,
                "areaSection": "Residential units",
                "category": "Window shade",
                "item": f"Shades for unit {code}",
                "productKind": "Shade",
                "sourceLocation": f"{source} × {count} units on the unit matrix",
                "calculationBasis": (
                    f"{count} units of {code} on the unit matrix × {openings} shade opening"
                    f"{'s' if openings != 1 else ''} on {source}"
                ),
                "dataSource": "drawing_set",
                "notes": plans[0].get("notes") or "",
            }
        )
    return {"lines": lines, "total": total, "unmatched": unmatched}


_UNIT_NUMBER = re.compile(r"^(?:LW-)?\d{2,4}$", re.IGNORECASE)
_NOT_A_TYPE = re.compile(r"BATH|BED|DEN|\+|TOTAL|SQ\.?FT|UNITS", re.IGNORECASE)


def _is_unit_type(value: str) -> bool:
    """A unit type is a plan code such as A-1B, not a bedroom mix such as 2BR+2BATH."""
    text = value.strip()
    if not text or _NOT_A_TYPE.search(text):
        return False
    return bool(re.match(r"^[A-Z]{1,3}-?\d", text, re.IGNORECASE))


_UNIT_IN_NAME = re.compile(
    r"UNIT[-_ ]+([A-Z0-9]+(?:[-_ ]+[A-Z0-9]+)*)",
    re.IGNORECASE,
)


def type_from_filename(name: str) -> str:
    """Unit code printed in a sheet name, such as A-1-a or B-1-S."""
    match = _UNIT_IN_NAME.search(name or "")
    if not match:
        return ""
    token = re.split(r"[-_ ]Rev", match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
    return token.strip("-_ ")


def merge_unit_rows(tile_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """
    One unit number is one apartment, even when overlapping tiles both show it.

    printedTotal is the largest TOTAL UNITS figure on the sheet, which is the
    grand total rather than a single floor's subtotal.
    """
    readings: dict[str, list[tuple[str, int]]] = {}
    printed: list[int] = []
    for payload in tile_payloads:
        for row in payload.get("units") or []:
            if not isinstance(row, dict):
                continue
            number = str(row.get("unit") or "").strip().upper()
            unit_type = str(row.get("type") or "").strip()
            description = str(row.get("description") or "")
            if not _UNIT_NUMBER.match(number):
                continue
            if not unit_type or not _is_unit_type(unit_type):
                continue
            if "NOT USED" in unit_type.upper() or "NOT USED" in description.upper():
                continue
            try:
                qty = int(row.get("qty") or 1)
            except (TypeError, ValueError):
                qty = 1
            if qty <= 0:
                continue
            readings.setdefault(number, []).append((unit_type, qty))
        for value in payload.get("printedTotals") or []:
            try:
                total = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < total < 5000:
                printed.append(total)
    units = {}
    for number, options in readings.items():
        type_names = [name for name, _qty in options]
        chosen = max(set(type_names), key=type_names.count)
        qty = next(item_qty for name, item_qty in options if name == chosen)
        units[number] = {"unit": number, "type": chosen, "qty": qty}
    grouped: dict[str, dict[str, Any]] = {}
    for unit in units.values():
        key = norm_type(unit["type"])
        bucket = grouped.setdefault(key, {"type": unit["type"], "count": 0})
        bucket["count"] += unit["qty"]
    return {
        "units": list(units.values()),
        "types": list(grouped.values()),
        "transcribedUnits": sum(unit["qty"] for unit in units.values()),
        "printedTotal": max(printed) if printed else None,
    }


def _opening_count(plans: list[dict[str, Any]]) -> int:
    counts: list[int] = []
    for row in plans:
        raw = row.get("shadeOpenings")
        if raw is None:
            raw = row.get("shades")
        try:
            counts.append(max(0, int(raw or 0)))
        except (TypeError, ValueError):
            continue
    if not counts:
        return 0
    # Variants of one type disagree. Use the value that appears most often.
    best = max(set(counts), key=counts.count)
    return best


def _units_from_floor_plans(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["role"] != "floor_plan":
            continue
        for number, unit_type, area in _UNIT_LINE.findall(row.get("text") or ""):
            found[number] = {
                "number": number,
                "type": unit_type.upper(),
                "area": area.replace(",", ""),
                "file": row["file"],
            }
    return [found[key] for key in sorted(found, key=lambda item: int(item))]


def _project_name(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        text = row.get("text") or ""
        match = re.search(r"(\d{2,4}[\-–]\d{2,4}\s+[A-Z][A-Z ]{3,40})", text)
        if match and "MADEIRA" in match.group(1).upper():
            return " ".join(match.group(1).split())
        if "PROJECT" in text.upper() and "MADEIRA" in text.upper():
            for line in text.splitlines():
                if "MADEIRA" in line.upper() and len(line.strip()) < 80:
                    return " ".join(line.split())
    return ""


def _images_for_role(role: str, data: bytes) -> list[tuple[str, bytes]]:
    if role == "unit_matrix":
        return [(f"tile-{index + 1}", jpeg) for index, jpeg in enumerate(_matrix_tiles(data))]
    if role == "unit_plan":
        jpeg = _render_jpeg(data, max_edge=2000)
        return [("plan", jpeg)] if jpeg else []
    return []


def _matrix_tiles(data: bytes, columns: int = 3, rows: int = 3) -> list[bytes]:
    """Overlapping crops so a unit row on a tile edge is still readable."""
    try:
        import fitz
    except Exception:
        return []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.page_count < 1:
            doc.close()
            return []
        page = doc[0]
        rect = page.rect
        overlap = 0.06
        tiles: list[bytes] = []
        for row in range(rows):
            for column in range(columns):
                x0 = rect.width * max(0.0, column / columns - overlap)
                y0 = rect.height * max(0.0, row / rows - overlap)
                x1 = rect.width * min(1.0, (column + 1) / columns + overlap)
                y1 = rect.height * min(1.0, (row + 1) / rows + overlap)
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(1.7, 1.7),
                    clip=fitz.Rect(x0, y0, x1, y1),
                    alpha=False,
                )
                tiles.append(pix.tobytes("jpeg", jpg_quality=68))
        doc.close()
        return tiles
    except Exception:
        return []


def _render_jpeg(data: bytes, max_edge: int = 1400) -> Optional[bytes]:
    try:
        import fitz
    except Exception:
        return None
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        if doc.page_count < 1:
            doc.close()
            return None
        page = doc[0]
        longest = max(page.rect.width, page.rect.height) or 1
        scale = min(2.0, max_edge / longest)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        jpeg = pix.tobytes("jpeg", jpg_quality=65)
        doc.close()
        return jpeg
    except Exception:
        return None
