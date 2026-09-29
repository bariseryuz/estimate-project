"""
Agent 4 — Validation Agent

Responsibilities:
  - Cross-reference all pipeline outputs against the source document
  - Identify errors, omissions, and inconsistencies
  - Produce a confidence score (0–100) and severity-ranked issue list
  - Give a final recommendation: Approved / Needs Review / Rejected
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json
from domain.shade_roles import VALIDATION_RAG_QUERIES, VALIDATION_ROLE
from rag.rag_utils import retrieve_relevant

ProgressCallback = Callable[[str], Awaitable[None]]


async def run_validation(
    context_parser_output: dict,
    takeoff_output: dict,
    estimation_output: dict,
    vision_result: Optional[dict] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    if takeoff_output.get("dataSource") == "drawing_set":
        await progress("Checking the unit-matrix total against the rows that were read.")
        return _drawing_set_validation(takeoff_output, estimation_output)

    embedded_chunks = context_parser_output.get("embedded_chunks") or []
    workbook = context_parser_output.get("workbook") or {}

    if embedded_chunks:
        await progress("Retrieving cross-check sections from source document…")
        xref_chunks = await retrieve_relevant(
            VALIDATION_RAG_QUERIES,
            embedded_chunks,
            top_k=10,
        )
        xref_context = "\n\n".join(c["text"] for c in xref_chunks)
    else:
        # Workbook fast path: no RAG index exists, so the parsed workbook facts
        # themselves are the source of truth to check the estimate against.
        await progress("Cross-checking against the parsed workbook facts…")
        xref_context = (workbook.get("promptBlock") or "")[:12000] or "(no source text indexed)"

    takeoff_summary = json.dumps(
        {
            "totalItemCount": takeoff_output.get("totalItemCount"),
            "categories": takeoff_output.get("categories"),
            "sampleItems": takeoff_output.get("takeoffItems", [])[:20],
            "summary": takeoff_output.get("summary"),
        },
        indent=2,
    )

    estimation_summary = json.dumps(
        {
            "totalEstimate": estimation_output.get("totalEstimate"),
            "subtotal": estimation_output.get("subtotal"),
            "overhead": estimation_output.get("overhead"),
            "profit": estimation_output.get("profit"),
            "currency": estimation_output.get("currency"),
            "executiveSummary": estimation_output.get("executiveSummary"),
            "assumptions": estimation_output.get("assumptions"),
            "topEstimates": estimation_output.get("estimates", [])[:15],
        },
        indent=2,
    )

    vision_summary = ""
    if vision_result:
        vision_summary = json.dumps(
            {
                "estimatedTotalShades": vision_result.get("estimatedTotalShades"),
                "estimatedTotalWindows": vision_result.get("estimatedTotalWindows"),
                "shadesRequired": vision_result.get("shadesRequired"),
                "catalogueSummary": vision_result.get("catalogueSummary"),
            },
            indent=2,
        )

    await progress("Running confidence analysis and cross-checks…")

    result = await complete_json(
        VALIDATION_ROLE,
        f"""Validate this window shade estimate before the contractor sends the offer.

AI VISION PRE-COUNT (Agent 0 — cross-check take-off):
{vision_summary or "(not available)"}

TAKE-OFF (Agent 2):
{takeoff_summary}

ESTIMATION & OFFER (Agent 3):
{estimation_summary}

SOURCE DOCUMENT CROSS-CHECK:
{xref_context}

Return ONLY valid JSON matching this schema:
{{
  "confidenceScore": number (0-100),
  "confidenceLevel": "Low | Medium | High | Very High",
  "readyToSendOffer": boolean,
  "validationIssues": [
    {{
      "severity": "Critical | Warning | Info",
      "category": "string",
      "issue": "string",
      "recommendation": "string"
    }}
  ],
  "crossChecks": [
    {{
      "item": "string — e.g. Total shade count vs window schedule",
      "documentValue": "string",
      "estimatedValue": "string",
      "match": boolean,
      "notes": "string"
    }}
  ],
  "strengths": ["string"],
  "limitations": ["string"],
  "overallAssessment": "string",
  "recommendation": "Approved | Needs Review | Rejected"
}}""",
        temperature=0.25,
        max_tokens=4096,
    )

    await progress("Validation complete.")
    return result


def _drawing_set_validation(takeoff: dict, estimation: dict) -> dict:
    """The count is already arithmetic. Do not send it through another model call."""
    printed = takeoff.get("matrixUnitsPrinted")
    read = takeoff.get("matrixUnitsRead")
    match = printed is not None and read == printed
    priced = estimation.get("totalEstimate") is not None
    summary = takeoff.get("summary") or "Shade count taken from the unit matrix and unit plans."
    issues = []
    if not match:
        issues.append(
            {
                "severity": "Warning",
                "category": "Count",
                "issue": f"Matrix rows read {read}. The sheet prints TOTAL UNITS {printed}.",
                "recommendation": "Check the unit-matrix image for the apartments that did not match.",
            }
        )
    if not priced:
        issues.append(
            {
                "severity": "Info",
                "category": "Price",
                "issue": "These drawings do not print a sell price.",
                "recommendation": "Add the price workbook when a bid is required.",
            }
        )
    return {
        "confidenceScore": 90 if match else 55,
        "confidenceLevel": "High" if match else "Medium",
        "readyToSendOffer": bool(match and priced),
        "validationIssues": issues,
        "crossChecks": [
            {
                "item": "Unit matrix total",
                "documentValue": "" if printed is None else str(printed),
                "estimatedValue": "" if read is None else str(read),
                "match": bool(match),
                "notes": summary,
            }
        ],
        "strengths": [summary],
        "limitations": ["Lobby and amenity shades are not on the unit plans."]
        + ([] if priced else ["No sell price is printed on these sheets."]),
        "overallAssessment": summary,
        "recommendation": "Approved" if match else "Needs Review",
        "validationSummary": summary,
        "userFriendlySummary": summary,
    }
