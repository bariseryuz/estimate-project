"""
Architectural plan reader.

Schedules are read first. Each physical opening on a unit plan is counted once.
Matrix rows are matched by exact subtype, then multiplied with ordinary arithmetic.
An opening quantity is not a shade quantity. Unresolved rows stay visible and are
withheld from the released set.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

ISSUE_CODES = (
    "MISSING_SCHEDULE",
    "UNREADABLE_TAG",
    "UNTAGGED_OPENING",
    "BLANK_DIMENSION",
    "DIMENSION_CONFLICT",
    "UNKNOWN_SUBTYPE",
    "VARIANT_UNCONFIRMED",
    "MATRIX_MISMATCH",
    "DUPLICATE_OPENING",
    "SCOPE_UNCONFIRMED",
    "REVISION_CONFLICT",
    "PROJECT_SEPARATION",
)

_SHEET_NUMBER = re.compile(r"(A-\d+(?:\.\d+)*[A-Z]?)", re.IGNORECASE)
_COPY_SUFFIX = re.compile(r"\((\d+)\)\s*(?:\.pdf)?$", re.IGNORECASE)
_REVISION = re.compile(r"Rev\.([A-Za-z0-9#][A-Za-z0-9#.\-]*)", re.IGNORECASE)
_SPECIFIC_UNIT = re.compile(r"\bUNIT\s+((?:LW-)?\d{3,4})\b", re.IGNORECASE)
_HIGH_MULTIPLIER = 20


def exception_record(
    *,
    project_id: str,
    unit_or_mark: str,
    issue_code: str,
    source_location: str,
    quantity_affected: Optional[int],
    decision_needed: str,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "unit_or_mark": unit_or_mark,
        "issue_code": issue_code,
        "source_location": source_location,
        "quantity_affected": quantity_affected,
        "decision_needed": decision_needed,
    }


def formatting_key(value: str) -> str:
    """A-1A and A-1-a share a key. A-1a and A-1b do not. A-1a and A-1 do not."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sheet_number_from_name(name: str) -> str:
    match = _SHEET_NUMBER.search(name or "")
    return match.group(1).upper() if match else ""


def copy_suffix(name: str) -> str:
    """A trailing (3) or (4) is another upload of the file, not a design revision."""
    match = _COPY_SUFFIX.search(name or "")
    return match.group(1) if match else ""


def revision_from_name(name: str) -> str:
    match = _REVISION.search(name or "")
    return match.group(1) if match else ""


def title_from_name(name: str) -> str:
    base = re.sub(r"\.pdf$", "", name or "", flags=re.IGNORECASE)
    base = _COPY_SUFFIX.sub("", base).strip()
    base = _SHEET_NUMBER.sub("", base, count=1)
    base = _REVISION.sub("", base)
    return " ".join(base.replace("-", " ").replace("_", " ").replace("&", " ").split())


def project_id_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "unknown-project"


