"""
Agent 3 — Estimation Agent

Responsibilities:
  - Receive the take-off list from Agent 2
  - Price it deterministically (catalogue math, Bid Summary reference) whenever the
    take-off came from structured workbooks
  - Otherwise retrieve cost/scope context from the RAG index and let the model price
  - Produce a fully costed estimate plus client-facing offer copy

The split matters: arithmetic is reproducible Python, prose is the model's job.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json
from domain.pricing import build_estimate_from_takeoff, get_pricing_policy, price_line
from domain.shade_roles import ESTIMATION_AGENT_ROLE, ESTIMATION_RAG_QUERIES
from rag.rag_utils import retrieve_relevant

ProgressCallback = Callable[[str], Awaitable[None]]


async def run_estimation_agent(
    context_parser_output: dict,
    takeoff_output: dict,
    workbook_analysis: Optional[dict] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    ctx = context_parser_output or {}
    wb = workbook_analysis or ctx.get("workbook") or {}

    if _takeoff_is_deterministic(takeoff_output):
        await progress("Pricing workbook take-off deterministically (catalogue + Bid Summary)…")
        result = build_estimate_from_takeoff(takeoff_output, wb)
        result["clientOffer"] = await _build_client_offer(
            result, takeoff_output, wb, ctx, progress
        )
        await progress("Estimation complete (deterministic pricing).")
        return result

    return await _run_llm_estimation(ctx, takeoff_output, wb, progress)


def _takeoff_is_deterministic(takeoff: Optional[dict]) -> bool:
    """True when the take-off came straight from parsed Excel, not the model."""
    return bool((takeoff or {}).get("dataSource") == "workbook_excel")


async def _run_llm_estimation(
    ctx: dict,
    takeoff_output: dict,
    wb: dict,
    progress: Callable[[str], Awaitable[None]],
) -> dict:
    embedded_chunks = ctx.get("embedded_chunks") or []
    document_type = ctx.get("documentType", "")
    takeoff_items = takeoff_output.get("takeoffItems", [])

    cost_context = scope_context = ""
    if embedded_chunks:
        await progress("Retrieving cost and pricing sections…")
        cost_chunks = await retrieve_relevant(
            ESTIMATION_RAG_QUERIES["pricing"],
            embedded_chunks,
            top_k=8,
        )
        await progress("Retrieving project scope…")
        scope_chunks = await retrieve_relevant(
            ESTIMATION_RAG_QUERIES["scope"],
            embedded_chunks,
            top_k=6,
        )
        cost_context = "\n\n".join(c["text"] for c in cost_chunks)
        scope_context = "\n\n".join(c["text"] for c in scope_chunks)

    # Trim take-off to avoid token overflow — send first 60 items
    takeoff_summary = json.dumps(takeoff_items[:60], indent=2)

    wb_block = ""
    if wb.get("promptBlock"):
        wb_block = f"""
WORKBOOKS (read like an estimator — Window Matrix = shade count authority):
{wb.get('guidance', '')}
{wb.get('promptBlock')[:12000]}
Official shade total from matrix: {wb.get('authoritativeTotalShades')}
"""

    reference_block = _reference_pricing_block(wb)

    await progress("Building client offer…")

    result = await complete_json(
        ESTIMATION_AGENT_ROLE,
        f"""You are preparing a proposal the contractor will EMAIL to their client.
Read the take-off and workbooks the way an experienced shade estimator would:
explain scope in plain language, state total shades clearly, and price confidently.

Build a CLIENT-READY window shade offer.

PROJECT: {ctx.get('projectName') or wb.get('projectName') or document_type}

TAKE-OFF (Agent 2):
{takeoff_summary}
{wb_block}
{reference_block}
DOCUMENT PRICING CONTEXT (Bid Summary / specs if present):
{cost_context}

PROJECT SCOPE:
{scope_context}

clientOffer.offerNarrative must be 2-4 professional sentences the contractor can paste into an email.
clientOffer.totalShades must match Window Matrix when authoritative total is provided.

