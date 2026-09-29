"""
pipeline/nodes.py — LangGraph node functions.

Each node wraps one agent, emits SSE progress, and returns a partial state
dict that LangGraph merges before handing control to the next node.

Error handling: any exception is caught, stored in state["error"], and the
conditional edges in graph.py route the graph straight to END.
"""

from __future__ import annotations

import sse
from domain.pipeline_playbook import get_agent_playbook
from domain.workbook_takeoff import workbook_count_reconciliation
from pipeline.document_steps import emit_doc_step
from pipeline.validation_adjust import adjust_validation_for_workbook
from agents.context_parser import run_context_parser
from agents.drawing_reader import read_drawing_set
from agents.vision_analyst import run_vision_analyst
from agents.estimation_agent import run_estimation_agent
from agents.takeoff_engine import run_takeoff_engine
from agents.validation import run_validation
from pipeline.state import PipelineState

#: Agents the workbook fast path deliberately does not run.
FAST_PATH_SKIPPED = ("visionAnalyst", "contextParser")


async def _emit(sid: str, agent: str, status: str, message: str, data: dict | None = None) -> None:
    payload: dict = {"agent": agent, "status": status, "message": message}
    if data is not None:
        payload["data"] = data
    await sse.emit(sid, payload)


async def _fail(sid: str, agent: str, label: str, message: str) -> dict:
    await _emit(sid, agent, "error", message)
    return {"error": f"{label}: {message}"}


def _require(state: PipelineState, key: str) -> dict | None:
    if state.get("error"):
        return None
    value = state.get(key)
    if not isinstance(value, dict):
        return None
    return value


def _context_or_empty(state: PipelineState) -> dict:
    """Context report, or a minimal stand-in on the workbook fast path."""
    context = state.get("context_result")
    if isinstance(context, dict):
        return context
    wb = state.get("workbook_analysis") or {}
    return {
        "documentType": "Direct Shades workbook set (Excel)",
        "trade": "Window Treatments / Shades",
        "projectName": wb.get("projectName"),
        "windowScheduleLocation": "WINDOW MATRIX sheet",
        "readingGuide": (wb.get("guidance") or "")[:600],
        "abbreviations": [],
        "embedded_chunks": [],
        "workbook": wb,
    }


# ── Node: Intake router ───────────────────────────────────────────────────────

async def intake_node(state: PipelineState) -> dict:
    """
    Announce the route before any agent runs.

    On the fast path the skipped agents are reported as 'skipped' so the UI shows
    five resolved steps instead of two that never start.
    """
    sid = state["session_id"]
    wb = state.get("workbook_analysis") or {}

    if not state.get("workbook_fast_path"):
        await _emit(
            sid,
            "document",
            "running",
            "Full analysis route: AI vision, document index, then take-off.",
            data={"route": "full"},
        )
        return {}

    counts = workbook_count_reconciliation(wb)
    await _emit(
        sid,
        "document",
        "running",
        (
            f"Excel fast path: WINDOW MATRIX states {counts['matrixTotal']} shades, "
            "so the count and dimensions are read straight from the workbooks."
        ),
        data={"route": "workbook_fast_path", "workbookCounts": counts},
    )
    for agent in FAST_PATH_SKIPPED:
        await _emit(
            sid,
            agent,
            "skipped",
            "Not needed — the structured workbooks already state the count and sizes.",
            data={"playbook": get_agent_playbook(agent), "reason": "workbook_fast_path"},
        )
    return {}


# ── Node 0: Vision Analyst ────────────────────────────────────────────────────

