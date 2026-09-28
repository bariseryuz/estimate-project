"""
One small object that answers "do the numbers agree?" without reading every table.

`projectSummary` is the first thing the UI renders and the first thing a reviewer
should check. It deliberately repeats numbers that live elsewhere in the response so
a mismatch is visible in one place instead of being spread across three tabs.
"""

from __future__ import annotations

from typing import Any, Optional

#: matchStatus values, in order of how much attention they need.
MATCH_STATUS = ("match", "mismatch", "matrix_only", "no_matrix")


def build_project_summary(
    *,
    source_meta: Optional[dict],
    workbook: Optional[dict],
    takeoff: Optional[dict],
    estimation: Optional[dict],
    validation: Optional[dict],
    quantity_schedule: Optional[dict],
    workbook_fast_path: bool = False,
) -> dict[str, Any]:
    meta = source_meta or {}
    wb = workbook or {}
    takeoff = takeoff or {}
    estimation = estimation or {}
    validation = validation or {}
    qs = quantity_schedule or {}
    totals = qs.get("totals") or {}

    matrix_total = wb.get("authoritativeTotalShades")
    additional = int(wb.get("additionalShadeCount") or 0)
    project_total = wb.get("projectShadeCount")
    if project_total is None:
        project_total = matrix_total
    marking_sum = totals.get("matrixMarkingSum") or _marking_sum(wb)
    blind_sum = _blind_sum(wb)
    schedule_sum = totals.get("unitQuantity")
    takeoff_total = takeoff.get("totalShadeCount")

    counted = [n for n in (schedule_sum, takeoff_total, marking_sum, blind_sum) if n]
    status, status_note = _match_status(
        matrix_total, project_total, additional, takeoff_total, schedule_sum, counted
    )

    reference = wb.get("referencePricing") or {}
    grand_total = estimation.get("totalEstimate") or totals.get("grandTotal")
    client_total = reference.get("clientTotal")
    price_anchor = client_total if client_total is not None else reference.get("grandTotal")

    return {
        "projectName": wb.get("projectName") or (qs.get("methodology") or {}).get("projectName"),
        "matrixTotal": matrix_total,
        "additionalShadeCount": additional or None,
        "projectShadeCount": project_total,
        "matrixWindows": wb.get("authoritativeTotalWindows"),
        "markingSum": marking_sum,
        "blindQtySum": blind_sum,
        "takeoffTotal": takeoff_total,
        "scheduleLineSum": schedule_sum,
        "lineCount": totals.get("lineCount") or len(qs.get("lines") or []),
        "totalSquareFeet": totals.get("totalSquareFeet"),
        "grandTotal": grand_total,
        "currency": estimation.get("currency") or totals.get("currency") or "USD",
        "pricePerUnit": _price_per_unit(grand_total, takeoff_total or project_total or matrix_total),
        "priceSource": estimation.get("priceSource") or ("llm" if estimation else None),
        "referenceGrandTotal": reference.get("grandTotal"),
        "clientTotal": client_total,
        "referenceUnitRate": reference.get("unitRate"),
        "priceVariance": _price_variance(
            grand_total,
            price_anchor,
            "sheet Total Bid" if client_total is not None else "product sales price",
        ),
        "matchStatus": status,
        "matchNote": status_note,
        "countAuthority": takeoff.get("workbookAuthority")
        or takeoff.get("primarySource")
        or "Take-off engine",
        "route": "workbook_fast_path" if workbook_fast_path else "full_analysis",
        "confidenceScore": validation.get("confidenceScore"),
        "readyToSend": validation.get("readyToSendOffer"),
        "files": _files(meta, wb),
    }