def lock_drawing_set(files: list[dict[str, Any]], project_names: list[str]) -> dict[str, Any]:
    """
    Inventory every sheet, drop byte-for-byte duplicates, and stop before a
    sheet whose revision cannot be ordered. Two project names are not merged.
    """
    distinct = list(dict.fromkeys(name.strip() for name in project_names if name and name.strip()))
    seen: dict[str, str] = {}
    sheets: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in files:
        digest = file_sha256(row["data"])
        record = {
            "file": row["file"],
            "sheet_number": sheet_number_from_name(row["file"]),
            "title": title_from_name(row["file"]),
            "revision": revision_from_name(row["file"]),
            "copy_suffix": copy_suffix(row["file"]),
            "page": 1,
            "file_hash": digest,
        }
        if digest in seen:
            duplicates.append({**record, "duplicate_of": seen[digest]})
            continue
        seen[digest] = row["file"]
        sheets.append(record)

    exceptions: list[dict[str, Any]] = []
    blocked_sheets: set[str] = set()
    by_number: dict[str, list[dict[str, Any]]] = {}
    for sheet in sheets:
        if sheet["sheet_number"]:
            by_number.setdefault(sheet["sheet_number"], []).append(sheet)
    for number, group in by_number.items():
        if len(group) < 2:
            continue
        revisions = {sheet["revision"] for sheet in group}
        blocked_sheets.update(sheet["file"] for sheet in group)
        exceptions.append(
            exception_record(
                project_id="",
                unit_or_mark=number,
                issue_code="REVISION_CONFLICT",
                source_location=", ".join(sheet["file"] for sheet in group),
                quantity_affected=None,
                decision_needed=(
                    "These copies are not identical and a filename suffix such as (3) is not a revision. "
                    f"Revisions found: {', '.join(sorted(revisions)) or 'none printed'}. "
                    "Compare the title blocks before using either sheet."
                ),
            )
        )

    usable = [sheet for sheet in sheets if sheet["file"] not in blocked_sheets]
    drawing_set_id = ""
    if usable or sheets:
        basis = "\n".join(sorted(sheet["file_hash"] for sheet in (usable or sheets)))
        drawing_set_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    separated = len(distinct) > 1
    project_id = project_id_for(distinct[0]) if len(distinct) == 1 else ""
    if separated:
        exceptions.append(
            exception_record(
                project_id="",
                unit_or_mark=" | ".join(distinct),
                issue_code="PROJECT_SEPARATION",
                source_location="title block",
                quantity_affected=None,
                decision_needed=(
                    "These drawings name more than one project. Counts, unit types, and schedule "
                    "dimensions stay with their own project and are not added together."
                ),
            )
        )
    for item in exceptions:
        item["project_id"] = project_id
    return {
        "project_id": project_id,
        "project_name": distinct[0] if len(distinct) == 1 else "",
        "project_names": distinct,
        "drawing_set_id": drawing_set_id,
        "sheets": sheets,
        "usable_files": [] if separated else [sheet["file"] for sheet in usable],
        "duplicates": duplicates,
        "exceptions": exceptions,
        "separated": separated,
    }


