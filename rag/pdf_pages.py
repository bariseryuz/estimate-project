"""Render PDF pages to PNG images for vision models."""

from __future__ import annotations

import base64
from typing import TypedDict

import fitz


class PageImage(TypedDict):
    page_number: int
    media_type: str
    image_base64: str


def render_pdf_pages(
    pdf_bytes: bytes,
    *,
    dpi: int = 144,
    max_pages: int = 12,
    page_numbers: list[int] | None = None,
) -> list[PageImage]:
    """
    Rasterize PDF pages to PNG base64 payloads.

    page_numbers: optional 1-based indices to render (deduped, sorted). When omitted,
    renders the first max_pages pages.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = doc.page_count
    if page_numbers:
        indices = sorted({max(1, min(total, n)) for n in page_numbers})
        if max_pages > 0:
            indices = indices[:max_pages]
    else:
        end = total if max_pages <= 0 else min(total, max_pages)
        indices = list(range(1, end + 1))

    pages: list[PageImage] = []
    for one_based in indices:
        page = doc.load_page(one_based - 1)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        pages.append(
            {
                "page_number": one_based,
                "media_type": "image/png",
                "image_base64": base64.b64encode(png_bytes).decode("utf-8"),
            }
        )
    doc.close()
    return pages


def prioritize_pdf_page_indices(
    page_texts: list[str],
    *,
    max_pages: int = 12,
) -> list[int]:
    """
    Pick PDF pages most likely to contain window schedules / plans (1-based indices).
    Falls back to first N pages when no keyword hits.
    """
    keywords = (
        "window",
        "schedule",
        "shade",
        "shd",
        "blind",
        "screen",
        "opening",
        "elevation",
        "finish",
        "ws",
        "treatment",
    )
    scored: list[tuple[int, int]] = []
    for i, text in enumerate(page_texts):
        lower = (text or "").lower()
        score = sum(1 for kw in keywords if kw in lower)
        if score:
            scored.append((score, i + 1))

    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        chosen = [p for _, p in scored[:max_pages]]
        return sorted(set(chosen))

    return list(range(1, min(len(page_texts) or 1, max_pages) + 1))
