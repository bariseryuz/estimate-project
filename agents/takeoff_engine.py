"""
Agent 2 — Take-off Engine

Responsibilities:
  - Use the RAG index built by Agent 1
  - Retrieve material, quantity, and schedule sections
  - Perform a complete material take-off (count every item with unit)
  - Pass structured take-off list to Agent 3
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json
from agents.count_reconciliation import reconcile_takeoff
from domain.workbook_takeoff import (
    apply_workbook_precision,
    build_takeoff_from_workbook,
    workbook_has_structured_takeoff,
)
from utils.dimensions import enrich_takeoff_item
from domain.metric_guidelines import metric_guidelines_for_prompt
from domain.shade_roles import TAKEOFF_ENGINE_ROLE, TAKEOFF_RAG_QUERIES
from rag.rag_utils import retrieve_relevant

ProgressCallback = Callable[[str], Awaitable[None]]
DocumentStepCallback = Callable[..., Awaitable[None]]


async def run_takeoff_engine(
    context_parser_output: dict,
    vision_result: Optional[dict] = None,
    document_text: str = "",
    workbook_analysis: Optional[dict] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_document_step: Optional[DocumentStepCallback] = None,
) -> dict:
    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    embedded_chunks = context_parser_output.get("embedded_chunks") or []
    abbreviations = context_parser_output.get("abbreviations", [])
    document_type = context_parser_output.get("documentType", "")
    trade = context_parser_output.get("trade", "")

    abbrev_glossary = "\n".join(
        f"{a['term']}: {a['definition']}" for a in abbreviations
    ) or "(none provided)"

    wb = workbook_analysis or context_parser_output.get("workbook") or {}

    # Deterministic Excel path first: no retrieval, no LLM count, no reconciliation.
    if workbook_has_structured_takeoff(wb):
        await progress("Building take-off from parsed Excel (Matrix + Blind QTY)…")
        result = build_takeoff_from_workbook(wb)
        for item in result.get("takeoffItems") or []:
            if item.get("floor") and item.get("room") and not item.get("location"):
                item["location"] = f"{item['floor']}, {item['room']}"
            enrich_takeoff_item(item)
        if on_document_step:
            await _emit_takeoff_lines(on_document_step, result, label="Workbook line")
        await progress("Take-off complete (workbook authority).")
        return result

    if not embedded_chunks:
        raise ValueError(
            "No searchable document index and no structured workbook — cannot count shades."
        )

    wb_block = ""
    if wb.get("promptBlock"):
        auth = wb.get("authoritativeTotalShades")
        wb_block = f"""
--- STRUCTURED WORKBOOKS (any project; trust the header rows below) ---
{wb.get('guidance', '')}
{wb.get('promptBlock')}
{"REQUIRED TOTAL SHADES FROM WINDOW MATRIX: " + str(auth) if auth else ""}
"""

    vision = vision_result or context_parser_output.get("vision") or {}
    vision_block = ""
    if vision:
        import json

        vision_block = f"""
--- AI VISION PRE-COUNT (cross-check; prefer schedule when conflict) ---
Shades required: {vision.get('shadesRequired')}
Estimated total shades: {vision.get('estimatedTotalShades')}
Estimated total windows: {vision.get('estimatedTotalWindows')}
Catalogue matches: {json.dumps(vision.get('catalogueMatches', [])[:20], indent=2)}
Window openings (vision): {json.dumps(vision.get('windowOpenings', [])[:40], indent=2)}
Summary: {vision.get('catalogueSummary', '')}
"""

    await progress("Retrieving window openings & schedule data…")
    material_chunks = await retrieve_relevant(
        TAKEOFF_RAG_QUERIES["windows"],
        embedded_chunks,
        top_k=10,
    )

    await progress("Retrieving finish & room schedules…")
    schedule_chunks = await retrieve_relevant(
        TAKEOFF_RAG_QUERIES["schedules"],
        embedded_chunks,
        top_k=8,
    )

    await progress("Retrieving shade specification notes…")
    note_chunks = await retrieve_relevant(
        TAKEOFF_RAG_QUERIES["notes"],
        embedded_chunks,
        top_k=6,
    )

    material_context = "\n\n".join(c["text"] for c in material_chunks)
    schedule_context = "\n\n".join(c["text"] for c in schedule_chunks)
    note_context = "\n\n".join(c["text"] for c in note_chunks)

    if on_document_step:
        for label, chunks in (
            ("Window openings & schedule", material_chunks),
            ("Room / finish schedules", schedule_chunks),
            ("Shade specification notes", note_chunks),
        ):
            for i, chunk in enumerate(chunks[:4]):
                text = chunk.get("text") or ""
                await on_document_step(
                    message=f"{label} — passage {i + 1}",
                    heading=(text.split("\n", 1)[0] or label)[:120],
                    excerpt=text,
                    step_kind="takeoff_read",
                )

    await progress("Counting window shades…")

    result = await complete_json(
        TAKEOFF_ENGINE_ROLE,
        f"""Perform a WINDOW SHADE / BLIND take-off from ALL sections below.