def schedule_index(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Door type A and window A are different records."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        klass = str(record.get("opening_class") or "WINDOW").upper()
        mark = str(record.get("mark_normalized") or record.get("mark_raw") or "").upper()
        if mark:
            index[(klass, mark)] = record
    return index


def index_from_sizes(sizes: Optional[dict[str, dict[str, Any]]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for mark, size in (sizes or {}).items():
        if not isinstance(size, dict):
            continue
        token = str(mark).upper()
        index[("WINDOW", token)] = {
            "opening_class": "WINDOW",
            "mark_raw": token,
            "mark_normalized": token,
            "width_original": size.get("width") or None,
            "height_original": size.get("height") or None,
            "width_inches": size.get("widthInches"),
            "height_inches": size.get("heightInches"),
            "dimension_basis": "schedule" if size.get("width") and size.get("height") else None,
            "restrictions": "",
            "glazed": False,
            "opaque": False,
            "conflicts": [],
            "issue_codes": [],
        }
    return index


def expand_openings(
    matrix_types: list[dict[str, Any]],
    plan_rows: list[dict[str, Any]],
    sizes: Optional[dict[str, dict[str, Any]]] = None,
    schedules: Optional[list[dict[str, Any]]] = None,
    *,
    matrix_units: Optional[list[dict[str, Any]]] = None,
    project_id: str = "",
    drawing_set_id: str = "",
    project_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    extended quantity = openings on the plan x matrix units of that exact subtype.

    The arithmetic is fixed after the sheets are read. A language model is not
    asked to multiply. Shade quantity is left empty for the estimator.
    """
    names = [name for name in (project_names or []) if name]
    if len(dict.fromkeys(names)) > 1:
        blocked = exception_record(
            project_id="",
            unit_or_mark=" | ".join(dict.fromkeys(names)),
            issue_code="PROJECT_SEPARATION",
            source_location="title block",
            quantity_affected=None,
            decision_needed="Do not add these projects together.",
        )
        return _empty_expansion([blocked])

    dictionary = schedule_index(schedules or [])
    dictionary.update(index_from_sizes(sizes))
    units = [row for row in (matrix_units or []) if int(row.get("qty") or 0) > 0]
    exceptions: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    aliases: list[dict[str, str]] = []
    used_plan_keys: set[str] = set()

    for entry in matrix_types:
        code = str(entry.get("type") or "").strip()
        try:
            stated = int(entry.get("count") or 0)
        except (TypeError, ValueError):
            stated = 0
        if not code or stated <= 0:
            continue
        key = formatting_key(code)
        type_units = [row for row in units if formatting_key(str(row.get("type") or "")) == key]
        applicable = sum(int(row.get("qty") or 0) for row in type_units) or stated
        if type_units and applicable != stated:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=code,
                    issue_code="MATRIX_MISMATCH",
                    source_location="unit matrix",
                    quantity_affected=None,
                    decision_needed=(
                        f"The type total says {stated} and the individual rows add to {applicable}. "
                        "Resolve that before using either number."
                    ),
                )
            )
            continue
        plans = _plans_for_type(code, plan_rows)
        if not plans:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=code,
                    issue_code="UNKNOWN_SUBTYPE",
                    source_location="unit matrix",
                    quantity_affected=applicable,
                    decision_needed="No unit plan title matches this subtype. Do not borrow another variant.",
                )
            )
            continue
        signatures = {_opening_signature(_window_openings(plan)) for plan in plans}
        if len(signatures) > 1:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=code,
                    issue_code="VARIANT_UNCONFIRMED",
                    source_location=", ".join(str(plan.get("file") or "") for plan in plans),
                    quantity_affected=applicable,
                    decision_needed=(
                        "These plans use the same subtype label but do not show the same openings. "
                        "Do not pick the larger count."
                    ),
                )
            )
            continue
        plan = plans[0]
        used_plan_keys.add(formatting_key(str(plan.get("unitType") or "")) or key)
        plan_label = str(plan.get("unitType") or "") or _type_from_filename(str(plan.get("file") or ""))
        if plan_label and formatting_key(plan_label) == key and plan_label.strip().upper() != code.upper():
            aliases.append({"matrix": code, "plan": plan_label, "basis": "formatting"})
        specific = _specific_unit(plan)
        if specific:
            held = [row for row in type_units if str(row.get("unit") or "").upper() != specific]
            if held or not type_units:
                exceptions.append(
                    exception_record(
                        project_id=project_id,
                        unit_or_mark=code,
                        issue_code="VARIANT_UNCONFIRMED",
                        source_location=str(plan.get("file") or ""),
                        quantity_affected=applicable,
                        decision_needed=(
                            f"This plan is labeled for unit {specific}. "
                            "The other units of this subtype stay out of the multiplication."
                        ),
                    )
                )
                continue
        alias_only = bool(plan_label) and plan_label.strip().upper() != code.upper()
        _consume_plan(
            plan,
            code=code,
            plan_label=plan_label,
            applicable=applicable,
            units=type_units,
            dictionary=dictionary,
            project_id=project_id,
            drawing_set_id=drawing_set_id,
            alias_only=alias_only,
            records=records,
            excluded=excluded,
            exceptions=exceptions,
        )

    for plan in plan_rows:
        label = str(plan.get("unitType") or "") or _type_from_filename(str(plan.get("file") or ""))
        plan_key = formatting_key(label)
        if plan_key and plan_key not in {formatting_key(str(entry.get("type") or "")) for entry in matrix_types}:
            if plan_key not in used_plan_keys:
                exceptions.append(
                    exception_record(
                        project_id=project_id,
                        unit_or_mark=label,
                        issue_code="UNKNOWN_SUBTYPE",
                        source_location=str(plan.get("file") or ""),
                        quantity_affected=None,
                        decision_needed="This plan subtype is not on the unit matrix.",
                    )
                )
                used_plan_keys.add(plan_key)

    return _package(records, excluded, exceptions, aliases, project_id)


def reconcile_floor_totals(
    units: list[dict[str, Any]],
    floor_totals: list[dict[str, Any]],
    *,
    project_id: str = "",
    printed_total: Optional[int] = None,
    transcribed: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Floor subtotals and the building total are checks. They are not extra units."""
    exceptions = []
    if printed_total is not None and transcribed is not None and printed_total != transcribed:
        exceptions.append(
            exception_record(
                project_id=project_id,
                unit_or_mark="TOTAL UNITS",
                issue_code="MATRIX_MISMATCH",
                source_location="unit matrix",
                quantity_affected=transcribed,
                decision_needed=(
                    f"The sheet prints TOTAL UNITS {printed_total}. "
                    f"The individual rows add to {transcribed}."
                ),
            )
        )
    by_floor: dict[str, int] = {}
    for unit in units:
        floor = str(unit.get("floor") or "").strip()
        if not floor:
            continue
        by_floor[floor] = by_floor.get(floor, 0) + int(unit.get("qty") or 0)
    for row in floor_totals:
        floor = str(row.get("floor") or "").strip()
        try:
            printed = int(row.get("total"))
        except (TypeError, ValueError):
            continue
        actual = by_floor.get(floor)
        if actual is None or actual != printed:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=floor or "floor subtotal",
                    issue_code="MATRIX_MISMATCH",
                    source_location="unit matrix",
                    quantity_affected=actual,
                    decision_needed=(
                        f"The printed subtotal for {floor or 'that floor'} is {printed}. "
                        f"The unit rows on that floor add to {actual}."
                    ),
                )
            )
    return exceptions


def _empty_expansion(exceptions: list[dict[str, Any]]) -> dict[str, Any]:
    packaged = _package([], [], exceptions, [], "")
    return packaged


def _plans_for_type(code: str, plan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = formatting_key(code)
    exact = [
        row
        for row in plan_rows
        if formatting_key(str(row.get("unitType") or "")) == key
        or formatting_key(_type_from_filename(str(row.get("file") or ""))) == key
    ]
    return exact


def _type_from_filename(name: str) -> str:
    from domain.drawing_set import type_from_filename

    return type_from_filename(name)


def _specific_unit(plan: dict[str, Any]) -> str:
    blob = f"{plan.get('file') or ''}\n{plan.get('notes') or ''}"
    match = _SPECIFIC_UNIT.search(blob)
    return match.group(1).upper() if match else ""


def _window_openings(plan: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [row for row in (plan.get("openings") or []) if isinstance(row, dict)]
    if explicit:
        return explicit
    tags = [row for row in (plan.get("tags") or []) if isinstance(row, dict)]
    if tags:
        return [
            {
                "opening_class": "WINDOW",
                "mark": str(row.get("tag") or ""),
                "room": str(row.get("room") or ""),
                "bounding_box": row.get("bounding_box"),
            }
            for row in tags
        ]
    raw = plan.get("shadeOpenings")
    if raw is None:
        raw = plan.get("shades")
    try:
        count = int(raw or 0)
    except (TypeError, ValueError):
        count = 0
    return [
        {"opening_class": "WINDOW", "mark": "", "room": "", "untagged": True}
        for _ in range(max(0, count))
    ]


def _opening_signature(openings: list[dict[str, Any]]) -> tuple:
    return tuple(
        sorted(
            (
                str(row.get("opening_class") or "WINDOW").upper(),
                str(row.get("mark") or "").upper(),
                str(row.get("room") or "").upper(),
            )
            for row in openings
        )
    )


def _consume_plan(
    plan: dict[str, Any],
    *,
    code: str,
    plan_label: str,
    applicable: int,
    units: list[dict[str, Any]],
    dictionary: dict[tuple[str, str], dict[str, Any]],
    project_id: str,
    drawing_set_id: str,
    alias_only: bool,
    records: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    exceptions: list[dict[str, Any]],
) -> None:
    openings = _window_openings(plan)
    doors = [row for row in (plan.get("doors") or []) if isinstance(row, dict)]
    source = str(plan.get("file") or "")
    floors = sorted({str(row.get("floor") or "").strip() for row in units if str(row.get("floor") or "").strip()})
    floor_label = ", ".join(floors)
    unit_numbers = [str(row.get("unit") or "") for row in units if row.get("unit")]

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for opening in openings:
        klass = str(opening.get("opening_class") or "WINDOW").upper()
        mark = str(opening.get("mark") or "").strip().upper()
        if opening.get("assembly") and opening.get("components"):
            mark = "+".join(str(part).upper() for part in opening["components"])
        grouped.setdefault((klass, mark, str(opening.get("room") or "")), []).append(opening)

    seen_physical: set[tuple[str, str, str]] = set()
    for (klass, mark, room), group in grouped.items():
        physical_key = (source, mark, room, str(group[0].get("bounding_box")))
        if physical_key in seen_physical:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=mark or code,
                    issue_code="DUPLICATE_OPENING",
                    source_location=source,
                    quantity_affected=applicable,
                    decision_needed="This opening is already counted on the floor plan.",
                )
            )
            continue
        seen_physical.add(physical_key)
        per_unit = 1 if group[0].get("assembly") else len(group)
        if not mark:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=code,
                    issue_code="UNTAGGED_OPENING",
                    source_location=source,
                    quantity_affected=per_unit * applicable,
                    decision_needed="A glazed opening has no tag. Do not assign a mark or drop the opening.",
                )
            )
            records.append(
                _record(
                    project_id=project_id,
                    drawing_set_id=drawing_set_id,
                    opening_class=klass,
                    mark="",
                    unit_type=code,
                    floor=floor_label,
                    room=room,
                    schedule=None,
                    per_unit=per_unit,
                    applicable=applicable,
                    status="unresolved",
                    issue_codes=["UNTAGGED_OPENING"],
                    source=source,
                    plan=plan,
                    units=unit_numbers,
                    coverage="unresolved",
                )
            )
            continue
        schedule_class = "STOREFRONT" if mark.startswith("ST-") else klass
        schedule = dictionary.get((schedule_class, mark))
        if schedule is not None:
            klass = schedule_class
        issue_codes: list[str] = []
        status = "verified"
        coverage = "opening"
        if schedule is None:
            issue_codes.append("MISSING_SCHEDULE")
            status = "unresolved"
        else:
            issue_codes.extend(schedule.get("issue_codes") or [])
            if "BLANK_DIMENSION" in issue_codes or "DIMENSION_CONFLICT" in issue_codes:
                status = "unresolved"
            elif schedule.get("restrictions"):
                status = "provisional"
        if klass in {"DOOR", "STOREFRONT"}:
            coverage = "scope_review"
            if schedule and schedule.get("opaque"):
                status = "excluded"
            elif status == "verified":
                status = "provisional"
                issue_codes.append("SCOPE_UNCONFIRMED")
        if alias_only and status == "verified":
            status = "provisional"
        if applicable >= _HIGH_MULTIPLIER and status == "verified":
            status = "provisional"
        if "MISSING_SCHEDULE" in issue_codes:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=mark,
                    issue_code="MISSING_SCHEDULE",
                    source_location=source,
                    quantity_affected=per_unit * applicable,
                    decision_needed=f"No {klass} schedule row is named {mark}.",
                )
            )
        for code_name in ("BLANK_DIMENSION", "DIMENSION_CONFLICT"):
            if code_name in issue_codes:
                exceptions.append(
                    exception_record(
                        project_id=project_id,
                        unit_or_mark=mark,
                        issue_code=code_name,
                        source_location=str((schedule or {}).get("source_file") or source),
                        quantity_affected=per_unit * applicable,
                        decision_needed=(
                            "The schedule cell is blank."
                            if code_name == "BLANK_DIMENSION"
                            else "The schedule shows more than one size. Neither size was chosen."
                        ),
                    )
                )
        if "SCOPE_UNCONFIRMED" in issue_codes:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=mark,
                    issue_code="SCOPE_UNCONFIRMED",
                    source_location=source,
                    quantity_affected=per_unit * applicable,
                    decision_needed="Glazed door or storefront coverage is not specified. It is not a shade count.",
                )
            )
        built = _record(
            project_id=project_id,
            drawing_set_id=drawing_set_id,
            opening_class=klass,
            mark=mark,
            unit_type=code,
            floor=floor_label,
            room=room,
            schedule=schedule,
            per_unit=per_unit,
            applicable=applicable,
            status=status,
            issue_codes=issue_codes,
            source=source,
            plan=plan,
            units=unit_numbers,
            coverage=coverage,
        )
        if status == "excluded":
            built["exclusion_reason"] = "Opaque door. Glazing is not shown for this type."
            excluded.append(built)
        else:
            records.append(built)

    for door in doors:
        mark = str(door.get("tag") or door.get("mark") or "").upper()
        schedule = dictionary.get(("DOOR", mark))
        opaque = bool(schedule and schedule.get("opaque")) or schedule is None
        reason = (
            "Square door tag with no glazed schedule row. Counted as an opaque door and left out of the opening total."
            if schedule is None
            else "Opaque door on the door schedule."
            if opaque
            else ""
        )
        if schedule and not opaque:
            exceptions.append(
                exception_record(
                    project_id=project_id,
                    unit_or_mark=mark,
                    issue_code="SCOPE_UNCONFIRMED",
                    source_location=source,
                    quantity_affected=applicable,
                    decision_needed="This door type is glazed. Coverage is for the estimator, not an automatic shade.",
                )
            )
            records.append(
                _record(
                    project_id=project_id,
                    drawing_set_id=drawing_set_id,
                    opening_class="DOOR",
                    mark=mark,
                    unit_type=code,
                    floor=floor_label,
                    room=str(door.get("room") or ""),
                    schedule=schedule,
                    per_unit=1,
                    applicable=applicable,
                    status="provisional",
                    issue_codes=["SCOPE_UNCONFIRMED"],
                    source=source,
                    plan=plan,
                    units=unit_numbers,
                    coverage="scope_review",
                )
            )
            continue
        excluded.append(
            {
                **_record(
                    project_id=project_id,
                    drawing_set_id=drawing_set_id,
                    opening_class="DOOR",
                    mark=mark,
                    unit_type=code,
                    floor=floor_label,
                    room=str(door.get("room") or ""),
                    schedule=schedule,
                    per_unit=1,
                    applicable=applicable,
                    status="excluded",
                    issue_codes=[],
                    source=source,
                    plan=plan,
                    units=unit_numbers,
                    coverage="excluded",
                ),
                "exclusion_reason": reason,
            }
        )


