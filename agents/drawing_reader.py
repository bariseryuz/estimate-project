"""
Read the sheets that decide how many shades a project has before construction.

The unit matrix lists every apartment and its type. Each unit plan shows the
glazed openings in that type. The shade count is type count times shade
openings. A window schedule that does not print quantities is not used as a count.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from clients.llm import complete_json
from domain.drawing_set import combine_counts, merge_unit_rows

ProgressCallback = Callable[[str], Awaitable[None]]

_TILE_PROMPT = """This image is one crop of a unit-matrix sheet.
The columns are UNIT #, UNIT TYPE, DESCRIPTION.
Copy a row only when UNIT # is an apartment number such as 201, 415, or LW-103, and UNIT TYPE is a plan code such as A-2, B-1A, or C-3.
DESCRIPTION (1BR/1BATH, 2BR+2BATH, 3BR+2BATH) is not the type. Do not use it as the type.
Skip TOTAL lines, bedroom-mix subtotals (1+1, 2+2, 3+2), and any unit marked NOT USED.
qty is the # OF UNITS column. Use 1 when that column is blank or shows 1.
If this crop prints TOTAL UNITS as a count of apartments, put that integer in printedTotals.
Do not invent rows.
Return JSON:
{"units": [{"unit": "201", "type": "B-4", "qty": 1}], "printedTotals": []}"""

_PLAN_PROMPT = """Each image is one residential unit-plan sheet. The line before the image is the file name.
Count only the floor plan, not the interior elevations or the kitchen blow-ups.
windows: every glazed window, including bathrooms. Do not count doors or sliding glass doors.
shades: glazed windows in living, dining, and bedrooms only. Bathroom windows, doors, storefront, and exterior aluminum sunshades are not shades.
blinds: only if the sheet prints the word blind. Otherwise 0.
Return JSON:
{"sheets": [{"file": "exact file name", "unitType": "A-1a", "windows": 0, "shades": 0, "blinds": 0, "notes": ""}]}"""


async def read_drawing_set(
    sheets: list[dict[str, Any]],
    *,
    on_progress: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    async def progress(message: str) -> None:
        if on_progress:
            await on_progress(message)

    matrix = [sheet for sheet in sheets if sheet.get("role") == "unit_matrix"]
    plans = [sheet for sheet in sheets if sheet.get("role") == "unit_plan"]
    measured = [sheet for sheet in plans if isinstance(sheet.get("openings"), dict)]
    unread = [sheet for sheet in plans if sheet not in measured and sheet.get("imageBase64")]
    await progress(
        f"Reading the unit matrix in {len(matrix)} parts. "
        f"Unit plans measured from drawn window tags: {len(measured)}."
        + (f" {len(unread)} plans still need a visual read." if unread else "")
    )

    matrix_rows = await _read_tiles(matrix, progress)
    merged = merge_unit_rows(matrix_rows)
    plan_rows = [_plan_from_openings(sheet) for sheet in measured]
    if unread:
        plan_rows.extend(await _read_plans(unread, progress))
    combined = combine_counts(merged["types"], plan_rows)

    window_total = 0
    blind_total = 0
    for entry in merged["types"]:
        plan = _chosen_plan(str(entry.get("type") or ""), plan_rows)
        if not plan:
            continue
        try:
            count = int(entry.get("count") or 0)
            window_total += count * int(plan.get("windows") or 0)
            blind_total += count * int(plan.get("blinds") or 0)
        except (TypeError, ValueError):
            continue

    notes = []
    printed = merged.get("printedTotal")
    transcribed = merged.get("transcribedUnits") or 0
    if printed is not None and transcribed != printed:
        notes.append(
            f"The matrix prints TOTAL UNITS {printed}. The rows that could be read add up to {transcribed}."
        )
    elif printed is not None:
        notes.append(f"Unit rows add up to the printed TOTAL UNITS of {printed}.")
    if combined["unmatched"]:
        notes.append("No matching unit plan for: " + ", ".join(combined["unmatched"]) + ".")
    notes.append(
        "Each shade is a window tag drawn on the unit floor plan, in living, dining, bedroom, den, or kitchen. "
        "Square door tags, sliding doors, storefront, and exterior sunshades are not included. "
        "A bathroom window is counted as a window and not as a shade."
    )
    notes.append("Lobby and amenity shades are not on the unit plans, so they are not in this total.")

    unit_label = f"{printed} units" if printed is not None else f"{transcribed} units"
    summary = (
        f"{combined['total']} window shades, {window_total} windows, {blind_total} blinds, across {unit_label}."
    )
    methodology = (
        "Unit-matrix rows give the count of each unit type. "
        "Each type is multiplied by the shade openings on its unit plan. "
        + " ".join(notes)
    )
    lines = []
    for line in combined["lines"]:
        line["calculationBasis"] = line["calculationBasis"] + ". " + methodology
        lines.append(line)

    takeoff = {
        "takeoffItems": lines,
        "totalShadeCount": combined["total"],
        "countShades": combined["total"],
        "countBlinds": blind_total,
        "countScreens": 0,
        "motorizedCount": 0,
        "windowCount": window_total,
        "primarySource": "Unit matrix and unit plans",
        "sourcesUsed": ", ".join(dict.fromkeys(sheet["file"] for sheet in matrix + plans)),
        "summary": summary,
        "countMethodology": methodology,
        "dataSource": "drawing_set",
        "totalItemCount": len(lines),
        "matrixUnitsRead": transcribed,
        "matrixUnitsPrinted": printed,
    }
    return {
        "pagesAnalyzed": len({sheet["file"] for sheet in matrix + plans}),
        "totalPagesInFile": len({sheet["file"] for sheet in sheets}),
        "allPagesAnalyzed": False,
        "estimatedTotalShades": combined["total"],
        "estimatedTotalWindows": window_total,
        "estimatedTotalBlinds": blind_total,
        "shadesRequired": True if combined["total"] else None,
        "windowOpenings": [
            {"tag": line["windowTag"], "quantity": line["quantity"], "location": line["sourceLocation"]}
            for line in lines
        ],
        "catalogueSummary": summary,
        "confidenceNotes": notes,
        "drawingTakeoff": takeoff,
        "totalUnits": printed if printed is not None else transcribed,
    }


def _plan_from_openings(sheet: dict[str, Any]) -> dict[str, Any]:
    from domain.drawing_set import type_from_filename

    openings = sheet.get("openings") or {}
    shades = int(openings.get("shades") or 0)
    return {
        "file": sheet.get("file") or "",
        "unitType": type_from_filename(str(sheet.get("file") or "")),
        "windows": int(openings.get("windows") or 0),
        "shades": shades,
        "shadeOpenings": shades,
        "blinds": int(openings.get("blinds") or 0),
        "notes": openings.get("notes") or "",
    }


def _chosen_plan(type_code: str, plan_rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    from domain.drawing_set import norm_type, type_from_filename

    key = norm_type(type_code)
    exact = [row for row in plan_rows if norm_type(str(row.get("unitType") or "")) == key]
    if exact:
        return exact[0]
    named = [
        row
        for row in plan_rows
        if key and norm_type(type_from_filename(str(row.get("file") or ""))) == key
    ]
    return named[0] if named else None


async def _read_tiles(sheets: list[dict[str, Any]], progress: ProgressCallback) -> list[dict[str, Any]]:
    if not sheets:
        return []
    semaphore = asyncio.Semaphore(3)
    payloads: list[dict[str, Any]] = []

    async def one(sheet: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await complete_json(
                    "You transcribe unit-matrix tables. You copy only rows you can read.",
                    [
                        {"type": "text", "text": f"File: {sheet['file']} {sheet.get('part') or ''}\n{_TILE_PROMPT}"},
                        _image_block(sheet),
                    ],
                    temperature=0.0,
                    max_tokens=8000,
                )
            except Exception:
                return {}
            return result if isinstance(result, dict) else {}

    done = 0
    tasks = [asyncio.create_task(one(sheet)) for sheet in sheets]
    for task in asyncio.as_completed(tasks):
        payloads.append(await task)
        done += 1
        await progress(f"Unit matrix parts read: {done} of {len(sheets)}.")
    return payloads


async def _read_plans(sheets: list[dict[str, Any]], progress: ProgressCallback) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    batches = [sheets[index : index + 2] for index in range(0, len(sheets), 2)]
    semaphore = asyncio.Semaphore(3)

    async def one_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with semaphore:
            content: list[dict[str, Any]] = [{"type": "text", "text": _PLAN_PROMPT}]
            for sheet in batch:
                content.append({"type": "text", "text": f"File: {sheet['file']}"})
                content.append(_image_block(sheet))
            try:
                result = await complete_json(
                    "You count windows on residential unit plans. You do not count doors or sunshades.",
                    content,
                    temperature=0.0,
                    max_tokens=4000,
                )
            except Exception:
                return []
            found = result.get("sheets") if isinstance(result, dict) else None
            if not isinstance(found, list):
                return []
            cleaned = []
            for row in found:
                if not isinstance(row, dict):
                    continue
                if row.get("shadeOpenings") is None and row.get("shades") is not None:
                    row["shadeOpenings"] = row.get("shades")
                cleaned.append(row)
            return cleaned

    done = 0
    tasks = [asyncio.create_task(one_batch(batch)) for batch in batches]
    for task in asyncio.as_completed(tasks):
        rows.extend(await task)
        done += 1
        await progress(f"Unit plans read: {min(done * 2, len(sheets))} of {len(sheets)}.")
    return rows


def _image_block(sheet: dict[str, Any]) -> dict[str, Any]:
    mime = sheet.get("mediaType") or "image/jpeg"
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{sheet['imageBase64']}"},
    }
