"""
Agent 1 — Context Parser

Responsibilities:
  - Chunk the document and build the shared RAG index (embedded_chunks are passed downstream)
  - Identify abbreviations, acronyms, and technical terms
  - Map the document structure (sections / divisions)
  - Produce a reading guide for the downstream agents
  - Uses GPT-4o vision when an image is provided (visual agent)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_json
from clients.settings import get_chat_model
from domain.shade_roles import (
    CONTEXT_PARSER_RAG_QUERIES,
    CONTEXT_PARSER_ROLE,
)
from rag.document_text import prepare_document_text
from rag.rag_utils import chunk_text, embed_chunks, retrieve_relevant

ProgressCallback = Callable[[str], Awaitable[None]]
DocumentStepCallback = Callable[..., Awaitable[None]]  # message=, heading=, excerpt=, …


async def run_context_parser(
    document_text: str,
    image_base64: Optional[str] = None,
    image_media_type: str = "image/jpeg",
    page_images: Optional[list] = None,
    vision_result: Optional[dict] = None,
    workbook_analysis: Optional[dict] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_document_step: Optional[DocumentStepCallback] = None,
) -> dict:
    async def progress(msg: str) -> None:
        if on_progress:
            await on_progress(msg)

    await progress("Preparing document text for embedding…")
    text_for_chunking = await prepare_document_text(
        document_text,
        image_base64,
        image_media_type,
        on_progress=on_progress,
    )

    if not text_for_chunking.strip() and workbook_analysis:
        text_for_chunking = (workbook_analysis.get("promptBlock") or "").strip()
        if workbook_analysis.get("guidance"):
            text_for_chunking = f"{workbook_analysis.get('guidance')}\n\n{text_for_chunking}".strip()

    if not text_for_chunking.strip():
        raise ValueError("No readable text could be extracted from the document.")

    await progress("Chunking document into natural RAG segments…")
    chunks = chunk_text(text_for_chunking, chunk_size=450, overlap=90)

    await progress(f"Embedding {len(chunks)} chunks for retrieval…")
    embedded_chunks = await embed_chunks(chunks)

    if on_document_step:
        preview = chunks[0]["text"] if chunks else ""
        await on_document_step(
            message=f"Indexed {len(chunks)} searchable segments from your document text",
            heading="Document index ready",
            excerpt=preview,
            step_kind="index",
        )

    await progress("Retrieving abbreviation-rich sections…")
    abbrev_chunks = await retrieve_relevant(
        CONTEXT_PARSER_RAG_QUERIES["abbreviations"],
        embedded_chunks,
        top_k=8,
    )
    if on_document_step:
        for i, chunk in enumerate(abbrev_chunks[:5]):
            text = chunk.get("text") or ""
            await on_document_step(
                message=f"Legend & abbreviations — passage {i + 1}",
                heading=(text.split("\n", 1)[0] or "Abbreviations")[:120],
                excerpt=text,
                step_kind="rag",
            )

    await progress("Retrieving window schedule & plan sections…")
    struct_chunks = await retrieve_relevant(
        CONTEXT_PARSER_RAG_QUERIES["structure"],
        embedded_chunks,
        top_k=8,
    )
    if on_document_step:
        for i, chunk in enumerate(struct_chunks[:6]):
            text = chunk.get("text") or ""
            await on_document_step(
                message=f"Schedule / plan — passage {i + 1}",
                heading=(text.split("\n", 1)[0] or "Window schedule")[:120],
                excerpt=text,
                step_kind="rag",
            )

    abbrev_context = "\n\n".join(c["text"] for c in abbrev_chunks)
    struct_context = "\n\n".join(c["text"] for c in struct_chunks)

    workbook_context = ""
    if workbook_analysis:
        files = workbook_analysis.get("files") or []
        names = ", ".join(f.get("name", "") for f in files[:5])
        workbook_context = (
            f"\n\nWORKBOOK SET (already parsed — use these facts):\n"
            f"Project: {workbook_analysis.get('projectName')}\n"
            f"Window Matrix total shades: {workbook_analysis.get('authoritativeTotalShades')}\n"
            f"Files: {names}\n"
            f"{(workbook_analysis.get('guidance') or '')[:800]}\n"
        )

    vision_context = ""
    if vision_result and vision_result.get("pagesAnalyzed"):
        vision_context = (
            f"\n\nVISION ANALYST (pre-count):\n"
            f"Estimated shades: {vision_result.get('estimatedTotalShades')}\n"
            f"Summary: {(vision_result.get('catalogueSummary') or '')[:400]}\n"
        )

    model_label = get_chat_model()
    await progress(f"Running {model_label} context analysis (visual agent)…")

    text_prompt = f"""Analyze this construction project document for WINDOW SHADE estimation.