def _record(
    *,
    project_id: str,
    drawing_set_id: str,
    opening_class: str,
    mark: str,
    unit_type: str,
    floor: str,
    room: str,
    schedule: Optional[dict[str, Any]],
    per_unit: int,
    applicable: int,
    status: str,
    issue_codes: list[str],
    source: str,
    plan: dict[str, Any],
    units: list[str],
    coverage: str,
) -> dict[str, Any]:
    schedule = schedule or {}
    extended = per_unit * applicable
    width = schedule.get("width_original") or ""
    height = schedule.get("height_original") or ""
    return {
        "project_id": project_id,
        "drawing_set_id": drawing_set_id,
        "opening_id": f"{formatting_key(unit_type)}:{opening_class}:{mark or 'untagged'}:{formatting_key(room)}",
        "opening_class": opening_class,
        "mark_raw": mark,
        "mark_normalized": mark,
        "unit_type_raw": unit_type,
        "unit_type_normalized": formatting_key(unit_type),
        "floor_or_variant": floor,
        "room_or_location": room,
        "width_original": width or None,
        "height_original": height or None,
        "dimension_basis": schedule.get("dimension_basis"),
        "width_inches": schedule.get("width_inches"),
        "height_inches": schedule.get("height_inches"),
        "count_per_unit": per_unit,
        "applicable_unit_count": applicable,
        "extended_quantity": extended,
        "coverage_scope": coverage,
        "review_status": status,
        "shade_quantity": None,
        "plan_sheet": source,
        "plan_view": "floor plan",
        "plan_bounding_box": None,
        "schedule_sheet": schedule.get("source_file") or "",
        "schedule_bounding_box": schedule.get("bounding_box"),
        "matrix_units": units,
        "drawing_revision": schedule.get("revision") or "",
        "source_file_hash": schedule.get("source_file_hash") or "",
        "dimension_conflicts": schedule.get("conflicts") or [],
        "issue_codes": list(dict.fromkeys(issue_codes)),
        "reviewer_decision": None,
        "windowTag": mark,
        "unitType": unit_type,
        "apartmentCount": applicable,
        "perApartment": per_unit,
        "quantity": extended,
        "unit": "EA",
        "floor": floor,
        "room": room,
        "areaSection": "Residential units",
        "category": "Architectural opening",
        "item": f"{mark or 'Untagged'} {opening_class.lower()} opening",
        "productKind": "Architectural opening",
        "width": width,
        "height": height,
        "widthInches": schedule.get("width_inches"),
        "heightInches": schedule.get("height_inches"),
        "sourceLocation": source,
        "calculationBasis": (
            f"{applicable} units of {unit_type} x {per_unit} {mark or 'untagged'} "
            f"{opening_class.lower()} opening on {source}. "
            "This is an architectural opening count, not a shade count."
        ),
        "dataSource": "drawing_set",
        "notes": plan.get("notes") or "",
        "pricingEligible": False,
    }