async def vision_analyst_node(state: PipelineState) -> dict:
    sid = state["session_id"]

    async def on_progress(msg: str) -> None:
        await _emit(sid, "visionAnalyst", "running", msg)

    async def on_document_step(**fields: object) -> None:
        await emit_doc_step(
            sid,
            "visionAnalyst",
            str(fields.get("message") or fields.get("heading") or "Reading page"),
            heading=fields.get("heading") if isinstance(fields.get("heading"), str) else None,
            excerpt=fields.get("excerpt") if isinstance(fields.get("excerpt"), str) else None,
            source_file=fields.get("source_file") if isinstance(fields.get("source_file"), str) else None,
            source_sheet=fields.get("source_sheet") if isinstance(fields.get("source_sheet"), str) else None,
            source_page=fields.get("source_page") if isinstance(fields.get("source_page"), int) else None,
            step_kind=str(fields.get("step_kind") or "excerpt"),
        )

    await _emit(
        sid,
        "visionAnalyst",
        "focus",
        get_agent_playbook("visionAnalyst").get("mission", ""),
        data={"playbook": get_agent_playbook("visionAnalyst")},
    )
    await _emit(sid, "visionAnalyst", "running", "Reading document with AI vision…")

    drawing_sheets = state.get("drawing_sheets") or []
    if drawing_sheets:
        try:
            result = await read_drawing_set(drawing_sheets, on_progress=on_progress)
            await _emit(
                sid,
                "visionAnalyst",
                "complete",
                result.get("catalogueSummary") or "Drawing set read.",
                data={
                    "pagesAnalyzed": result.get("pagesAnalyzed"),
                    "estimatedTotalShades": result.get("estimatedTotalShades"),
                    "estimatedTotalWindows": result.get("estimatedTotalWindows"),
                    "shadesRequired": result.get("shadesRequired"),
                    "catalogueSummary": result.get("catalogueSummary"),
                    "confidenceNotes": (result.get("confidenceNotes") or [])[:5],
                    "openingCount": len(result.get("windowOpenings") or []),
                },
            )
            return {
                "vision_result": result,
                "document_text": state["document_text"],
                "page_images": [],
                "drawing_sheets": [],
            }
        except Exception as exc:
            return await _fail(sid, "visionAnalyst", "Vision Analyst", str(exc))

    try:
        result = await run_vision_analyst(
            document_text=state["document_text"],
            pdf_bytes=state.get("pdf_bytes"),
            page_texts=state.get("page_texts"),
            image_base64=state.get("image_base64"),
            image_media_type=state.get("image_media_type") or "image/jpeg",
            on_progress=on_progress,
            on_document_step=on_document_step,
        )

        page_images = result.pop("page_images", [])
        if page_images:
            result["analyzedPageNumbers"] = [p["page_number"] for p in page_images]
        vision_transcript = (result.get("visualTranscript") or "").strip()
        merged_text = state["document_text"]
        if vision_transcript:
            merged_text = (
                f"{merged_text.rstrip()}\n\n--- AI VISION EXTRACTION ---\n{vision_transcript}"
            ).strip()

        await _emit(
            sid,
            "visionAnalyst",
            "complete",
            "Vision analysis complete.",
            data={
                "pagesAnalyzed": result.get("pagesAnalyzed"),
                "estimatedTotalShades": result.get("estimatedTotalShades"),
                "estimatedTotalWindows": result.get("estimatedTotalWindows"),
                "shadesRequired": result.get("shadesRequired"),
                "catalogueSummary": result.get("catalogueSummary"),
                "confidenceNotes": (result.get("confidenceNotes") or [])[:5],
                "openingCount": len(result.get("windowOpenings") or []),
            },
        )
        return {
            "vision_result": result,
            "document_text": merged_text,
            "page_images": page_images,
        }

    except Exception as exc:
        return await _fail(sid, "visionAnalyst", "Vision Analyst", str(exc))


# ── Node 1: Context Parser ────────────────────────────────────────────────────

