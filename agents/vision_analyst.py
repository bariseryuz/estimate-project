"""
Agent 0 — Vision Analyst

Reads every PDF page / image, applies metric guidelines, and produces deduplicated counts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json, complete_text
from clients.settings import (
    get_vision_pages_per_request,
    get_vision_parallel_requests,
    get_vision_pdf_dpi,
    resolve_vision_page_count,
)
from domain.catalogue import catalogue_for_prompt
from domain.metric_guidelines import metric_guidelines_for_prompt
from domain.shade_roles import VISION_ANALYST_ROLE
from rag.pdf_pages import PageImage, prioritize_pdf_page_indices, render_pdf_pages

ProgressCallback = Callable[[str], Awaitable[None]]
DocumentStepCallback = Callable[..., Awaitable[None]]


async def run_vision_analyst(
    *,
    document_text: str,
    pdf_bytes: Optional[bytes] = None,
    page_texts: Optional[list[str]] = None,
    image_base64: Optional[str] = None,
    image_media_type: str = "image/jpeg",
    on_progress: Optional[ProgressCallback] = None,
    on_document_step: Optional[DocumentStepCallback] = None,
) -> dict:
    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    page_images: list[PageImage] = []
    total_pdf_pages = len(page_texts or [])
    all_pages_mode = False

    if pdf_bytes:
        limit = resolve_vision_page_count(total_pdf_pages or 1)
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pdf_pages = doc.page_count
        doc.close()
        limit = resolve_vision_page_count(total_pdf_pages)
        all_pages_mode = limit >= total_pdf_pages

        if all_pages_mode:
            await progress(f"Rendering all {total_pdf_pages} PDF page(s) for vision…")
            indices = list(range(1, total_pdf_pages + 1))
        else:
            await progress(
                f"Rendering {limit} of {total_pdf_pages} PDF pages (priority sheets)…"
            )
            indices = prioritize_pdf_page_indices(page_texts or [], max_pages=limit)

        page_images = render_pdf_pages(
            pdf_bytes,
            dpi=get_vision_pdf_dpi(),
            max_pages=0,
            page_numbers=indices,
        )
    elif image_base64:
        page_images = [
            {
                "page_number": 1,
                "media_type": image_media_type,
                "image_base64": image_base64,
            }
        ]

    if not page_images:
        await progress("No visual pages — using extracted text only.")
        if on_document_step:
            preview = (document_text or "").strip()[:900]
            await on_document_step(
                message="Excel / text project — skipping PDF vision",
                heading="Document text drives this estimate",
                excerpt=preview or "Workbook and schedule text only.",
                step_kind="text_only",
            )
        return {
            "pagesAnalyzed": 0,
            "totalPagesInFile": total_pdf_pages,
            "allPagesAnalyzed": False,
            "visualTranscript": "",
            "windowOpenings": [],
            "catalogueMatches": [],
            "estimatedTotalWindows": None,
            "estimatedTotalShades": None,
            "estimatedTotalBlinds": None,
            "shadesRequired": None,
            "catalogueSummary": "Text-only document (Excel/Word/CSV) — see take-off from schedule text.",
            "pageSummaries": [],
            "page_images": [],
            "confidenceNotes": [],
        }

    batch_size = get_vision_pages_per_request()
    parallel = get_vision_parallel_requests()
    batches = _chunk_pages(page_images, batch_size)

    await progress(
        f"Analyzing {len(page_images)} page(s) in {len(batches)} vision batch(es)…"
    )
    semaphore = asyncio.Semaphore(parallel)
    guidelines = metric_guidelines_for_prompt()

    async def analyze_batch(batch: list[PageImage]) -> dict:
        async with semaphore:
            page_nums = [p["page_number"] for p in batch]
            if on_document_step:
                await on_document_step(
                    message=f"Reading drawing pages {page_nums}",
                    heading=f"PDF pages {', '.join(str(p) for p in page_nums)}",
                    excerpt=(
                        "Looking for window tags, schedule tables, and shade callouts on these sheets."
                    ),
                    source_page=page_nums[0] if page_nums else None,
                    step_kind="page",
                )
            user_content: list = [
                {
                    "type": "text",
                    "text": (
                        f"Construction document pages {page_nums}. "
                        f"Apply these counting rules:\n{guidelines}\n\n"
                        "For EACH page: list every window tag, schedule row, or opening symbol. "
                        "Return plain text: page number headers, then tags, sizes, shade/blind callouts."
                    ),
                },
            ]
            for page in batch:
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{page['media_type']};base64,{page['image_base64']}",
                            "detail": "high",
                        },
                    }
                )
            transcript = await complete_text(
                VISION_ANALYST_ROLE,
                user_content,
                temperature=0.0,
                max_tokens=8000,
            )
            return {"pages": page_nums, "transcript": transcript.strip()}

    batch_results = await asyncio.gather(*(analyze_batch(b) for b in batches))
    merged_transcript = "\n\n".join(
        f"--- PAGES {r['pages']} ---\n{r['transcript']}"
        for r in batch_results
        if r.get("transcript")
    )

    if on_document_step and merged_transcript:
        await on_document_step(
            message="Vision extracted text from pages",
            heading="Merged vision transcript",
            excerpt=merged_transcript[:900],
            step_kind="vision_text",
        )

    catalogue_block = catalogue_for_prompt()
    existing_text = (document_text or "").strip()
    text_for_merge = existing_text[:20000] if existing_text else "(no embedded PDF text)"

    await progress("Reconciling all pages — dedupe tags, apply schedule authority…")

    structured = await complete_json(
        VISION_ANALYST_ROLE,
        f"""Full-document vision analysis. You MUST use every page transcript below.

