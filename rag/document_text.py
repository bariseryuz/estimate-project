"""Extract natural text from documents for RAG embedding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from clients.llm import complete_text

ProgressCallback = Callable[[str], Awaitable[None]]

MIN_TEXT_LENGTH = 50


async def prepare_document_text(
    document_text: str,
    image_base64: Optional[str] = None,
    image_media_type: str = "image/jpeg",
    on_progress: Optional[ProgressCallback] = None,
) -> str:
    """
    Return clean, embeddable text for the RAG index.

    - PDF / text uploads: use extracted text as-is (when sufficient)
    - Image uploads: run vision OCR to pull labels, notes, schedules, etc.
    """
    cleaned = _normalize_whitespace(document_text)
    if len(cleaned) >= MIN_TEXT_LENGTH:
        return cleaned

    if not image_base64:
        return cleaned

    if on_progress:
        await on_progress("Extracting readable text from document image…")

    user_content = [
        {
            "type": "text",
            "text": (
                "Extract ALL readable text from this construction project document. "
                "Prioritize: window schedules, window tags, opening sizes (W×H), "
                "room names, floor levels, shade/blind/screen specifications, "
                "general notes, legends, and abbreviations (WS, SHD, WIN). "
                "Preserve section groupings with blank lines between sections. "
                "Return plain text only — no JSON, no markdown fences."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{image_media_type};base64,{image_base64}",
                "detail": "high",
            },
        },
    ]

    extracted = await complete_text(
        (
            "You are a construction document OCR specialist. "
            "Transcribe every piece of visible text accurately and completely."
        ),
        user_content,
        temperature=0.0,
        max_tokens=8000,
    )

    return _normalize_whitespace(extracted) or cleaned


def _normalize_whitespace(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    normalized: list[str] = []
    blank = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not blank and normalized:
                normalized.append("")
                blank = True
            continue
        normalized.append(stripped)
        blank = False

    return "\n".join(normalized).strip()