async def context_parser_node(state: PipelineState) -> dict:
    sid = state["session_id"]

    async def on_progress(msg: str) -> None:
        await _emit(sid, "contextParser", "running", msg)

    async def on_document_step(**fields: object) -> None:
        await emit_doc_step(
            sid,
            "contextParser",
            str(fields.get("message") or fields.get("heading") or "Reading document"),
            heading=fields.get("heading") if isinstance(fields.get("heading"), str) else None,
            excerpt=fields.get("excerpt") if isinstance(fields.get("excerpt"), str) else None,
            source_file=fields.get("source_file") if isinstance(fields.get("source_file"), str) else None,
            source_sheet=fields.get("source_sheet") if isinstance(fields.get("source_sheet"), str) else None,
            source_page=fields.get("source_page") if isinstance(fields.get("source_page"), int) else None,
            step_kind=str(fields.get("step_kind") or "excerpt"),
        )

    await _emit(
        sid,
        "contextParser",
        "focus",
        get_agent_playbook("contextParser").get("mission", ""),
        data={"playbook": get_agent_playbook("contextParser")},
    )
    drawing = (state.get("vision_result") or {}).get("drawingTakeoff") or {}
    if drawing.get("takeoffItems"):
        await _emit(
            sid,
            "contextParser",
            "complete",
            "The count is already on the drawings, so the document index is skipped.",
            data={
                "documentType": "Architectural drawing set",
                "projectName": None,
                "keyFindings": [drawing.get("summary") or ""][:1],
            },
        )
        return {
            "context_result": {
                "documentType": "Architectural drawing set",
                "trade": "Window Treatments / Shades",
                "projectName": None,
                "windowScheduleLocation": "Unit matrix and unit plans",
                "readingGuide": (state.get("document_text") or "")[:600],
                "abbreviations": [],
                "embedded_chunks": [],
                "workbook": state.get("workbook_analysis") or {},
                "keyFindings": [drawing.get("summary") or ""],
            }
        }

    await _emit(sid, "contextParser", "running", "Analyzing document context…")

    try:
        page_images = state.get("page_images") or []
        result = await run_context_parser(
            state["document_text"],
            state.get("image_base64"),
            image_media_type=state.get("image_media_type") or "image/jpeg",
            page_images=page_images,
            vision_result=state.get("vision_result"),
            workbook_analysis=state.get("workbook_analysis"),
            on_progress=on_progress,
            on_document_step=on_document_step,
        )
        await _emit(
            sid,
            "contextParser",
            "complete",
            "Context parsing complete.",
            data={
                "documentType": result.get("documentType"),
                "trade": result.get("trade"),
                "projectName": result.get("projectName"),
                "windowScheduleLocation": result.get("windowScheduleLocation"),
                "abbreviationCount": len(result.get("abbreviations", [])),
                "readingGuide": (result.get("readingGuide") or "")[:500],
                "shadeSpecification": (result.get("shadeSpecification") or "")[:400],
                "keyFindings": (result.get("keyFindings") or [])[:6],
            },
        )
        return {"context_result": result}

    except Exception as exc:
        return await _fail(sid, "contextParser", "Context Parser", str(exc))


# ── Node 2: Take-off Engine ───────────────────────────────────────────────────

async def takeoff_engine_node(state: PipelineState) -> dict:
    sid = state["session_id"]

    if state.get("error"):
        return {}

    context = _require(state, "context_result")
    if context is None:
        if not state.get("workbook_fast_path"):
            return await _fail(sid, "takeoffEngine", "Take-off Engine", "Missing context from Agent 1.")
        context = _context_or_empty(state)

    async def on_progress(msg: str) -> None:
        await _emit(sid, "takeoffEngine", "running", msg)

    async def on_document_step(**fields: object) -> None:
        await emit_doc_step(
            sid,
            "takeoffEngine",
            str(fields.get("message") or fields.get("heading") or "Take-off read"),
            heading=fields.get("heading") if isinstance(fields.get("heading"), str) else None,
            excerpt=fields.get("excerpt") if isinstance(fields.get("excerpt"), str) else None,
            source_file=fields.get("source_file") if isinstance(fields.get("source_file"), str) else None,
            source_sheet=fields.get("source_sheet") if isinstance(fields.get("source_sheet"), str) else None,
            step_kind=str(fields.get("step_kind") or "excerpt"),
        )

    await _emit(
        sid,
        "takeoffEngine",
        "focus",
        get_agent_playbook("takeoffEngine").get("mission", ""),
        data={"playbook": get_agent_playbook("takeoffEngine")},
    )
    await _emit(sid, "takeoffEngine", "running", "Counting window shades…")

    try:
        result = await run_takeoff_engine(
            context,
            vision_result=state.get("vision_result"),
            document_text=state.get("document_text") or "",
            workbook_analysis=state.get("workbook_analysis"),
            on_progress=on_progress,
            on_document_step=on_document_step,
        )
        await _emit(
            sid,
            "takeoffEngine",
            "complete",
            "Take-off complete.",
            data={
                "totalShades": result.get("totalShadeCount") or result.get("totalItemCount"),
                "countByType": result.get("countByType"),
                "categories": result.get("categories"),
                "summary": result.get("summary"),
                "primarySource": result.get("primarySource"),
                "sourcesUsed": (result.get("sourcesUsed") or "")[:400],
                "lineCount": len(result.get("takeoffItems") or []),
                "reconciliationNotes": (result.get("reconciliationNotes") or [])[:4],
            },
        )
        return {"takeoff_result": result}

    except Exception as exc:
        return await _fail(sid, "takeoffEngine", "Take-off Engine", str(exc))