Review every sheet/page represented in the text. Apply metric guidelines exactly.

{metric_guidelines_for_prompt()}

PROJECT ABBREVIATIONS:
{abbrev_glossary}
Document type: {document_type} | Trade: {trade}
{wb_block}
{vision_block}

--- WINDOW OPENINGS & SCHEDULE ---
{material_context}

--- ROOM / FINISH SCHEDULES ---
{schedule_context}

--- SHADE SPEC NOTES ---
{note_context}

Return ONLY valid JSON matching this schema:
{{
  "takeoffItems": [
    {{
      "id": "string",
      "windowTag": "string — e.g. W-101",
      "category": "string — e.g. Solar Shade, Blackout, Motorized Roller",
      "item": "string — shade product description",
      "description": "string",
      "quantity": number,
      "unit": "EA",
      "width": "string — REQUIRED when in source, e.g. 48 inches or 4'-0\\"",
      "height": "string — REQUIRED when in source, e.g. 72 inches",
      "floor": "string — level / building area, e.g. Level 12, LIVINGS",
      "room": "string — room name or unit type",
      "areaSection": "string — Matrix section e.g. LIVING AREAS, BEDROOMS",
      "mountType": "Inside Mount | Outside Mount | Unknown",
      "location": "string — combined location for display (floor, room)",
      "motorized": boolean,
      "productKind": "Shade | Blind | Screen | Other",
      "sourceLocation": "string — REQUIRED: exact sheet + row/tag, e.g. Blind QTY UNITS LIVINGS row 14",
      "calculationBasis": "string — REQUIRED: how qty was derived, e.g. '1 EA per tag A-101; Matrix marking sum'",
      "notes": "string"
    }}
  ],
  "totalShadeCount": number,
  "countShades": number,
  "countBlinds": number,
  "countScreens": number,
  "motorizedCount": number,
  "primarySource": "string — main schedule/section used for the count",
  "sourcesUsed": "string — list EVERY sheet name or PDF page reviewed",
  "countByType": [{{"type": "string", "count": number}}],
  "countByFloor": [{{"floor": "string", "count": number}}],
  "categories": ["string"],
  "totalItemCount": number,
  "summary": "string — e.g. 47 window shades across 3 floors, 12 motorized",
  "countMethodology": "string — plain English: which sheets/rows were summed and how TOTAL was verified"
}}""",
        temperature=0.1,
        max_tokens=8192,
    )

    result = await reconcile_takeoff(
        result,
        vision_result=vision,
        context=context_parser_output,
        workbook_analysis=wb,
        document_excerpt=document_text or _chunks_preview(material_context, schedule_context),
        on_progress=on_progress,
    )

    if wb:
        result["takeoffItems"] = apply_workbook_precision(
            wb,
            result.get("takeoffItems") or [],
        )
        if wb.get("authoritativeTotalShades") is not None:
            result.setdefault(
                "countMethodology",
                f"Shade total = WINDOW MATRIX TOTAL row ({wb['authoritativeTotalShades']} EA). "
                "Line qty/dimensions prefer Blind QTY UNITS + Matrix marking rows from Excel.",
            )

    for item in result.get("takeoffItems") or []:
        if item.get("floor") and item.get("room") and not item.get("location"):
            item["location"] = f"{item['floor']}, {item['room']}"
        enrich_takeoff_item(item)

    auth = wb.get("authoritativeTotalShades")
    if auth is not None and not result.get("reconciliationNotes"):
        if result.get("totalShadeCount") != auth:
            result.setdefault("reconciliationNotes", []).append(
                f"Adjusted total to WINDOW MATRIX authoritative count: {auth}"
            )
        result["totalShadeCount"] = auth
        result["workbookAuthority"] = "WINDOW MATRIX TOTAL row"

    if on_document_step:
        await _emit_takeoff_lines(on_document_step, result, label="Take-off line")

    await progress("Take-off complete.")
    return result


async def _emit_takeoff_lines(
    on_document_step: DocumentStepCallback,
    result: dict,
    *,
    label: str,
    limit: int = 14,
) -> None:
    """Stream the first take-off lines to the document walker with their source cells."""
    for item in (result.get("takeoffItems") or [])[:limit]:
        tag = item.get("windowTag") or item.get("id") or "Opening"
        loc = item.get("sourceLocation") or item.get("location") or ""
        await on_document_step(
            message=f"{label}: {tag}",
            heading=loc[:120] or tag,
            excerpt=(
                f"Qty {item.get('quantity')} EA · {item.get('width')} × {item.get('height')} · "
                f"{item.get('category') or item.get('item') or ''}"
            ).strip(),
            source_file=item.get("referenceFile") if isinstance(item.get("referenceFile"), str) else None,
            source_sheet=item.get("referenceSheet") if isinstance(item.get("referenceSheet"), str) else None,
            step_kind="takeoff_line",
        )


def _chunks_preview(*parts: str, limit: int = 15000) -> str:
    return "\n\n".join(parts)[:limit]
