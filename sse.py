"""
sse.py — Shared Server-Sent Events queue management.

Both main.py (HTTP endpoint) and pipeline/nodes.py (LangGraph nodes)
import from here so progress messages flow from the graph to the browser.
"""

from __future__ import annotations

import asyncio

_queues: dict[str, asyncio.Queue] = {}


def get_or_create(session_id: str) -> asyncio.Queue:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue()
    return _queues[session_id]


async def emit(session_id: str, payload: dict) -> None:
    queue = _queues.get(session_id)
    if queue:
        await queue.put(payload)


async def close(session_id: str) -> None:
    """Send sentinel None to signal the SSE stream to end."""
    queue = _queues.get(session_id)
    if queue:
        await queue.put(None)


def remove(session_id: str) -> None:
    _queues.pop(session_id, None)