{metric_guidelines_for_prompt()}

PRODUCT CATALOGUE:
{catalogue_block}

EXTRACTED TEXT (Excel/Word/PDF text layer — schedule authority):
{text_for_merge}

VISION BY PAGE (all pages analyzed: {len(page_images)}):
{merged_transcript}

Deduplicate by window TAG. Schedule rows beat plan symbol counts.
Sum line quantities for final metrics.

Return ONLY valid JSON:
{{
  "pagesAnalyzed": {len(page_images)},
  "totalPagesInFile": {total_pdf_pages or len(page_images)},
  "allPagesAnalyzed": {json.dumps(all_pages_mode)},
  "visualTranscript": "string",
  "windowOpenings": [
    {{
      "tag": "string",
      "width": "string",
      "height": "string",
      "room": "string",
      "floor": "string",
      "shadeType": "string",
      "productKind": "Shade | Blind | Screen | Other",
      "quantity": number,
      "sourcePage": number,
      "needsShade": boolean,
      "notes": "string"
    }}
  ],
  "catalogueMatches": [{{"sku": "string", "productName": "string", "quantity": number, "reason": "string"}}],
  "estimatedTotalWindows": number,
  "estimatedTotalShades": number,
  "estimatedTotalBlinds": number,
  "motorizedCount": number,
  "shadesRequired": boolean,
  "catalogueSummary": "string",
  "pageSummaries": [{{"page": number, "summary": "string", "openingCount": number}}],
  "confidenceNotes": ["string"]
}}""",
        temperature=0.05,
        max_tokens=8000,
    )

    structured["pagesAnalyzed"] = structured.get("pagesAnalyzed") or len(page_images)
    structured["totalPagesInFile"] = total_pdf_pages or len(page_images)
    structured["allPagesAnalyzed"] = structured.get("allPagesAnalyzed", all_pages_mode)
    structured["page_images"] = page_images
    if not structured.get("visualTranscript"):
        structured["visualTranscript"] = merged_transcript

    await progress("Vision analysis complete (all pages reconciled).")
    return structured


def _chunk_pages(pages: list[PageImage], size: int) -> list[list[PageImage]]:
    return [pages[i : i + size] for i in range(0, len(pages), size)]
