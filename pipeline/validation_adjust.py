"""Make validation scores understandable for workbook-driven estimates."""

from __future__ import annotations

from typing import Any, Optional


def adjust_validation_for_workbook(
    validation: dict,
    workbook: Optional[dict],
    takeoff: Optional[dict],
    estimation: Optional[dict] = None,
) -> dict:
    """Boost score when Matrix TOTAL matches take-off; add plain-language summary."""
    result = dict(validation or {})
    wb = workbook or {}
    takeoff = takeoff or {}
    estimation = estimation or {}

    auth = wb.get("authoritativeTotalShades")
    project = wb.get("projectShadeCount")
    if project is None:
        project = auth
    additional = int(wb.get("additionalShadeCount") or 0)
    takeoff_total = takeoff.get("totalShadeCount")
    marking_sum = sum(
        int(r.get("totalShades") or 0)
        for r in (wb.get("windowMatrixMarkings") or [])
        if r.get("marking")
    )
    # Alignment means the TAKE-OFF agrees with the workbook. The marking rows footing
    # to the TOTAL row only proves the matrix is internally consistent, so it is used
    # as a fallback when the take-off reported no total at all — never as a substitute
    # for a take-off total that disagrees.
    if project is None:
        matrix_aligned = False
    elif takeoff_total is not None:
        matrix_aligned = takeoff_total == project
    else:
        matrix_aligned = additional == 0 and marking_sum == auth

    issues = list(result.get("validationIssues") or result.get("issues") or [])
    result["validationIssues"] = issues
    checks = _deterministic_cross_checks(
        wb, takeoff, estimation, result.get("crossChecks") or []
    )
    result["crossChecks"] = checks

    # Confidence has to answer to the arithmetic, not just to the count. A price that
    # disagrees with the client's own bid is a real problem even when the count is right.
    failed = [
        c
        for c in checks
        if c.get("computed") and not c.get("advisory") and not c.get("match")
    ]
    for check in failed:
        issues.append(
            {
                "severity": "High",
                "issue": f"{check['item']} does not reconcile.",
                "detail": f"{check.get('documentValue')} vs {check.get('estimatedValue')}.",
                "recommendation": "Open the source sheet and reconcile before sending.",
            }
        )

    if matrix_aligned and not failed:
        score = int(result.get("confidenceScore") or 0)
        # Nothing was inferred: the count was read from the TOTAL row and every computed
        # check reconciles, so the floor is higher than a model-graded take-off earns.
        floor = 88 if _takeoff_is_deterministic(takeoff) else 82
        result["confidenceScore"] = max(score, floor)
        result["confidenceLevel"] = "High"
        if result.get("recommendation") == "Rejected":
            result["recommendation"] = "Needs Review"
        if result.get("readyToSendOffer") is not True:
            result["readyToSendOffer"] = score >= 70 or result["confidenceScore"] >= floor
        extra = ""
        if additional and auth is not None:
            extra = (
                f" Window Matrix TOTAL is {auth}. "
                f"{additional} more shades are in areas the matrix does not list."
            )
        result["userFriendlySummary"] = (
            f"Shade count is {project} units.{extra} "
            "Review sizes and pricing in the list below, then send or adjust."
        )
        result.setdefault("strengths", []).insert(
            0,
            (
                f"Take-off total is {project}: matrix TOTAL {auth} plus {additional} "
                "in areas the matrix does not list."
                if additional
                else f"Take-off total equals WINDOW MATRIX TOTAL row ({auth})."
            ),
        )
    elif failed:
        result["confidenceScore"] = min(int(result.get("confidenceScore") or 0) or 60, 65)
        result["confidenceLevel"] = "Medium"
        result["readyToSendOffer"] = False
        result["userFriendlySummary"] = (
            f"{len(failed)} number{'s' if len(failed) > 1 else ''} did not reconcile: "
            + "; ".join(c["item"] for c in failed)
            + ". Check these against the source sheets before sending."
        )
    elif auth is not None:
        result["userFriendlySummary"] = (
            f"Window Matrix shows {auth} shades; take-off shows {takeoff_total or '?'}. "
            "Open the shade list and reconcile before sending."
        )
    else:
        result["userFriendlySummary"] = result.get("overallAssessment") or (
            "Review the shade list and totals before sending to your client."
        )

    # Short checklist for the UI (max 4 items)
    checklist: list[str] = []
    if additional and project is not None:
        checklist.append(
            f"Project shades: {project} ({auth} on the matrix + {additional} outside it)"
            + (" ✓" if matrix_aligned else f" — take-off: {takeoff_total or '?'}")
        )
    elif auth is not None:
        checklist.append(
            f"Matrix TOTAL: {auth} shades"
            + (
                f" — take-off matches ✓"
                if matrix_aligned
                else f" — take-off: {takeoff_total or '?'}"
            )
        )
    checklist.append("Confirm width × height on a sample of units")
    checklist.append("Confirm total price matches your bid expectations")
    if result.get("readyToSendOffer"):
        checklist.append("Ready for final review and client email")
    else:
        checklist.append("Quick review recommended before sending")
    result["reviewChecklist"] = checklist[:4]

    return result