def _package(
    records: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    exceptions: list[dict[str, Any]],
    aliases: list[dict[str, str]],
    project_id: str,
) -> dict[str, Any]:
    def summing(status: str) -> int:
        return sum(int(row.get("extended_quantity") or 0) for row in records if row.get("review_status") == status)

    verified = summing("verified")
    provisional = summing("provisional")
    unresolved = summing("unresolved")
    lines = _aggregate([row for row in records if row.get("review_status") in {"verified", "provisional"}])
    unresolved_lines = _aggregate([row for row in records if row.get("review_status") == "unresolved"])
    unmatched = []
    for item in exceptions:
        if item["issue_code"] == "UNKNOWN_SUBTYPE" and item["source_location"] == "unit matrix":
            unmatched.append(item["unit_or_mark"])
    return {
        "lines": lines,
        "unresolvedLines": unresolved_lines,
        "records": records,
        "excluded": excluded,
        "exceptions": exceptions,
        "aliases": aliases,
        "total": verified + provisional,
        "releasedTotal": verified,
        "openingQuantity": verified,
        "provisionalQuantity": provisional,
        "unresolvedQuantity": unresolved,
        "shadeQuantity": None,
        "unmatched": unmatched,
        "project_id": project_id,
        "byStatus": {
            "verified": verified,
            "provisional": provisional,
            "unresolved": unresolved,
            "excluded": len(excluded),
        },
    }


