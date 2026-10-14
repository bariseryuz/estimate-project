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
from concurrent.futures import ThreadPoolExecutor
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


def _measure_plan(row: dict[str, Any]) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    from domain.plan_openings import count_plan_openings

    try:
        return row, count_plan_openings(row["data"])
    except Exception:
        return row, None


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

    identities = project_identities(classified)
    from domain.architectural_reader import lock_drawing_set

    locked = lock_drawing_set(
        [{"file": row["file"], "data": row["data"]} for row in classified],
        identities,
    )
    usable = set(locked["usable_files"])
    classified = [row for row in classified if row["file"] in usable]
    units = _units_from_floor_plans(classified)
    project = locked["project_name"] or _project_name(classified)

    sheets = []
    plan_rows = [row for row in classified if row["role"] == "unit_plan"]
    measured_plans: dict[str, Optional[dict[str, Any]]] = {}
    if plan_rows:
        workers = min(8, len(plan_rows))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row, openings in pool.map(_measure_plan, plan_rows):
                measured_plans[row["file"]] = openings
    for row in classified:
        if row["role"] == "unit_plan":
            openings = measured_plans.get(row["file"])
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
    from domain.window_sizes import read_schedule_dictionary, read_window_sizes, schedule_words

    schedule_roles = {
        "window_schedule": "WINDOW",
        "door_schedule": "DOOR",
        "storefront_schedule": "STOREFRONT",
    }
    for row in classified:
        if row["role"] not in schedule_roles:
            continue
        words = schedule_words(row["data"])
        sizes = read_window_sizes(row["data"], words) if row["role"] == "window_schedule" else None
        dictionary = read_schedule_dictionary(row["data"], schedule_roles[row["role"]], words)
        for record in dictionary:
            record["source_file"] = row["file"]
            record["source_file_hash"] = next(
                (sheet["file_hash"] for sheet in locked["sheets"] if sheet["file"] == row["file"]),
                "",
            )
            record["revision"] = next(
                (sheet["revision"] for sheet in locked["sheets"] if sheet["file"] == row["file"]),
                "",
            )
        if sizes or dictionary:
            sheets.append(
                {
                    "file": row["file"],
                    "role": "window_sizes" if row["role"] == "window_schedule" else "schedule_dictionary",
                    "sizes": sizes or {},
                    "dictionary": dictionary,
                    "openingClass": schedule_roles[row["role"]],
                }
            )

    printed_types = unit_types_from_floor_plans(classified)
    if printed_types:
        sheets.append(
            {
                "file": "floor-plan labels",
                "role": "floor_plan_types",
                "types": printed_types,
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
        "Read the window, storefront, and door schedules before counting. "
        "Each physical opening on a unit floor plan is counted once. "
        "Reflected ceiling plans and elevations are not extra openings. "
        "The unit matrix multiplies that count. The result is an architectural opening quantity, not a shade quantity."
    )
    sheets.append(
        {
            "file": "drawing-set",
            "role": "reader_context",
            "projectId": locked["project_id"],
            "drawingSetId": locked["drawing_set_id"],
            "projectNames": locked["project_names"],
            "readerExceptions": locked["exceptions"],
        }
    )
    if locked["separated"]:
        brief_lines.append(
            "More than one project is in this upload. Their counts are not combined."
        )
    if locked["duplicates"]:
        brief_lines.append(f"Exact duplicate files set aside: {len(locked['duplicates'])}.")
    if any(item["issue_code"] == "REVISION_CONFLICT" for item in locked["exceptions"]):
        brief_lines.append("Sheets with an uncertain revision were not used.")

    return {
        "projectName": project,
        "projectId": locked["project_id"],
        "drawingSetId": locked["drawing_set_id"],
        "projectNames": locked["project_names"],
        "separated": locked["separated"],
        "inventory": locked["sheets"],
        "duplicates": locked["duplicates"],
        "readerExceptions": locked["exceptions"],
        "sheetCount": len(pdfs),
        "roles": by_role,
        "floorPlanUnits": len(units),
        "brief": "\n".join(brief_lines),
        "sheets": (
            [sheet for sheet in sheets if sheet.get("role") == "reader_context"]
            if locked["separated"]
            else sheets
        ),
    }


def classify_sheet(name: str, text: str) -> str:
    blob = f"{name}\n{(text or '')[:5000]}".lower()
    file_name = name.lower()
    if "unit matrix" in blob or "unit-matrix" in file_name:
        return "unit_matrix"
    if "door schedule" in blob or ("door" in file_name and "schedule" in file_name and "window" not in file_name):
        return "door_schedule"
    if "storefront" in blob and "schedule" in blob and "window" not in blob:
        return "storefront_schedule"
    if "window schedule" in blob or ("window" in file_name and "schedule" in file_name):
        return "window_schedule"
    if re.search(r"unit[-_ ]+[a-z0-9]", file_name) and "matrix" not in file_name:
        return "unit_plan"
    if "floor" in file_name and not any(skip in file_name for skip in _FLOOR_SKIP):
        return "floor_plan"
    return "other"


_SKIP_TEXT = (
    "elev",
    "section",
    "detail",
    "note",
    "rcp",
    "roof",
    "site",
    "stair",
    "door",
    "cladding",
    "zoning",
    "cover",
    "legend",
    "schedule",
    "finish",
    "furniture",
)


def filenames_needing_text(names: list[str]) -> list[str]:
    """
    Sheets whose words can change the count.

    A named unit plan is measured from its drawn tags, and a named unit matrix
    is read from the image. Floor-plan labels, and any sheet that might still
    be a matrix or a floor plan, are the ones that need a text read.
    """
    roles = [(name, classify_sheet(name, "")) for name in names]
    role_set = {role for _, role in roles}
    named_pair = "unit_matrix" in role_set and "unit_plan" in role_set
    needed: list[str] = []
    for name, role in roles:
        if role == "floor_plan":
            needed.append(name)
            continue
        if role in ("unit_plan", "unit_matrix", "window_schedule", "door_schedule", "storefront_schedule"):
            continue
        lower = name.lower()
        if "plan" in lower and not any(skip in lower for skip in _SKIP_TEXT):
            needed.append(name)
            continue
        if not named_pair:
            needed.append(name)
    return needed


def norm_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def combine_counts(
    matrix_types: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    sizes: Optional[dict[str, dict[str, Any]]] = None,
    schedules: Optional[list[dict[str, Any]]] = None,
    *,
    matrix_units: Optional[list[dict[str, Any]]] = None,
    project_id: str = "",
    drawing_set_id: str = "",
    project_names: Optional[list[str]] = None,
    hold_multiplication: bool = False,
) -> dict[str, Any]:
    """
    Multiply plan openings by the matrix count of that exact subtype.

    A-1a, A-1b, and A-1c stay separate. A-1A matches A-1-a only as a formatting
    alias. The result is an opening count. Shade quantity is left empty.
    """
    from domain.architectural_reader import expand_openings

    return expand_openings(
        matrix_types,
        plan_rows,
        sizes,
        schedules,
        matrix_units=matrix_units,
        project_id=project_id,
        drawing_set_id=drawing_set_id,
        project_names=project_names,
        hold_multiplication=hold_multiplication,
    )


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

    printedTotal is a TOTAL UNITS figure at least as large as the apartments
    already read. A smaller figure is one floor's subtotal, not the building.
    """
    readings: dict[str, list[tuple[str, int, str]]] = {}
    printed: list[int] = []
    floor_totals: list[dict[str, Any]] = []
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
            floor = str(row.get("floor") or "").strip()
            readings.setdefault(number, []).append((unit_type, qty, floor))
        for value in payload.get("printedTotals") or []:
            try:
                total = int(value)
            except (TypeError, ValueError):
                continue
            if 0 < total < 5000:
                printed.append(total)
        for row in payload.get("floorTotals") or []:
            if isinstance(row, dict) and row.get("floor"):
                floor_totals.append({"floor": str(row.get("floor")), "total": row.get("total")})
    units = {}
    for number, options in readings.items():
        type_names = [name for name, _qty, _floor in options]
        chosen = sorted(set(type_names), key=lambda name: (-type_names.count(name), name))[0]
        chosen_rows = [(item_qty, floor) for name, item_qty, floor in options if name == chosen]
        qtys = [item_qty for item_qty, _floor in chosen_rows]
        qty = sorted(qtys, key=lambda item_qty: (-qtys.count(item_qty), item_qty))[0]
        floors = [floor for item_qty, floor in chosen_rows if item_qty == qty and floor]
        units[number] = {
            "unit": number,
            "type": chosen,
            "qty": qty,
            "floor": floors[0] if floors else "",
        }
    transcribed = sum(unit["qty"] for unit in units.values())
    # A floor's TOTAL UNITS is smaller than the apartments already read.
    # Only a figure at least that large can be the building total.
    building = [value for value in printed if value >= transcribed]
    rejected = [value for value in printed if value < transcribed]
    grouped = _group_unit_types(list(units.values()))
    return {
        "units": list(units.values()),
        "types": grouped,
        "transcribedUnits": transcribed,
        "printedTotal": max(building) if building else None,
        "rejectedPrintedTotals": rejected,
        "floorTotals": floor_totals,
    }


def _floor_key(value: str) -> str:
    """Ground, 2ND FLOOR, and 2 are the same floor label."""
    text = re.sub(r"\bfloor\b", "", (value or "").lower())
    text = re.sub(r"[^a-z0-9]", "", text)
    names = {
        "ground": "1",
        "first": "1",
        "second": "2",
        "third": "3",
        "fourth": "4",
        "fifth": "5",
        "sixth": "6",
        "seventh": "7",
        "eighth": "8",
        "ninth": "9",
    }
    if text in names:
        return names[text]
    return re.sub(r"(st|nd|rd|th)$", "", text)


def accept_matrix_table(merged: dict[str, Any]) -> dict[str, Any]:
    """
    A floor subtotal is accepted only when that floor's rows add up to it.

    Multiplication waits for that check. A printed figure smaller than the
    unit rows is a floor subtotal, not the building total, and those rows
    are not multiplied until the floors reconcile.
    """
    from domain.architectural_reader import exception_record

    units = [dict(unit) for unit in merged.get("units") or []]
    transcribed = sum(int(unit.get("qty") or 0) for unit in units)
    printed_total = merged.get("printedTotal")
    rejected = [int(value) for value in merged.get("rejectedPrintedTotals") or []]
    floor_rows = []
    for row in merged.get("floorTotals") or []:
        floor = _floor_key(str(row.get("floor") or ""))
        try:
            total = int(row.get("total"))
        except (TypeError, ValueError):
            continue
        if floor and total >= 0:
            floor_rows.append({"floor": floor, "total": total, "label": str(row.get("floor"))})

    exceptions: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    ready = False

    def mismatch(mark: str, printed: Optional[int], actual: Optional[int], needed: str) -> None:
        exceptions.append(
            exception_record(
                project_id="",
                unit_or_mark=mark,
                issue_code="MATRIX_MISMATCH",
                source_location="unit matrix",
                quantity_affected=actual,
                decision_needed=needed,
            )
        )

    if floor_rows:
        by_floor: dict[str, list[dict[str, Any]]] = {}
        unlabeled = 0
        for unit in units:
            floor = _floor_key(str(unit.get("floor") or ""))
            if not floor:
                unlabeled += int(unit.get("qty") or 0)
                continue
            by_floor.setdefault(floor, []).append(unit)
        failed = False
        seen: set[str] = set()
        matched: list[dict[str, Any]] = []
        for row in floor_rows:
            actual = sum(int(unit.get("qty") or 0) for unit in by_floor.get(row["floor"], []))
            if actual == row["total"] and row["floor"] not in seen:
                matched.extend(by_floor[row["floor"]])
                seen.add(row["floor"])
            else:
                failed = True
                mismatch(
                    row["label"],
                    row["total"],
                    actual,
                    (
                        f"The printed subtotal for {row['label']} is {row['total']}. "
                        f"The unit rows on that floor add to {actual}. "
                        "That subtotal was not accepted, and nothing was multiplied."
                    ),
                )
        extra = [floor for floor in by_floor if floor not in seen]
        if extra or unlabeled:
            failed = True
            mismatch(
                "floor rows",
                None,
                unlabeled + sum(int(unit.get("qty") or 0) for floor in extra for unit in by_floor[floor]),
                "Some unit rows have no accepted floor subtotal. They were not multiplied.",
            )
        floor_sum = sum(row["total"] for row in floor_rows)
        if not failed and printed_total is not None and printed_total != floor_sum:
            failed = True
            mismatch(
                "TOTAL UNITS",
                printed_total,
                floor_sum,
                (
                    f"The sheet prints TOTAL UNITS {printed_total}. "
                    f"The accepted floor subtotals add to {floor_sum}."
                ),
            )
        if not failed and matched:
            accepted = matched
            ready = True
    elif rejected or (printed_total is not None and printed_total != transcribed):
        shown = rejected[0] if rejected else printed_total
        mismatch(
            "TOTAL UNITS",
            shown,
            transcribed,
            (
                f"A printed figure of {shown} does not equal the {transcribed} unit rows. "
                "It was not used as the building total, and the rows were not multiplied."
            ),
        )
    elif units:
        accepted = units
        ready = True

    return {
        "ready": ready,
        "units": accepted if ready else [],
        "types": _group_unit_types(accepted) if ready else [],
        "exceptions": exceptions,
        "transcribedUnits": transcribed,
    }


def unit_types_from_floor_plans(rows: list[dict[str, Any]]) -> dict[str, str]:
    """
    Apartment number to unit type, from text printed on the floor plans.

    An overall floor plan wins. A partial plan fills a number the overall
    sheets do not label, and only when those partials agree. Area diagrams
    are not used.
    """
    overall_norm: dict[str, set[str]] = {}
    overall_printed: dict[str, str] = {}
    partial: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        if row.get("role") != "floor_plan":
            continue
        name = str(row.get("file") or "")
        if "F.A.R" in name.upper():
            continue
        found = _UNIT_LINE.findall(row.get("text") or "")
        if "PARTIAL" in name.upper():
            for number, unit_type, _area in found:
                partial.setdefault(number, set()).add((norm_type(unit_type), unit_type.upper()))
            continue
        for number, unit_type, _area in found:
            overall_norm.setdefault(number, set()).add(norm_type(unit_type))
            overall_printed[number] = unit_type.upper()
    typed: dict[str, str] = {}
    for number, norms in overall_norm.items():
        if len(norms) == 1:
            typed[number] = overall_printed[number]
    for number, options in partial.items():
        if number in typed:
            continue
        norms = {norm for norm, _printed in options}
        if len(norms) != 1:
            continue
        hyphenated = [printed for _norm, printed in options if "-" in printed]
        typed[number] = sorted(hyphenated or [printed for _norm, printed in options])[0]
    return typed


def apply_printed_unit_types(merged: dict[str, Any], printed_types: dict[str, str]) -> dict[str, Any]:
    """Use a floor-plan label when that apartment number is printed in text."""
    if not printed_types:
        return merged
    units = []
    confirmed = 0
    corrected = 0
    for unit in merged.get("units") or []:
        item = dict(unit)
        printed = printed_types.get(str(item.get("unit") or "").upper())
        if printed:
            if norm_type(printed) == norm_type(str(item.get("type") or "")):
                confirmed += 1
            else:
                corrected += 1
            item["type"] = printed
        units.append(item)
    updated = dict(merged)
    updated["units"] = units
    updated["types"] = _group_unit_types(units)
    updated["transcribedUnits"] = sum(int(unit.get("qty") or 0) for unit in units)
    updated["typesFromFloorPlans"] = confirmed + corrected
    updated["typesCorrected"] = corrected
    return updated


def _group_unit_types(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for unit in units:
        key = norm_type(str(unit.get("type") or ""))
        if not key:
            continue
        bucket = grouped.setdefault(key, {"type": unit["type"], "count": 0})
        bucket["count"] += int(unit.get("qty") or 0)
    return list(grouped.values())


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


_PROJECT_LABEL = re.compile(r"PROJECT(?:\s+NAME)?\s*[:\-]\s*(.+)", re.IGNORECASE)
_STREET_SUFFIX = r"(?:AVENUE|AVE|STREET|BOULEVARD|BLVD|DRIVE|DR|LANE|LN|COURT|CT|ROAD|WAY)"
_STREET_LINE = re.compile(
    rf"\b(\d{{2,5}}(?:[ \t]*[-–][ \t]*\d{{2,5}})?[ \t]+"
    rf"(?:[A-Z0-9][A-Z0-9.'\-]*[ \t]+){{1,6}}"
    rf"(?:{_STREET_SUFFIX}))\b",
    re.IGNORECASE,
)
_STREET_ALIAS = {
    "AVE": "AVENUE",
    "BLVD": "BOULEVARD",
    "DR": "DRIVE",
    "LN": "LANE",
    "CT": "COURT",
    "RD": "ROAD",
}


def _street_name(value: str) -> str:
    parts = value.upper().split()
    if parts and parts[-1] in _STREET_ALIAS:
        parts[-1] = _STREET_ALIAS[parts[-1]]
    return " ".join(parts)


def _street_scores(text: str) -> dict[str, int]:
    """Site address scores above the client and the architect in the same title block."""
    scores: dict[str, int] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        for match in _STREET_LINE.finditer(line):
            name = _street_name(match.group(1))
            before = " ".join(lines[max(0, index - 4) : index]).upper()
            after = " ".join(lines[index + 1 : index + 3]).upper()
            score = 1
            if re.search(r"\d-\d", name):
                score += 2
            if any(word in after for word in ("PROPOSED", "DEVELOPMENT", "APARTMENT", "RESIDENCE")):
                score += 5
            if "CLIENT" in before:
                score -= 6
            if "COPYRIGHT" in before or "PROTECTED" in before or "TEL" in before:
                score -= 4
            scores[name] = scores.get(name, 0) + score
    return scores


def project_identities(rows: list[dict[str, Any]]) -> list[str]:
    """
    One project name for this upload.

    A project label is the name. A title block that also prints the client and
    the architect is still one project. Two project labels, or two site streets
    that never share a sheet, stay apart.
    """
    labeled: dict[str, int] = {}
    for row in rows:
        for line in (row.get("text") or "").splitlines():
            match = _PROJECT_LABEL.search(line.strip())
            if not match:
                continue
            name = " ".join(match.group(1).split())
            if 3 < len(name) < 80:
                labeled[name] = labeled.get(name, 0) + 1
    if labeled:
        return list(labeled)

    chosen: list[str] = []
    fallback: dict[str, int] = {}
    for row in rows:
        scores = _street_scores(row.get("text") or "")
        if not scores:
            continue
        best = max(scores, key=lambda name: (scores[name], len(name)))
        for name in scores:
            fallback[name] = fallback.get(name, 0) + 1
        if scores[best] > 0 and all(scores[other] < scores[best] for other in scores if other != best):
            chosen.append(best)
    unique = list(dict.fromkeys(chosen))
    if unique:
        return unique
    if not fallback:
        return []
    winner = sorted(fallback, key=lambda name: (fallback[name], len(name)), reverse=True)[0]
    return [winner]


def _project_name(rows: list[dict[str, Any]]) -> str:
    """The name the title block repeats. A street or a PROJECT label, not one job's name."""
    names = project_identities(rows)
    if not names:
        return ""
    return names[0]


def _images_for_role(role: str, data: bytes) -> list[tuple[str, bytes]]:
    if role == "unit_matrix":
        return [(f"tile-{index + 1}", jpeg) for index, jpeg in enumerate(_matrix_tiles(data))]
    if role == "unit_plan":
        jpeg = _render_jpeg(data, max_edge=2000)
        return [("plan", jpeg)] if jpeg else []
    return []


def _matrix_tiles(data: bytes, columns: int = 3, rows: int = 3) -> list[bytes]:
    """
    One crop per scanned table, upright.

    A full-sheet grid spends most of its calls on blank paper and cuts rows
    in half. A table embedded as an image is cropped on its own. A vector
    matrix still uses the grid, and a nearly blank cell is left out.
    """
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
        tiles = _table_image_tiles(page)
        if not tiles:
            tiles = _grid_tiles(page, columns, rows)
        doc.close()
        return tiles
    except Exception:
        return []


def _table_image_tiles(page) -> list[bytes]:
    import fitz

    rotation = page.rotation
    order = page.rotation_matrix
    boxes = []
    for info in page.get_image_info():
        if min(info.get("width") or 0, info.get("height") or 0) < 900:
            continue
        box = fitz.Rect(info["bbox"]) & page.mediabox
        if box.width < 80 or box.height < 80:
            continue
        if box.width > page.mediabox.width * 0.92 and box.height > page.mediabox.height * 0.92:
            continue
        boxes.append(box)
    kept: list[Any] = []
    for box in sorted(boxes, key=lambda item: item.width * item.height, reverse=True):
        area = box.width * box.height
        if any(_rect_area(fitz.Rect(box) & other) > 0.85 * area for other in kept):
            continue
        kept.append(box)
    if not kept:
        return []

    def _reading_order(box: Any) -> tuple[float, float]:
        center = fitz.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2) * order
        return (center.y, center.x)

    page.set_rotation(0)
    tiles = []
    ordered = sorted(kept, key=_reading_order)
    for box in ordered:
        longer = max(box.width, box.height) or 1
        scale = min(2.0, 1700 / longer)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box, alpha=False)
        if pix.width < 40 or pix.height < 40:
            continue
        tiles.append(_upright_jpeg(pix, rotation))
    if not tiles:
        page.set_rotation(rotation)
    return tiles


def _rect_area(rect) -> float:
    return max(rect.width, 0.0) * max(rect.height, 0.0)


def _upright_jpeg(pix, rotation: int) -> bytes:
    turns = rotation % 360
    if turns == 0:
        return pix.tobytes("jpeg", jpg_quality=68)
    try:
        from PIL import Image
    except Exception:
        return pix.tobytes("jpeg", jpg_quality=68)
    import io

    mode = "RGB" if pix.n >= 3 else "L"
    image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    image = image.transpose(
        {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}[turns]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=68)
    return buffer.getvalue()


def _grid_tiles(page, columns: int, rows: int) -> list[bytes]:
    import fitz

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
            if _ink_fraction(pix) < 0.012:
                continue
            tiles.append(pix.tobytes("jpeg", jpg_quality=68))
    return tiles


def _ink_fraction(pix) -> float:
    samples = pix.samples
    step = max(pix.n, 1) * 8
    dark = 0
    total = 0
    for index in range(0, len(samples), step):
        total += 1
        if samples[index] < 245:
            dark += 1
    if not total:
        return 0.0
    return dark / total


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