def _takeoff_is_deterministic(takeoff: dict) -> bool:
    """True when the take-off came from parsed workbook rows rather than a model."""
    return (takeoff.get("dataSource") or "") == "workbook_excel"


def _deterministic_cross_checks(
    wb: dict,
    takeoff: dict,
    estimation: dict,
    model_checks: list,
) -> list[dict[str, Any]]:
    """
    Put arithmetic cross-checks ahead of the model's prose checks.

    These rows are computed from the parsed workbook, so they are the ones a reviewer
    can trust without re-reading the spreadsheets. The model's own cross-checks are
    kept afterwards, deduplicated by item name.
    """
    counts = takeoff.get("workbookCounts") or {}
    auth = wb.get("authoritativeTotalShades")
    checks: list[dict[str, Any]] = []

    if auth is not None:
        project = wb.get("projectShadeCount")
        if project is None:
            project = auth
        extra = int(wb.get("additionalShadeCount") or 0)
        if extra:
            document = f"{auth} on the matrix + {extra} outside it = {project} EA"
            notes = (
                "The matrix TOTAL counts the sections printed on that sheet. "
                "Quantity sheets for other areas are added once."
            )
        else:
            document = f"{auth} EA (Matrix TOTAL)"
            notes = "The matrix TOTAL row counts the sections printed on that sheet."
        checks.append(
            {
                "item": "Total shade count vs WINDOW MATRIX TOTAL row",
                "documentValue": document,
                "estimatedValue": f"{takeoff.get('totalShadeCount')} EA (take-off)",
                "match": takeoff.get("totalShadeCount") == project,
                "notes": notes,
                "computed": True,
            }
        )
        if counts.get("markingSum"):
            checks.append(
                {
                    "item": "Matrix marking rows sum vs TOTAL row",
                    "documentValue": f"{counts['markingSum']} EA (Σ markings)",
                    "estimatedValue": f"{auth} EA (TOTAL row)",
                    "match": counts["markingSum"] == auth,
                    "notes": "A gap here means the matrix itself does not foot — check the sheet.",
                    "computed": True,
                }
            )
        if counts.get("blindQtySum"):
            project = wb.get("projectShadeCount") if wb.get("projectShadeCount") is not None else auth
            extra = int(wb.get("additionalShadeCount") or 0)
            checks.append(
                {
                    "item": "Blind QTY sheet rows vs TOTAL row",
                    "documentValue": f"{counts['blindQtySum']} EA (Σ QTY column)",
                    "estimatedValue": f"{project} EA (project shades)",
                    "match": counts["blindQtySum"] == project,
                    "notes": (
                        "Quantity sheets for areas on the matrix repeat that count. "
                        "Sheets for other areas are added once."
                        if extra
                        else "Quantity sheets often cover one unit type; a gap is expected "
                        "when the matrix multiplies by unit count."
                    ),
                    "computed": True,
                    # A gap is normal on per-unit-type sheets, so this row informs the
                    # reviewer without arguing that the estimate is wrong.
                    "advisory": True,
                }
            )

    reference = wb.get("referencePricing") or {}
    product = reference.get("grandTotal")
    client = reference.get("clientTotal")
    anchor = client if client is not None else product
    if anchor is not None and estimation.get("totalEstimate") is not None:
        try:
            delta = round(float(estimation["totalEstimate"]) - float(anchor), 2)
        except (TypeError, ValueError):
            delta = None
        uses_bid = (
            client is not None
            and product is not None
            and abs(float(client) - float(product)) > 1
        )
        checks.append(
            {
                "item": "Project total vs Bid Summary grand total",
                "documentValue": (
                    f"${float(anchor):,.2f} (Total Bid)"
                    if uses_bid
                    else f"${float(product if product is not None else anchor):,.2f} (Bid Summary)"
                ),
                "estimatedValue": f"${float(estimation['totalEstimate']):,.2f} (offer)",
                "match": delta == 0,
                "notes": (
                    (
                        f"Variance ${delta:,.2f} against the sheet Total Bid. "
                        f"Product sales price on the quoted tabs is ${float(product):,.2f}."
                    )
                    if delta is not None and uses_bid
                    else f"Variance ${delta:,.2f}."
                    if delta is not None
                    else "Variance not computable."
                ),
                "computed": True,
            }
        )

    seen = {str(c.get("item", "")).strip().lower() for c in checks}
    for check in model_checks:
        if isinstance(check, dict) and str(check.get("item", "")).strip().lower() not in seen:
            checks.append(check)
    return checks