def _aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per class, mark, and size, with the subtype breakdown kept on the line."""
    grouped: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for row in rows:
        key = (
            row.get("opening_class"),
            row.get("windowTag") or "",
            row.get("width") or "",
            row.get("height") or "",
        )
        if key not in grouped:
            grouped[key] = dict(row)
            grouped[key]["quantity"] = 0
            grouped[key]["extended_quantity"] = 0
            grouped[key]["_parts"] = []
            grouped[key]["_types"] = []
            order.append(key)
        bucket = grouped[key]
        bucket["quantity"] += int(row.get("quantity") or 0)
        bucket["extended_quantity"] += int(row.get("extended_quantity") or 0)
        part = row.get("calculationBasis") or ""
        if part:
            bucket["_parts"].append(part)
        unit_type = row.get("unitType") or ""
        if unit_type and unit_type not in bucket["_types"]:
            bucket["_types"].append(unit_type)
        if row.get("review_status") == "unresolved":
            bucket["review_status"] = "unresolved"
        elif row.get("review_status") == "provisional" and bucket.get("review_status") != "unresolved":
            bucket["review_status"] = "provisional"
    lines = []
    for key in order:
        line = grouped[key]
        parts = line.pop("_parts")
        types = line.pop("_types")
        line["breakdown"] = parts
        if len(types) > 1:
            line["unitType"] = ""
        line["calculationBasis"] = " ".join(parts)
        line["shade_quantity"] = None
        lines.append(line)
    return lines
