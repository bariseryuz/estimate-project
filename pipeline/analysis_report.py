"""
Build a client-facing analysis / estimation detail report (where & how we counted).
"""

from __future__ import annotations

from typing import Any, Optional


def build_analysis_detail(
    source_meta: Optional[dict],
    vision: Optional[dict],
    context: Optional[dict],
    takeoff: Optional[dict],
    estimation: Optional[dict],
    validation: Optional[dict],
) -> dict:
    source_meta = source_meta or {}
    vision = vision or {}
    context = context or {}
    takeoff = takeoff or {}
    estimation = estimation or {}
    validation = validation or {}

    ingest = dict(source_meta.get("ingest") or {})
    if vision.get("analyzedPageNumbers"):
        ingest["visionPagesAnalyzed"] = vision["analyzedPageNumbers"]
    items = takeoff.get("takeoffItems") or []

    shade_count = takeoff.get("countShades")
    blind_count = takeoff.get("countBlinds")
    if shade_count is None or blind_count is None:
        shade_count, blind_count = _count_kinds(items)

    total_units = (
        takeoff.get("totalShadeCount")
        or takeoff.get("totalItemCount")
        or (shade_count + blind_count if shade_count + blind_count else None)
        or vision.get("estimatedTotalShades")
    )

    primary_source = _primary_source(context, ingest, vision, takeoff)
    steps = _analysis_steps(source_meta, ingest, vision, context, takeoff, estimation, validation)
    item_sources = _item_sources(items, vision)

    offer = estimation.get("clientOffer") or {}
    return {
        "fileName": source_meta.get("fileName"),
        "fileType": source_meta.get("sourceFormat"),
        "fileSizeBytes": source_meta.get("fileSizeBytes"),
        "ingest": ingest,
        "counts": {
            "totalShadesAndBlinds": total_units,
            "shades": shade_count,
            "blinds": blind_count,
            "screens": takeoff.get("countScreens") or 0,
            "byType": takeoff.get("countByType") or [],
            "byFloor": takeoff.get("countByFloor") or [],
            "visionEstimate": vision.get("estimatedTotalShades"),
            "finalTakeoff": takeoff.get("totalShadeCount"),
        },
        "howWeCounted": (
            takeoff.get("countMethodology")
            or takeoff.get("summary")
            or vision.get("catalogueSummary")
            or ""
        ),
        "primarySource": primary_source,
        "windowScheduleLocation": context.get("windowScheduleLocation"),
        "readingGuide": context.get("readingGuide"),
        "analysisSteps": steps,
        "lineItemSources": item_sources,
        "validationNote": validation.get("overallAssessment"),
        "confidenceScore": validation.get("confidenceScore"),
        "readyToSendOffer": validation.get("readyToSendOffer"),
        "totalEstimate": estimation.get("totalEstimate"),
        "offerHeadline": offer.get("headline"),
    }


def _count_kinds(items: list[dict]) -> tuple[int, int]:
    shades = blinds = 0
    for it in items:
        kind = (it.get("productKind") or it.get("category") or "").lower()
        qty = int(it.get("quantity") or 1)
        if "blind" in kind:
            blinds += qty
        elif "shade" in kind or "screen" in kind or "roller" in kind or "solar" in kind:
            shades += qty
        else:
            shades += qty
    return shades, blinds


def _primary_source(
    context: dict,
    ingest: dict,
    vision: dict,
    takeoff: dict,
) -> str:
    loc = context.get("windowScheduleLocation")
    if loc:
        return str(loc)

    sheets = ingest.get("sheets")
    if sheets:
        schedule_sheets = [
            s for s in sheets if _looks_like_schedule_name(str(s))
        ]
        if schedule_sheets:
            return f"Excel sheet(s): {', '.join(schedule_sheets)}"
        return f"Excel sheet(s): {', '.join(sheets[:5])}"

    vision_pages = [
        p.get("page")
        for p in (vision.get("pageSummaries") or [])
        if p.get("page")
    ]
    if vision_pages:
        return f"PDF/drawing pages (vision): {', '.join(map(str, vision_pages[:12]))}"

    analyzed = ingest.get("visionPagesAnalyzed")
    if analyzed:
        return f"PDF pages (vision): {', '.join(map(str, analyzed))}"

    pages_text = ingest.get("pagesWithText")
    if pages_text:
        return f"PDF pages (text): {', '.join(map(str, pages_text[:12]))}"

    if takeoff.get("primarySource"):
        return str(takeoff["primarySource"])

    return "Document text / RAG sections (see analysis steps below)"


def _looks_like_schedule_name(name: str) -> bool:
    lower = name.lower()
    return any(k in lower for k in ("window", "schedule", "opening", "shade", "ws"))


