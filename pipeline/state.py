"""
pipeline/state.py — LangGraph shared state.

PipelineState is the single object that flows through every node in the graph.
Each node reads from it and returns a partial dict of updated keys — LangGraph
merges those updates into the state automatically before passing to the next node.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import TypedDict


class PipelineState(TypedDict):
    # ── Inputs (set once before the graph starts) ────────────────────────────
    document_text: str
    image_base64: Optional[str]
    image_media_type: Optional[str]
    pdf_bytes: Optional[bytes]
    page_texts: Optional[list]
    page_images: Optional[list]
    source_meta: Optional[dict]
    workbook_analysis: Optional[dict]
    # Sheet images for an architectural set. Cleared after the vision node reads them.
    drawing_sheets: Optional[list]
    session_id: str

    # True when the upload is Excel-only and the WINDOW MATRIX states the total,
    # so the graph skips vision + context parsing (see pipeline/graph.py).
    workbook_fast_path: Optional[bool]

    # ── Agent outputs (filled in as each node completes) ─────────────────────
    vision_result: Optional[dict]     # Agent 0 — Vision Analyst
    context_result: Optional[dict]    # Agent 1 — Context Parser
    takeoff_result: Optional[dict]    # Agent 2 — Take-off Engine
    estimation_result: Optional[dict] # Agent 3 — Estimation Agent
    validation_result: Optional[dict] # Agent 4 — Validation

    # ── Error propagation ────────────────────────────────────────────────────
    # If any node sets this, conditional edges route directly to END
    error: Optional[str]