Return a structured JSON context report that helps downstream agents count shades.

ABBREVIATIONS & LEGEND CONTEXT:
{abbrev_context}

WINDOW SCHEDULE / PLAN CONTEXT:
{struct_context}
{workbook_context}
{vision_context}

Keep every string field SHORT (max 120 characters). Return ONLY compact valid JSON (no markdown):
{{
  "documentType": "string — e.g. Architectural Drawings, Window Schedule, Interior Design Set…",
  "trade": "Window Treatments / Shades",
  "projectName": "string or null",
  "windowScheduleLocation": "string — sheet number or section where window schedule lives",
  "shadeSpecification": "string — shade type, mount, motor, fabric requirements from spec",
  "scopeNotes": "string — inclusions, exclusions, NIC, by others",
  "abbreviations": [{{"term": "string", "definition": "string"}}],
  "documentStructure": [{{"section": "string", "description": "string"}}],
  "readingGuide": "string — how to count shades on THIS project specifically",
  "keyFindings": ["string — e.g. total windows on schedule, motorized count, missing dims"]
}}"""

    vision_pages = page_images or []
    if not vision_pages and image_base64:
        vision_pages = [
            {"page_number": 1, "media_type": image_media_type, "image_base64": image_base64}
        ]

    if vision_pages:
        user_content: list | str = [{"type": "text", "text": text_prompt}]
        for page in vision_pages[:4]:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{page['media_type']};base64,{page['image_base64']}",
                        "detail": "high",
                    },
                }
            )
    else:
        user_content = text_prompt

    try:
        result = await complete_json(
            CONTEXT_PARSER_ROLE,
            user_content,
            temperature=0.1,
            max_tokens=8192,
        )
    except (ValueError, json.JSONDecodeError):
        await progress("Retrying context analysis with compact JSON…")
        retry_prompt = (
            text_prompt
            + "\n\nIMPORTANT: Return minimal valid JSON only. "
            "Max 5 abbreviations, 5 documentStructure items, 3 keyFindings. "
            "No newlines inside strings."
        )
        result = await complete_json(
            CONTEXT_PARSER_ROLE,
            retry_prompt,
            temperature=0.05,
            max_tokens=8192,
        )

    if workbook_analysis and workbook_analysis.get("authoritativeTotalShades") is not None:
        result.setdefault("projectName", workbook_analysis.get("projectName"))
        result["windowScheduleLocation"] = result.get("windowScheduleLocation") or "WINDOW MATRIX sheet"
        findings = list(result.get("keyFindings") or [])
        total = workbook_analysis["authoritativeTotalShades"]
        tag = f"Window Matrix TOTAL row: {total} shades"
        if tag not in findings:
            findings.insert(0, tag)
        result["keyFindings"] = findings[:8]

    await progress("Context parsing complete.")

    # embedded_chunks and raw_chunks are passed to downstream agents
    return {
        **result,
        "embedded_chunks": embedded_chunks,
        "raw_chunks": chunks,
        "vision": vision_result,
        "workbook": workbook_analysis,
    }
