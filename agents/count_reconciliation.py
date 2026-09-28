"""
Reconciliation pass — dedupe tags, apply metric guidelines, align totals with line items.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json
from domain.metric_guidelines import metric_guidelines_for_prompt
from domain.shade_roles import TAKEOFF_ENGINE_ROLE

ProgressCallback = Callable[[str], Awaitable[None]]


async def reconcile_takeoff(
    takeoff: dict,
    *,
    vision_result: Optional[dict] = None,
    context: Optional[dict] = None,
    workbook_analysis: Optional[dict] = None,
    document_excerpt: str = "",
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    if on_progress:
        await on_progress("Reconciling counts across all pages (metric guidelines)…")

    vision = vision_result or {}
    ctx = context or {}
    wb = workbook_analysis or {}
    items = takeoff.get("takeoffItems") or []

    import json

    workbook_block = wb.get("promptBlock") or ""
    auth_shades = wb.get("authoritativeTotalShades")

    prompt = f"""You are the final accuracy pass for a window treatment take-off.
Apply METRIC GUIDELINES strictly. Use window schedule as authority when present.

{metric_guidelines_for_prompt()}

{wb.get('guidance', '')}

STRUCTURED WORKBOOK DATA (highest priority for totals; headers identify each sheet):
{workbook_block}
{"MANDATORY: totalShadeCount must equal " + str(auth_shades) + " from WINDOW MATRIX TOTAL row unless you document why not." if auth_shades else ""}

CONTEXT:
Project: {ctx.get('projectName') or wb.get('projectName')} | Schedule location: {ctx.get('windowScheduleLocation')}
Reading guide: {ctx.get('readingGuide')}

VISION (all pages analyzed: {vision.get('pagesAnalyzed', '?')}):
Openings: {json.dumps(vision.get('windowOpenings', [])[:80], indent=2)}
Vision shade estimate: {vision.get('estimatedTotalShades')}

DRAFT TAKE-OFF (fix duplicates, wrong totals, misclassified blinds vs shades):
{json.dumps(items[:120], indent=2)}

DOCUMENT EXCERPT (schedule text):
{document_excerpt[:12000]}

Return ONLY valid JSON — corrected take-off with reconciled metrics:
{{
  "takeoffItems": [same schema as draft, each with accurate productKind and sourceLocation],
  "totalShadeCount": number,
  "countShades": number,
  "countBlinds": number,
  "countScreens": number,
  "motorizedCount": number,
  "primarySource": "string",
  "sourcesUsed": "string — all sheets/pages reviewed",
  "countByType": [{{"type": "string", "count": number}}],
  "countByFloor": [{{"floor": "string", "count": number}}],
  "categories": ["string"],
  "totalItemCount": number,
  "summary": "string",
  "reconciliationNotes": ["string — discrepancies fixed, schedule vs plan, etc."],
  "accuracyConfidence": number (0-100)
}}"""

    reconciled = await complete_json(
        TAKEOFF_ENGINE_ROLE,
        prompt,
        temperature=0.05,
        max_tokens=8000,
    )

    notes = reconciled.get("reconciliationNotes") or []
    if notes and vision:
        existing = list(vision.get("confidenceNotes") or [])
        vision["confidenceNotes"] = existing + notes[:5]

    return reconciled
