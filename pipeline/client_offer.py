"""Assemble a proposal-style client offer for the UI (like commercial quoting tools)."""

from __future__ import annotations

from typing import Any, Optional


def build_client_offer_package(
    estimation: Optional[dict],
    takeoff: Optional[dict],
    workbook: Optional[dict],
    validation: Optional[dict],
    context: Optional[dict],
    quantity_schedule: Optional[dict] = None,
) -> dict[str, Any]:
    est = estimation or {}
    offer = est.get("clientOffer") or {}
    wb = workbook or {}
    ctx = context or {}
    val = validation or {}

    project = wb.get("projectName") or ctx.get("projectName") or "Your project"
    total_shades = (
        offer.get("totalShades")
        or takeoff.get("totalShadeCount")
        or wb.get("authoritativeTotalShades")
    )
    total_price = offer.get("totalPrice") or est.get("totalEstimate")
    price_per = offer.get("pricePerShade")
    if total_price and total_shades and not price_per:
        try:
            price_per = round(float(total_price) / float(total_shades), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            price_per = None

    headline = offer.get("headline") or (
        f"Window Shade Proposal — {project}"
        + (f" — {total_shades} units" if total_shades else "")
    )

    narrative = offer.get("offerNarrative") or est.get("executiveSummary") or ""
    assumptions = offer.get("assumptions") or est.get("assumptions") or []
    validity = offer.get("validityNote") or "Pricing subject to field verification and final measurements."

    sources: list[str] = []
    if wb.get("files"):
        for f in wb["files"]:
            sources.append(f"{f.get('name')} ({f.get('kind', 'file')})")
    if wb.get("authoritativeTotalShades") is not None:
        sources.append(f"Window Matrix TOTAL row: {wb['authoritativeTotalShades']} shades")

    ready = val.get("readyToSendOffer", False)
    confidence = val.get("confidenceScore")

    email_lines = [
        f"Subject: {headline}",
        "",
        f"Dear Client,",
        "",
        narrative or (
            f"We are pleased to submit our proposal for window shades at {project}. "
            f"Based on your Window Matrix and project documents, we count "
            f"{total_shades or 'the scheduled'} shade units in scope."
        ),
        "",
    ]
    if total_shades is not None:
        email_lines.append(f"• Total shades in scope: {total_shades}")
    if total_price is not None:
        email_lines.append(f"• Total investment: ${total_price:,.2f}" if isinstance(total_price, (int, float)) else f"• Total investment: {total_price}")
    if price_per is not None:
        email_lines.append(f"• Average per unit: ${price_per:,.2f}" if isinstance(price_per, (int, float)) else f"• Average per unit: {price_per}")
    measurement_note = (wb.get("measurementReference") or {}).get("note") or ""
    if measurement_note:
        email_lines.extend(["", measurement_note])
    email_lines.extend(["", validity, "", "Regards,", "Direct Shades & Blinds"])

    qs = quantity_schedule or {}
    methodology = qs.get("methodology") or {}
    reference = wb.get("referencePricing") or {}
    price_stack = reference.get("priceStack") or []
    product_total = reference.get("grandTotal")

    return {
        "headline": headline,
        "projectName": project,
        "totalShades": total_shades,
        "totalPrice": total_price,
        "productPrice": product_total,
        "pricePerShade": price_per,
        "currency": est.get("currency") or "USD",
        "offerNarrative": narrative,
        "assumptions": assumptions,
        "validityNote": validity,
        "documentsRead": sources,
        "readyToSend": ready,
        "confidenceScore": confidence,
        "emailDraft": "\n".join(email_lines),
        "countMethodology": methodology.get("countRule"),
        "dimensionMethodology": methodology.get("dimensionRule"),
        "measurementNote": measurement_note,
        "scheduleLineCount": len(qs.get("lines") or []),
        "priceStack": price_stack,
        "alternateOptions": reference.get("alternateOptions") or [],
    }
