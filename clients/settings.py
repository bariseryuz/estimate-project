"""LLM provider configuration."""

from __future__ import annotations

import os


def get_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in ("openai", "gemini"):
        return explicit
    if os.getenv("GEMINI_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return "gemini"
    return "openai"


def get_chat_model() -> str:
    provider = get_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    return os.getenv("OPENAI_MODEL", "gpt-4o")


def get_embedding_model() -> str:
    provider = get_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
    return os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


def get_vision_analyze_all_pages() -> bool:
    return os.getenv("VISION_ANALYZE_ALL", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def get_vision_hard_page_cap() -> int:
    return int(os.getenv("VISION_HARD_PAGE_CAP", "200"))


def get_vision_max_pages() -> int:
    """Legacy limit when VISION_ANALYZE_ALL=false. 0 = use hard cap only."""
    return int(os.getenv("VISION_MAX_PDF_PAGES", "0"))


def get_vision_pages_per_request() -> int:
    return max(1, int(os.getenv("VISION_PAGES_PER_REQUEST", "2")))


def get_vision_parallel_requests() -> int:
    return max(1, int(os.getenv("VISION_PARALLEL_REQUESTS", "4")))


def resolve_vision_page_count(total_pdf_pages: int) -> int:
    """How many PDF pages to rasterize and analyze."""
    cap = get_vision_hard_page_cap()
    total = max(0, total_pdf_pages)
    if get_vision_analyze_all_pages():
        return min(total, cap) if total else 0
    limit = get_vision_max_pages()
    if limit <= 0:
        return min(total, cap)
    return min(total, limit, cap)


def get_vision_pdf_dpi() -> int:
    return int(os.getenv("VISION_PDF_DPI", "144"))


def get_embedding_dimensions() -> int | None:
    """Output dimensions for gemini-embedding-2 (768, 1536, or 3072). None = model default."""
    raw = os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "").strip()
    if not raw:
        model = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
        return 768 if "embedding-2" in model else None
    return int(raw)