def _analysis_steps(
    source_meta: dict,
    ingest: dict,
    vision: dict,
    context: dict,
    takeoff: dict,
    estimation: dict,
    validation: dict,
) -> list[dict]:
    steps: list[dict] = []

    steps.append(
        {
            "step": 1,
            "stage": "File read",
            "where": _ingest_where(source_meta, ingest),
            "method": ingest.get("readMethod") or "File parser",
            "result": f"{ingest.get('characterCount', 0):,} characters extracted"
            if ingest.get("characterCount")
            else "Content loaded for AI analysis",
        }
    )

    pages = vision.get("pagesAnalyzed") or 0
    if pages:
        page_list = ingest.get("visionPagesAnalyzed") or [
            p.get("page") for p in (vision.get("pageSummaries") or []) if p.get("page")
        ]
        total_in_file = vision.get("totalPagesInFile") or ingest.get("pageCount")
        all_done = vision.get("allPagesAnalyzed")
        where = f"Pages {', '.join(map(str, page_list))}" if page_list else f"{pages} page(s)"
        if total_in_file and all_done:
            where = f"All {total_in_file} PDF pages analyzed"
        elif total_in_file:
            where = f"{pages} of {total_in_file} PDF pages"
        steps.append(
            {
                "step": 2,
                "stage": "AI vision",
                "where": where,
                "method": "Gemini vision — every page, metric guidelines, tag dedupe",
                "result": (
                    f"{vision.get('estimatedTotalShades', '?')} shades · "
                    f"{vision.get('estimatedTotalBlinds', '?')} blinds · "
                    f"{vision.get('estimatedTotalWindows', '?')} windows"
                ),
            }
        )
    elif ingest.get("readMethod", "").lower().startswith("image"):
        steps.append(
            {
                "step": 2,
                "stage": "AI vision",
                "where": "Uploaded image",
                "method": "Gemini vision OCR + opening detection",
                "result": vision.get("catalogueSummary") or "Visual read complete",
            }
        )

    steps.append(
        {
            "step": len(steps) + 1,
            "stage": "Context mapping",
            "where": context.get("windowScheduleLocation")
            or "Abbreviations + window schedule sections (RAG)",
            "method": "Context parser — document structure & reading guide",
            "result": context.get("documentType") or "Structure mapped",
        }
    )

    steps.append(
        {
            "step": len(steps) + 1,
            "stage": "Take-off count",
            "where": takeoff.get("sourcesUsed")
            or context.get("windowScheduleLocation")
            or "Retrieved schedule + spec chunks (RAG)",
            "method": "Take-off engine — line items with source references",
            "result": takeoff.get("summary") or f"{takeoff.get('totalShadeCount', '?')} units",
        }
    )

    steps.append(
        {
            "step": len(steps) + 1,
            "stage": "Pricing & offer",
            "where": "Take-off line items + catalogue list prices",
            "method": "Estimation agent",
            "result": f"Total {estimation.get('totalEstimate')}" if estimation.get("totalEstimate") else "Offer built",
        }
    )

    steps.append(
        {
            "step": len(steps) + 1,
            "stage": "Validation",
            "where": "Vision pre-count vs take-off vs source text",
            "method": "Validation agent cross-check",
            "result": (
                f"{validation.get('confidenceScore', '?')}/100 — "
                f"{validation.get('recommendation', '')}"
            ),
        }
    )

    return steps


def _ingest_where(source_meta: dict, ingest: dict) -> str:
    name = source_meta.get("fileName") or "upload"
    sheets = ingest.get("sheets")
    if sheets:
        return f"{name} — sheets: {', '.join(sheets[:8])}"
    if ingest.get("pageCount"):
        return f"{name} — {ingest['pageCount']} PDF page(s)"
    if ingest.get("slideCount"):
        return f"{name} — {ingest['slideCount']} slide(s)"
    return name


def _item_sources(items: list[dict], vision: dict) -> list[dict]:
    out: list[dict] = []
    for it in items[:100]:
        out.append(
            {
                "windowTag": it.get("windowTag"),
                "productKind": it.get("productKind") or it.get("category"),
                "item": it.get("item"),
                "quantity": it.get("quantity"),
                "location": it.get("location"),
                "sourceLocation": it.get("sourceLocation") or "See take-off notes",
            }
        )
    if not out and vision.get("windowOpenings"):
        for w in vision["windowOpenings"][:50]:
            out.append(
                {
                    "windowTag": w.get("tag"),
                    "productKind": w.get("shadeType"),
                    "item": w.get("shadeType"),
                    "quantity": w.get("quantity", 1),
                    "location": w.get("room"),
                    "sourceLocation": f"Vision — page {w.get('sourcePage', '?')}",
                }
            )
    return out