# ── Node 3: Estimation Agent ──────────────────────────────────────────────────

async def estimation_agent_node(state: PipelineState) -> dict:
    sid = state["session_id"]

    if state.get("error"):
        return {}

    takeoff = _require(state, "takeoff_result")
    if takeoff is None:
        return await _fail(sid, "estimationAgent", "Estimation Agent", "Missing take-off from Agent 2.")
    context = _context_or_empty(state)

    async def on_progress(msg: str) -> None:
        await _emit(sid, "estimationAgent", "running", msg)

    await _emit(
        sid,
        "estimationAgent",
        "focus",
        get_agent_playbook("estimationAgent").get("mission", ""),
        data={"playbook": get_agent_playbook("estimationAgent")},
    )
    await _emit(sid, "estimationAgent", "running", "Building client offer…")

    try:
        result = await run_estimation_agent(
            context,
            takeoff,
            workbook_analysis=state.get("workbook_analysis"),
            on_progress=on_progress,
        )
        offer = result.get("clientOffer") or {}
        await _emit(
            sid,
            "estimationAgent",
            "complete",
            "Estimation complete.",
            data={
                "totalEstimate": result.get("totalEstimate"),
                "totalShades": offer.get("totalShades") or result.get("shadeSummary", {}).get("totalShades"),
                "currency": result.get("currency"),
                "offerHeadline": offer.get("headline"),
                "priceSource": result.get("priceSource"),
                "lineCount": len(result.get("estimates") or []),
                "executiveSummary": (result.get("executiveSummary") or offer.get("executiveSummary") or "")[:500],
            },
        )
        return {"estimation_result": result}

    except Exception as exc:
        return await _fail(sid, "estimationAgent", "Estimation Agent", str(exc))


# ── Node 4: Validation ────────────────────────────────────────────────────────

async def validation_node(state: PipelineState) -> dict:
    sid = state["session_id"]

    if state.get("error"):
        return {}

    takeoff = _require(state, "takeoff_result")
    estimation = _require(state, "estimation_result")
    if takeoff is None or estimation is None:
        return await _fail(sid, "validation", "Validation", "Missing results from prior agents.")
    context = _context_or_empty(state)

    async def on_progress(msg: str) -> None:
        await _emit(sid, "validation", "running", msg)

    await _emit(
        sid,
        "validation",
        "focus",
        get_agent_playbook("validation").get("mission", ""),
        data={"playbook": get_agent_playbook("validation")},
    )
    await _emit(sid, "validation", "running", "Validating offer before send…")

    try:
        result = await run_validation(
            context,
            takeoff,
            estimation,
            vision_result=state.get("vision_result"),
            on_progress=on_progress,
        )
        result = adjust_validation_for_workbook(
            result,
            state.get("workbook_analysis"),
            takeoff,
            estimation=estimation,
        )
        await _emit(
            sid,
            "validation",
            "complete",
            "Validation complete.",
            data={
                "confidenceScore": result.get("confidenceScore"),
                "confidenceLevel": result.get("confidenceLevel"),
                "recommendation": result.get("recommendation"),
                "readyToSendOffer": result.get("readyToSendOffer"),
                "issues": (result.get("validationIssues") or result.get("issues") or [])[:6],
                "userFriendlySummary": result.get("userFriendlySummary"),
                "reviewChecklist": result.get("reviewChecklist"),
                "summary": (result.get("validationSummary") or result.get("summary") or "")[:400],
            },
        )
        return {"validation_result": result}

    except Exception as exc:
        return await _fail(sid, "validation", "Validation", str(exc))