def _match_status(
    matrix_total: Optional[int],
    project_total: Optional[int],
    additional: int,
    takeoff_total: Optional[int],
    schedule_sum: Optional[int],
    counted: list[int],
) -> tuple[str, str]:
    """
    The count to match is the project total: matrix TOTAL, plus shades in areas
    the matrix does not list. Comparing the take-off to the matrix alone flags a
    correct commons (or amenity) count as a mismatch.
    """
    if matrix_total is None:
        authority = None
    else:
        authority = project_total if project_total is not None else matrix_total
    if authority is None:
        if takeoff_total:
            return "no_matrix", (
                f"No WINDOW MATRIX TOTAL row was found; the count of {takeoff_total} comes "
                "from the take-off itself. Confirm it against your matrix."
            )
        return "no_matrix", "No authoritative shade count was found in the uploaded files."

    schedule_agrees = schedule_sum in (None, authority) or schedule_sum == authority
    outside = ""
    if additional and matrix_total is not None:
        outside = f" ({matrix_total} on the window matrix + {additional} outside it)"

    if takeoff_total == authority and schedule_agrees:
        return "match", (
            f"Take-off and schedule both total {authority} shades{outside}, "
            "matching the workbook count."
        )

    if takeoff_total == authority:
        basis = (
            f"{authority} shades{outside}"
            if additional
            else f"the WINDOW MATRIX TOTAL row ({authority} shades)"
        )
        return "matrix_only", (
            f"Project total follows {basis}. "
            f"The schedule lists {schedule_sum} unit(s), so some markings are rolled up "
            "rather than itemised."
        )

    others = ", ".join(str(n) for n in dict.fromkeys(counted) if n != authority)
    return "mismatch", (
        f"The workbook count is {authority} shades{outside} but the take-off shows "
        f"{takeoff_total or '?'}" + (f" (other counts seen: {others})" if others else "")
        + ". The matrix wins — reconcile before sending."
    )


def _marking_sum(wb: dict) -> Optional[int]:
    total = sum(
        int(r.get("totalShades") or 0)
        for r in (wb.get("windowMatrixMarkings") or [])
        if r.get("marking")
    )
    return total or None


def _blind_sum(wb: dict) -> Optional[int]:
    total = sum(int(l.get("quantity") or 0) for l in (wb.get("blindQtyLines") or []))
    return total or None


def _price_per_unit(total: Any, units: Any) -> Optional[float]:
    try:
        if total and units:
            return round(float(total) / float(units), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def _price_variance(
    estimate_total: Any,
    reference_total: Any,
    reference_name: str = "sheet total",
) -> Optional[dict[str, Any]]:
    """Signed difference between the offer and the total printed on the bid sheet."""
    try:
        estimate = float(estimate_total)
        reference = float(reference_total)
    except (TypeError, ValueError):
        return None
    if not reference:
        return None
    delta = round(estimate - reference, 2)
    return {
        "amount": delta,
        "percent": round(delta / reference * 100, 2),
        "formula": f"${estimate:,.2f} offer − ${reference:,.2f} {reference_name} = ${delta:,.2f}",
    }


def _files(meta: dict, wb: dict) -> list[dict[str, Any]]:
    """Every uploaded file with the role it played, so the UI can show provenance."""
    kinds = {f.get("name"): f for f in (wb.get("files") or [])}
    names = meta.get("fileNames") or ([meta.get("fileName")] if meta.get("fileName") else [])
    out: list[dict[str, Any]] = []
    for name in names:
        parsed = kinds.get(name) or {}
        out.append(
            {
                "name": name,
                "kind": parsed.get("kind") or "document",
                "sheets": parsed.get("sheets") or [],
                "role": _role_for_kind(parsed.get("kind")),
            }
        )
    for name, parsed in kinds.items():
        if name and name not in {f["name"] for f in out}:
            out.append(
                {
                    "name": name,
                    "kind": parsed.get("kind") or "document",
                    "sheets": parsed.get("sheets") or [],
                    "role": _role_for_kind(parsed.get("kind")),
                }
            )
    return out


def _role_for_kind(kind: Optional[str]) -> str:
    return {
        "window_matrix": "Shade count authority (TOTAL row) + Blind QTY dimensions",
        "material_summary": "Fabric and system selection",
        "bid_summary": "Reference pricing (dollars only)",
    }.get(kind or "", "Supporting document (text extraction)")
