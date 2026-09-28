"""
main.py — FastAPI server + LangGraph orchestrator.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()
load_dotenv(".env.local", override=True)

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from clients.settings import get_chat_model, get_provider

import sse
from pipeline.analysis_report import build_analysis_detail
from pipeline.client_offer import build_client_offer_package
from pipeline.estimation_audit import build_estimation_audit
from pipeline.project_summary import build_project_summary
from pipeline.quantity_schedule import build_quantity_schedule
from pipeline.graph import pipeline_graph
from pipeline.state import PipelineState
from rag.file_ingest import SUPPORTED_EXTENSIONS, ingest_upload, supported_extensions_hint
from domain.pipeline_playbook import get_agent_playbook
from domain.workbook_takeoff import should_use_workbook_fast_path
from pipeline.document_steps import emit_ingest_walk
from rag.workbook_analyzer import analyze_multiple

app = FastAPI(title="Estimator AI")

PUBLIC_DIR = Path(__file__).parent / "public"
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_TOTAL_UPLOAD_SIZE = 200 * 1024 * 1024
MAX_FILE_COUNT = 20
#: How often to tell the browser the run is still alive during a long agent step.
PROGRESS_HEARTBEAT_SECONDS = 20


@app.get("/api/progress/{session_id}")
async def progress_stream(session_id: str):
    queue = sse.get_or_create(session_id)

    async def event_generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if data is None:
                        break
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse.remove(session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def api_config():
    return {
        "provider": get_provider(),
        "model": get_chat_model(),
    }


@app.get("/api/pipeline-guide")
async def pipeline_guide():
    from domain.pipeline_playbook import AGENT_PLAYBOOK

    return {"agents": AGENT_PLAYBOOK}


def _merge_uploads(uploads: list[tuple[str, bytes, str]]) -> dict:
    """Merge multiple ingested files into one pipeline payload."""
    parts_text: list[str] = []
    page_texts: list[str] = []
    pdf_bytes: Optional[bytes] = None
    image_base64 = None
    image_media_type = None
    file_names: list[str] = []
    total_size = 0
    ingest_meta_list: list[dict] = []

    for filename, contents, ext in uploads:
        file_names.append(filename)
        total_size += len(contents)
        ingested = ingest_upload(filename, contents)
        ingest_meta_list.append({"file": filename, **ingested.ingest_meta})

        header = f"\n\n======== FILE: {filename} ({ingested.source_format}) ========\n\n"
        if ingested.document_text.strip():
            parts_text.append(header + ingested.document_text.strip())

        if ingested.page_texts:
            page_texts.extend(ingested.page_texts)

        if ingested.pdf_bytes and pdf_bytes is None:
            pdf_bytes = ingested.pdf_bytes
        if ingested.image_base64 and image_base64 is None:
            image_base64 = ingested.image_base64
            image_media_type = ingested.image_media_type

    document_text = "\n".join(parts_text).strip()
    workbook_analysis = analyze_multiple(uploads)

    display_name = (
        file_names[0]
        if len(file_names) == 1
        else f"{len(file_names)} files ({', '.join(file_names[:3])}{'…' if len(file_names) > 3 else ''})"
    )

    return {
        "document_text": document_text,
        "page_texts": page_texts or None,
        "pdf_bytes": pdf_bytes,
        "image_base64": image_base64,
        "image_media_type": image_media_type,
        "workbook_analysis": workbook_analysis,
        "source_meta": {
            "fileName": display_name,
            "fileNames": file_names,
            "fileSizeBytes": total_size,
            "sourceFormat": uploads[0][2] if len(uploads) == 1 else "multi",
            "ingest": {
                "readMethod": "Multi-file ingest" if len(uploads) > 1 else ingest_meta_list[0].get("readMethod", ""),
                "files": ingest_meta_list,
            },
        },
    }


async def _emit_progress_heartbeat(session_id: str) -> None:
    """
    Keep the browser informed during long single steps.

    Vision on a large drawing set can run for minutes without any agent emitting a
    message; without this the UI looks stalled even though work is happening.
    """
    elapsed = 0
    try:
        while True:
            await asyncio.sleep(PROGRESS_HEARTBEAT_SECONDS)
            elapsed += PROGRESS_HEARTBEAT_SECONDS
            await sse.emit(
                session_id,
                {
                    "agent": "system",
                    "status": "heartbeat",
                    "message": f"Still working — {elapsed // 60}m {elapsed % 60}s elapsed.",
                    "data": {"elapsedSeconds": elapsed},
                },
            )
    except asyncio.CancelledError:
        pass


@app.post("/api/estimate")
async def estimate(
    sessionId: str = Form(...),
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
):
    session_id = (sessionId or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing sessionId — reload the page and retry.")

    upload_list: List[UploadFile] = list(files or [])
    if file and file.filename:
        upload_list.append(file)
    if not upload_list:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(upload_list) > MAX_FILE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files ({len(upload_list)}). Upload at most {MAX_FILE_COUNT} at a time.",
        )

    uploads: list[tuple[str, bytes, str]] = []
    seen_names: set[str] = set()
    empty_files: list[str] = []
    total_bytes = 0

    for upload in upload_list:
        if not upload.filename:
            continue
        name = Path(upload.filename).name  # strip any directory component
        ext = Path(name).suffix.lower()
        contents = await upload.read()

        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f'"{name}" exceeds the 50 MB limit.',
            )
        if ext and ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Unsupported type "{ext}" on {name}. '
                    f"Supported: {supported_extensions_hint()}"
                ),
            )
        if not contents:
            empty_files.append(name)
            continue
        if name.lower() in seen_names:
            continue  # same file dropped twice — count it once
        seen_names.add(name.lower())

        total_bytes += len(contents)
        if total_bytes > MAX_TOTAL_UPLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Upload set is too large ({total_bytes / 1024 / 1024:.0f} MB). "
                    f"Keep the total under {MAX_TOTAL_UPLOAD_SIZE // 1024 // 1024} MB."
                ),
            )
        uploads.append((name, contents, ext or ""))

    if not uploads:
        detail = "No valid files in upload."
        if empty_files:
            detail = f"These files are empty: {', '.join(empty_files)}. Re-export and try again."
        raise HTTPException(status_code=400, detail=detail)

    try:
        merged = _merge_uploads(uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    wb_early = merged.get("workbook_analysis") or {}
    has_workbook_data = bool(
        wb_early.get("authoritativeTotalShades") is not None
        or wb_early.get("blindQtyLines")
        or wb_early.get("windowMatrixMarkings")
    )
    if (
        not merged["document_text"].strip()
        and not merged["image_base64"]
        and not merged["pdf_bytes"]
        and not has_workbook_data
    ):
        raise HTTPException(status_code=422, detail="Could not extract content from the file(s).")

    wb = merged.get("workbook_analysis") or {}
    fast_path = should_use_workbook_fast_path(
        wb,
        has_pdf=bool(merged["pdf_bytes"]),
        has_image=bool(merged["image_base64"]),
    )
    await sse.emit(
        session_id,
        {
            "agent": "document",
            "status": "focus",
            "message": "Your files are loaded into the estimation workspace.",
            "data": {
                "playbook": get_agent_playbook("document"),
                "fileNames": merged["source_meta"].get("fileNames") or [],
                "fileName": merged["source_meta"].get("fileName"),
                "workbookProject": wb.get("projectName"),
                "matrixTotal": wb.get("authoritativeTotalShades"),
                "workbookKinds": [
                    f.get("kind") for f in (wb.get("files") or []) if f.get("kind")
                ],
                "referenceGrandTotal": (wb.get("referencePricing") or {}).get("grandTotal"),
                "skippedFiles": empty_files,
                "route": "workbook_fast_path" if fast_path else "full_analysis",
                "textChars": len(merged["document_text"] or ""),
                "hasPdf": bool(merged["pdf_bytes"]),
            },
        },
    )
    await emit_ingest_walk(session_id, merged)

    initial_state: PipelineState = {
        "document_text": merged["document_text"],
        "image_base64": merged["image_base64"],
        "image_media_type": merged["image_media_type"],
        "pdf_bytes": merged["pdf_bytes"],
        "page_texts": merged["page_texts"],
        "page_images": None,
        "source_meta": merged["source_meta"],
        "workbook_analysis": merged["workbook_analysis"],
        "session_id": session_id,
        "workbook_fast_path": fast_path,
        "vision_result": None,
        "context_result": None,
        "takeoff_result": None,
        "estimation_result": None,
        "validation_result": None,
        "error": None,
    }

    final_state = None
    heartbeat = asyncio.create_task(_emit_progress_heartbeat(session_id))
    try:
        final_state = await pipeline_graph.ainvoke(initial_state)
    except Exception as exc:
        await sse.emit(session_id, {"agent": "error", "status": "error", "message": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        heartbeat.cancel()
        await sse.close(session_id)

    if final_state and final_state.get("error"):
        raise HTTPException(status_code=500, detail=final_state["error"])

    context_for_client = {
        k: v
        for k, v in (final_state.get("context_result") or {}).items()
        if k not in ("embedded_chunks", "raw_chunks")
    }

    vision_for_client = dict(final_state.get("vision_result") or {})
    if "page_images" in vision_for_client:
        del vision_for_client["page_images"]

    takeoff = final_state.get("takeoff_result")
    estimation = final_state.get("estimation_result")
    validation = final_state.get("validation_result")
    workbook = final_state.get("workbook_analysis")

    analysis_detail = build_analysis_detail(
        final_state.get("source_meta"),
        vision_for_client,
        context_for_client,
        takeoff,
        estimation,
        validation,
    )
    if workbook:
        analysis_detail["workbooks"] = workbook.get("files")
        if workbook.get("authoritativeTotalShades") is not None:
            analysis_detail.setdefault("counts", {})["windowMatrixTotal"] = workbook[
                "authoritativeTotalShades"
            ]

    quantity_schedule = build_quantity_schedule(
        takeoff,
        estimation,
        workbook,
        context_for_client,
    )
    estimation_audit = build_estimation_audit(
        source_meta=final_state.get("source_meta"),
        workbook=workbook,
        takeoff=takeoff,
        estimation=estimation,
        context=context_for_client,
        quantity_schedule=quantity_schedule,
    )
    project_summary = build_project_summary(
        source_meta=final_state.get("source_meta"),
        workbook=workbook,
        takeoff=takeoff,
        estimation=estimation,
        validation=validation,
        quantity_schedule=quantity_schedule,
        workbook_fast_path=bool(final_state.get("workbook_fast_path")),
    )

    analysis_detail["quantitySchedule"] = quantity_schedule
    analysis_detail["estimationAudit"] = estimation_audit
    analysis_detail["projectSummary"] = project_summary
    analysis_detail["lineItemSources"] = [
        {
            "windowTag": line.get("windowTag"),
            "productKind": line.get("productKind"),
            "item": line.get("systemType"),
            "quantity": line.get("quantity"),
            "location": ", ".join(
                x for x in [line.get("floor"), line.get("room")] if x
            )
            or line.get("room"),
            "width": line.get("width"),
            "height": line.get("height"),
            "squareFeet": line.get("squareFeetEach"),
            "sourceLocation": line.get("sourceLocation"),
            "countBasis": line.get("countBasis"),
            "pricingFormula": line.get("pricingFormula"),
        }
        for line in (quantity_schedule.get("lines") or [])[:150]
    ]

    client_offer = build_client_offer_package(
        estimation,
        takeoff,
        workbook,
        validation,
        context_for_client,
        quantity_schedule=quantity_schedule,
    )

    return JSONResponse(
        {
            "success": True,
            "projectSummary": project_summary,
            "clientOfferPackage": client_offer,
            "quantitySchedule": quantity_schedule,
            "estimationAudit": estimation_audit,
            "analysisDetail": analysis_detail,
            "workbook": workbook,
            "vision": vision_for_client,
            "context": context_for_client,
            "takeoff": takeoff,
            "estimation": estimation,
            "validation": validation,
        }
    )


@app.get("/api/health")
async def health():
    checks: dict = {}

    def _ok(key: str, passed: bool, detail: str = "") -> None:
        checks[key] = {"ok": passed, "detail": detail}

    _ok("static_ui", PUBLIC_DIR.is_dir() and (PUBLIC_DIR / "index.html").is_file())
    _ok("catalogue", (Path(__file__).parent / "data" / "catalogue.json").is_file())

    try:
        import json_repair  # noqa: F401

        _ok("json_repair", True)
    except ImportError:
        _ok("json_repair", False, "missing package — pip install -r requirements.txt")

    provider = get_provider()
    model = get_chat_model()
    _ok("llm_provider", provider in ("gemini", "openai"), provider)
    if provider == "gemini":
        _ok("api_key", bool(os.environ.get("GEMINI_API_KEY")), "GEMINI_API_KEY")
    else:
        _ok("api_key", bool(os.environ.get("OPENAI_API_KEY")), "OPENAI_API_KEY")

    try:
        from utils.llm_json import parse_chat_json

        parse_chat_json('{"health": true}')
        _ok("json_parser", True)
    except Exception as exc:
        _ok("json_parser", False, str(exc))

    try:
        _ok("pipeline_graph", pipeline_graph is not None)
    except Exception as exc:
        _ok("pipeline_graph", False, str(exc))

    try:
        from domain.pricing import get_pricing_policy, select_catalogue_item

        select_catalogue_item(system_type="solar roller", motorized=False)
        _ok("pricing_engine", True, get_pricing_policy().describe())
    except Exception as exc:
        _ok("pricing_engine", False, str(exc))

    all_ok = all(c["ok"] for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "provider": provider,
        "model": model,
        "checks": checks,
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#F59E0B"/>'
        '<path d="M8 24L16 8L24 24" stroke="#1E1B0E" stroke-width="2.5" fill="none"/>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 3000))
    provider = get_provider()
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        print("\n  WARNING: OPENAI_API_KEY is not set.\n")
    elif provider == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        print("\n  WARNING: GEMINI_API_KEY is not set. Add it to .env.local\n")
    else:
        print(f"\n  LLM provider: {provider} ({get_chat_model()})\n")

    print(f"\n  Estimator AI  →  http://localhost:{port}\n")
    # Railway sets RAILWAY_ENVIRONMENT. Reload is for local editing only.
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=not os.environ.get("RAILWAY_ENVIRONMENT"),
    )