Return ONLY valid JSON matching this schema:
{{
  "estimates": [
    {{
      "id": "string",
      "windowTag": "string",
      "category": "string",
      "item": "string",
      "quantity": number,
      "unit": "EA",
      "width": "string",
      "height": "string",
      "unitCost": number,
      "laborCost": number,
      "materialCost": number,
      "totalCost": number,
      "calculationFormula": "string — show math e.g. 2 EA × ($210 material + $55 labor) = $530",
      "notes": "string"
    }}
  ],
  "shadeSummary": {{
    "totalShades": number,
    "motorizedCount": number,
    "byType": [{{"type": "string", "count": number, "subtotal": number}}],
    "totalSquareFeet": number
  }},
  "clientOffer": {{
    "headline": "string — e.g. Window Shade Proposal — 47 Units",
    "totalShades": number,
    "totalPrice": number,
    "pricePerShade": number,
    "offerNarrative": "string — 2-3 sentences ready to email the client",
    "assumptions": ["string"],
    "validityNote": "string — e.g. Pricing valid 30 days, subject to field verification"
  }},
  "subtotal": number,
  "overhead": number,
  "profit": number,
  "totalEstimate": number,
  "currency": "USD",
  "assumptions": ["string"],
  "executiveSummary": "string"
}}""",
        temperature=0.2,
        max_tokens=8192,
    )

    _fill_missing_line_prices(result, takeoff_items)
    _fill_missing_totals(result)

    await progress("Estimation complete.")
    return result


def _reference_pricing_block(wb: dict) -> str:
    reference = wb.get("referencePricing") or {}
    if not reference:
        return ""
    tabs = ", ".join(
        f"{t.get('sheet')}: ${float(t.get('total') or 0):,.0f}"
        for t in (reference.get("tabs") or [])[:10]
    )
    return f"""
