"""
Human-readable playbook for each pipeline agent (UI + SSE focus events).
"""

from __future__ import annotations

from typing import Any

AGENT_PLAYBOOK: dict[str, dict[str, Any]] = {
    "document": {
        "step": "—",
        "title": "Your project files",
        "mission": "Everything the estimators uploaded becomes the working set for this run.",
        "logic": [
            "Ingest each file (Excel Window Matrix, Material Summary, Bid Summary, or PDF).",
            "Parse workbook structure — WINDOW MATRIX TOTAL row is the shade count authority.",
            "Extract text for search (RAG) and detect PDF pages for vision.",
            "Pass one merged document story to the AI agents in order.",
        ],
        "reads": "Uploaded files",
        "produces": "Unified text, workbook facts, optional PDF bytes for vision",
    },
    "visionAnalyst": {
        "step": "00",
        "title": "Vision Analyst",
        "mission": "See the drawings the way a field estimator walks the sheet set.",
        "logic": [
            "Read window, door, and storefront schedules into separate mark namespaces.",
            "Count each physical opening once on the unit floor plan.",
            "Multiply that count by the matching unit-matrix rows.",
            "Hold blank sizes, conflicts, and unknown subtypes. Do not turn openings into shades.",
        ],
        "reads": "PDF / image pages + embedded text layer",
        "produces": "Vision transcript merged into the document; opening list & pre-count",
    },
    "contextParser": {
        "step": "01",
        "title": "Context Parser",
        "mission": "Learn how THIS project labels windows and where the schedule lives.",
        "logic": [
            "Chunk document text and build a searchable embedding index (RAG).",
            "Pull abbreviation legends and window-schedule sections via retrieval.",
            "Combine workbook facts (Matrix total) with schedule/spec context.",
            "Write a reading guide so take-off knows exactly where to count.",
        ],
        "reads": "Full document text + workbook analysis + vision summary",
        "produces": "Abbreviations, structure map, reading guide, RAG index for take-off",
    },
    "takeoffEngine": {
        "step": "02",
        "title": "Take-off Engine",
        "mission": "Count every shade line-by-line like a quantity surveyor.",
        "logic": [
            "Retrieve window schedules, room data, and spec notes from RAG.",
            "Cross-check vision pre-count and WINDOW MATRIX total when present.",
            "List each unit: tag, size, type, location, qty (EA).",
            "Reconcile totals; Matrix TOTAL wins on Direct Shades workbooks.",
        ],
        "reads": "RAG chunks + context report + workbook authority",
        "produces": "Take-off lines, total shade count, count by type/floor",
    },
    "estimationAgent": {
        "step": "03",
        "title": "Estimation Agent",
        "mission": "Turn counts into a client-ready price and narrative.",
        "logic": [
            "Price each shade by type and size tier (manual, solar, blackout, motor).",
            "Add labor, waste, overhead, and profit per commercial norms.",
            "Align headline count with take-off / Matrix total.",
            "Draft executive summary text for the contractor's email.",
        ],
        "reads": "Take-off list + catalogue + scope notes",
        "produces": "Line pricing, project total, client offer copy",
    },
    "validation": {
        "step": "04",
        "title": "Validation",
        "mission": "Audit before send — same checklist a senior estimator would use.",
        "logic": [
            "Compare schedule row count vs take-off vs offer headline.",
            "Check dimensions and shade types match the spec.",
            "Flag missing TBD openings instead of silent drops.",
            "Score confidence and recommend Approve / Review / Reject.",
        ],
        "reads": "Context + take-off + estimation + vision",
        "produces": "Confidence score, issues list, ready-to-send flag",
    },
}


def get_agent_playbook(agent_id: str) -> dict[str, Any]:
    return AGENT_PLAYBOOK.get(agent_id, {})
