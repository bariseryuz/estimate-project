"""Emit document-centric SSE steps (what the engine is reading, where, and why)."""

from __future__ import annotations

import re
from typing import Any, Optional

import sse

FILE_HEADER_RE = re.compile(
    r"========\s*FILE:\s*(.+?)\s*\(([^)]+)\)\s*========",
    re.IGNORECASE,
)


def _clip(text: str, limit: int = 900) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


async def emit_doc_step(
    session_id: str,
    agent: str,
    message: str,
    *,
    heading: Optional[str] = None,
    excerpt: Optional[str] = None,
    source_file: Optional[str] = None,
    source_sheet: Optional[str] = None,
    source_page: Optional[int] = None,
    step_kind: str = "excerpt",
) -> None:
    await sse.emit(
        session_id,
        {
            "agent": agent,
            "status": "docStep",
            "message": message,
            "data": {
                "kind": step_kind,
                "heading": heading or message,
                "excerpt": _clip(excerpt or ""),
                "sourceFile": source_file,
                "sourceSheet": source_sheet,
                "sourcePage": source_page,
            },
        },
    )


async def emit_ingest_walk(session_id: str, merged: dict[str, Any]) -> None:
    """Walk uploaded files and visible text sections before agents run."""
    meta = merged.get("source_meta") or {}
    names = meta.get("fileNames") or []
    if merged.get("drawing_sheets") and len(names) > 12:
        await emit_doc_step(
            session_id,
            "document",
            f"Opened {len(names)} drawing sheets. Reading the unit matrix, unit plans, and floor plans.",
            heading="Drawing set",
            excerpt=f"{len(names)} sheets are in the workspace. Notes, elevations, and details are not read for the count.",
            step_kind="file",
        )
        return
    for name in names:
        await emit_doc_step(
            session_id,
            "document",
            f"Opened {name}",
            heading=f"File in workspace: {name}",
            excerpt=f"This file is part of the merged project set ({len(names)} file(s) total).",
            source_file=name,
            step_kind="file",
        )

    text = (merged.get("document_text") or "").strip()
    if text:
        for match in FILE_HEADER_RE.finditer(text):
            fname = match.group(1).strip()
            fmt = match.group(2).strip()
            start = match.end()
            next_m = FILE_HEADER_RE.search(text, start)
            end = next_m.start() if next_m else min(start + 2500, len(text))
            body = _clip(text[start:end], 900)
            await emit_doc_step(
                session_id,
                "document",
                f"Reading extracted text from {fname}",
                heading=f"{fname} ({fmt})",
                excerpt=body or "(empty text layer)",
                source_file=fname,
                step_kind="extract",
            )

    wb = merged.get("workbook_analysis")
    if wb:
        await emit_workbook_walk(session_id, wb)


async def emit_workbook_walk(session_id: str, wb: dict[str, Any]) -> None:
    project = wb.get("projectName")
    if project:
        await emit_doc_step(
            session_id,
            "document",
            f"Project identified: {project}",
            heading="Workbook project name",
            excerpt=project,
            step_kind="workbook",
        )

    for f in wb.get("files") or []:
        sheets = ", ".join(f.get("sheets") or [])[:400]
        await emit_doc_step(
            session_id,
            "document",
            f"Structured Excel: {f.get('name')}",
            heading=f"{f.get('kind', 'workbook').replace('_', ' ').title()}",
            excerpt=sheets or "Sheets parsed",
            source_file=f.get("name"),
            step_kind="workbook",
        )

    total = wb.get("authoritativeTotalShades")
    if total is not None:
        await emit_doc_step(
            session_id,
            "document",
            f"WINDOW MATRIX TOTAL row → {total} shades",
            heading="Authoritative shade count (Matrix TOTAL row)",
            excerpt=(
                f"TOTAL SHADES = {total}. "
                "Downstream agents must align the offer to this count when present."
            ),
            source_sheet="WINDOW MATRIX",
            step_kind="authority",
        )

    for row in (wb.get("windowMatrixMarkings") or [])[:18]:
        marking = row.get("marking") or "?"
        shades = row.get("totalShades")
        section = row.get("section") or ""
        await emit_doc_step(
            session_id,
            "document",
            f"Matrix row: {marking}",
            heading=f"Marking {marking} ({section})".strip(),
            excerpt=f"Total shades for this marking: {shades}",
            source_sheet="WINDOW MATRIX",
            step_kind="matrix_row",
        )

    for note in (wb.get("notes") or [])[:4]:
        await emit_doc_step(
            session_id,
            "document",
            "Workbook parsing note",
            heading="Parser note",
            excerpt=note,
            step_kind="note",
        )


async def emit_chunk_reads(
    session_id: str,
    agent: str,
    label: str,
    chunks: list[dict],
    *,
    source_file: Optional[str] = None,
    max_items: int = 6,
) -> None:
    for i, chunk in enumerate(chunks[:max_items]):
        text = chunk.get("text") or ""
        first_line = text.split("\n", 1)[0].strip()[:120]
        await emit_doc_step(
            session_id,
            agent,
            f"{label} — section {i + 1}",
            heading=first_line or label,
            excerpt=text,
            source_file=source_file,
            step_kind="rag",
        )
