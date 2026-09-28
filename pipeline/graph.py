"""
pipeline/graph.py — LangGraph graph assembly.

Two routes leave the intake node.

  FULL ROUTE (drawings present, or no structured workbook)

    START → intake → vision_analyst → context_parser → takeoff_engine
                                                        → estimation_agent → validation → END

  WORKBOOK FAST PATH (Excel-only upload with a WINDOW MATRIX TOTAL row)

    START → intake ─────────────────────────────────→ takeoff_engine
                                                        → estimation_agent → validation → END

  On the fast path the Window Matrix already states the count and the Blind QTY
  sheets already state every dimension, so vision and the RAG index would only add
  latency, cost, and a second opinion that must then be reconciled away. Take-off and
  pricing become deterministic Python; the model is used for prose and the audit.

Each edge after a node is conditional: if state["error"] is set the graph
routes directly to END, skipping all remaining agents.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from pipeline.nodes import (
    context_parser_node,
    estimation_agent_node,
    intake_node,
    takeoff_engine_node,
    validation_node,
    vision_analyst_node,
)
from pipeline.state import PipelineState


def _check_error(state: PipelineState) -> str:
    """Route to 'error' (END) if a node set state['error'], else 'ok'."""
    return "error" if state.get("error") else "ok"


def _intake_route(state: PipelineState) -> str:
    """Choose the workbook fast path or the full vision + RAG route."""
    if state.get("error"):
        return "error"
    return "fast" if state.get("workbook_fast_path") else "full"


def build_graph():
    builder = StateGraph(PipelineState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("intake",           intake_node)
    builder.add_node("vision_analyst",   vision_analyst_node)
    builder.add_node("context_parser",   context_parser_node)
    builder.add_node("takeoff_engine",   takeoff_engine_node)
    builder.add_node("estimation_agent", estimation_agent_node)
    builder.add_node("validation",       validation_node)

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_edge(START, "intake")

    builder.add_conditional_edges(
        "intake",
        _intake_route,
        {"full": "vision_analyst", "fast": "takeoff_engine", "error": END},
    )
    builder.add_conditional_edges(
        "vision_analyst",
        _check_error,
        {"ok": "context_parser", "error": END},
    )
    builder.add_conditional_edges(
        "context_parser",
        _check_error,
        {"ok": "takeoff_engine", "error": END},
    )
    builder.add_conditional_edges(
        "takeoff_engine",
        _check_error,
        {"ok": "estimation_agent", "error": END},
    )
    builder.add_conditional_edges(
        "estimation_agent",
        _check_error,
        {"ok": "validation", "error": END},
    )
    builder.add_edge("validation", END)

    return builder.compile()


# Compiled graph — import and call .ainvoke() from main.py
pipeline_graph = build_graph()