BID SUMMARY REFERENCE PRICING (dollars only — never change the shade count):
Source: {reference.get('source')}
Grand total: {reference.get('grandTotal')}
Blended unit rate: {reference.get('unitRate')} ({reference.get('unitRateBasis')})
Tabs: {tabs}
Align totalEstimate with this grand total when it is present, and say so in the narrative.
"""


def _fill_missing_line_prices(result: dict, takeoff_items: list[dict]) -> None:
    """Price any line the model left blank so no row shows an empty dollar column."""
    policy = get_pricing_policy()
    by_tag = {
        str(i.get("windowTag") or "").strip().upper(): i
        for i in takeoff_items
        if i.get("windowTag")
    }
    for line in result.get("estimates") or []:
        qty = max(1, int(line.get("quantity") or 1))
        material = line.get("materialCost")
        labor = line.get("laborCost")
        total = line.get("totalCost")

        if total is None and line.get("unitCost") is not None:
            total = round(float(line["unitCost"]) * qty, 2)
            line["totalCost"] = total

        if total is None:
            source = by_tag.get(str(line.get("windowTag") or "").strip().upper(), {})
            priced = price_line(
                {
                    "quantity": qty,
                    "widthInches": source.get("widthInches"),
                    "heightInches": source.get("heightInches"),
                    "squareFeetEach": source.get("squareFeet"),
                    "systemType": line.get("category") or line.get("item"),
                    "motorized": source.get("motorized"),
                },
                policy,
            )
            line["unitCost"] = priced["unitPrice"]
            line["materialCost"] = priced["unitMaterial"]
            line["laborCost"] = priced["unitLabor"]
            line["totalCost"] = priced["extendedPrice"]
            line["calculationFormula"] = priced["pricingFormula"]
            line["priceSource"] = "catalogue_fallback"
            line["catalogueSku"] = priced["catalogueSku"]
            continue

        if not line.get("calculationFormula"):
            if material is not None and labor is not None:
                line["calculationFormula"] = (
                    f"{qty} EA × (${float(material):,.2f} material + ${float(labor):,.2f} labor) "
                    f"= ${float(total):,.2f}"
                )
            elif line.get("unitCost") is not None:
                line["calculationFormula"] = (
                    f"{qty} EA × ${float(line['unitCost']):,.2f} = ${float(total):,.2f}"
                )


def _fill_missing_totals(result: dict) -> None:
    """Recompute subtotal/total from the lines when the model omitted them."""
    lines = result.get("estimates") or []
    line_sum = round(
        sum(float(l.get("totalCost") or 0) for l in lines),
        2,
    )
    if result.get("subtotal") is None and line_sum:
        result["subtotal"] = line_sum
        result.setdefault("assumptions", []).append(
            f"Subtotal recomputed as the sum of {len(lines)} priced lines (${line_sum:,.2f})."
        )
    if result.get("totalEstimate") is None:
        subtotal = float(result.get("subtotal") or line_sum or 0)
        overhead = float(result.get("overhead") or 0)
        profit = float(result.get("profit") or 0)
        if subtotal:
            result["totalEstimate"] = round(subtotal + overhead + profit, 2)
    result.setdefault("currency", "USD")


_OFFER_FALLBACK_VALIDITY = (
    "Pricing valid 30 days and subject to field verification of every opening."
)


async def _build_client_offer(
    estimate: dict,
    takeoff: dict,
    wb: dict,
    ctx: dict,
    progress: Callable[[str], Awaitable[None]],
) -> dict:
    """
    Ask the model for offer prose only — the numbers are already fixed.

    A slim prompt keeps the Excel path cheap, and a template fallback means an empty
    or malformed model response can never fail the run.
    """
    project = wb.get("projectName") or ctx.get("projectName") or "this project"
    total_shades = takeoff.get("totalShadeCount")
    total_price = estimate.get("totalEstimate")
    price_per = None
    if total_price and total_shades:
        price_per = round(float(total_price) / float(total_shades), 2)

    offer = {
        "headline": f"Window Shade Proposal — {project} — {total_shades} units",
        "totalShades": total_shades,
        "totalPrice": total_price,
        "pricePerShade": price_per,
        "offerNarrative": "",
        "assumptions": estimate.get("assumptions") or [],
        "validityNote": _OFFER_FALLBACK_VALIDITY,
    }

    await progress("Drafting the client-facing offer text…")
    try:
        prose = await complete_json(
            ESTIMATION_AGENT_ROLE,
            f"""Write the client-facing wording for a window shade proposal.
The numbers below are FINAL — do not change or recompute them.

Project: {project}
Total shades: {total_shades} (WINDOW MATRIX TOTAL row)
Total price: {total_price} USD
Average per shade: {price_per} USD
Count source: {takeoff.get('primarySource')}
Price source: {estimate.get('priceSource')}
Scope notes: {(ctx.get('scopeNotes') or '')[:400]}

Return ONLY this JSON:
{{
  "headline": "string — e.g. Window Shade Proposal — 47 Units",
  "offerNarrative": "string — 2-4 sentences the contractor can paste into an email",
  "assumptions": ["string — 3 to 5 short items"],
  "validityNote": "string — one sentence"
}}""",
            temperature=0.3,
            max_tokens=1200,
        )
    except Exception as exc:  # empty response, bad JSON, provider hiccup
        offer["offerNarrative"] = (
            f"We are pleased to submit our proposal for window shades at {project}. "
            f"Your Window Matrix shows {total_shades} shade units in scope, and our "
            f"price for the complete supply and installation is ${float(total_price or 0):,.0f}. "
            "Every line item in the attached schedule cites the sheet and row it came from."
        )
        offer["narrativeNote"] = f"Offer text generated locally ({exc})."
        return offer

    offer["headline"] = prose.get("headline") or offer["headline"]
    offer["offerNarrative"] = prose.get("offerNarrative") or ""
    if prose.get("assumptions"):
        offer["assumptions"] = [*prose["assumptions"], *offer["assumptions"]][:8]
    offer["validityNote"] = prose.get("validityNote") or offer["validityNote"]
    return offer
