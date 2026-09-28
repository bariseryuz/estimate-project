"""Parse JSON from LLM responses (markdown fences, truncation, repair)."""

from __future__ import annotations

import json
import re
from typing import Any

#: Model responses that are a bare JSON array are returned under this key.
LIST_WRAPPER_KEY = "items"


def parse_chat_json(content: str | None) -> dict[str, Any]:
    """
    Best-effort extraction of one JSON object from a model response.

    Tries, in order: the fenced block, the widest brace span, the first brace to end
    of text (truncated responses), each fenced block individually, and finally
    `json_repair`. A bare array is wrapped so callers always receive a dict.
    """
    if not content or not str(content).strip():
        raise ValueError("Model returned empty response content")

    raw = str(content).strip()
    last_err: str | None = None

    for text in _candidates(raw):
        for attempt in (text, _strip_trailing_commas(text)):
            try:
                return _as_dict(json.loads(attempt))
            except json.JSONDecodeError as exc:
                last_err = str(exc)
            except ValueError as exc:
                last_err = str(exc)

        repaired = _repair(text)
        if repaired is not None:
            return repaired

        closed = _close_truncated_json(text)
        if closed:
            try:
                return _as_dict(json.loads(closed))
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = str(exc)

    detail = last_err or "unknown parse error"
    raise ValueError(f"Could not parse JSON from model response: {detail}")


def _candidates(raw: str) -> list[str]:
    """Ordered list of substrings that might be the JSON payload."""
    out: list[str] = []

    def add(text: str | None) -> None:
        if text and text.strip() and text.strip() not in out:
            out.append(text.strip())

    # A whole-response array must be tried before the brace scan, which would
    # otherwise pull a single element out of the list and discard the rest.
    unfenced = _strip_fence(raw)
    if unfenced.startswith("["):
        add(unfenced)

    add(_extract_json_text(raw))

    # Truncated model output often has no closing brace — also try from first "{" to EOF.
    start = raw.find("{")
    if start != -1:
        add(raw[start:])

    # Some models emit prose, then a fenced block, then more prose; try each block.
    for fence in re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
        add(fence)

    add(raw)
    return out


def _strip_fence(content: str) -> str:
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    return (fence.group(1) if fence else content).strip()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {LIST_WRAPPER_KEY: value}
    raise ValueError(f"Model returned {type(value).__name__}, expected a JSON object")


def _repair(text: str) -> dict[str, Any] | None:
    try:
        import json_repair
    except ImportError:
        return None

    for loader in (json_repair.loads, lambda t: json_repair.repair_json(t, return_objects=True)):
        try:
            candidate = loader(text)
        except Exception:
            continue
        if isinstance(candidate, (dict, list)) and candidate:
            try:
                return _as_dict(candidate)
            except ValueError:
                continue
    return None


def _strip_trailing_commas(text: str) -> str:
    """Remove commas that sit directly before a closing brace or bracket."""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _extract_json_text(content: str) -> str:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def _close_truncated_json(text: str) -> str | None:
    """Best-effort fix for truncated JSON object (unterminated string)."""
    if not text.strip().startswith("{"):
        return None
    trimmed = text.rstrip()
    if trimmed.endswith("}"):
        return None
    # Drop trailing partial key/string and close brackets
    trimmed = re.sub(r',?\s*"[^"]*$', "", trimmed)
    trimmed = re.sub(r",?\s*$", "", trimmed)
    open_braces = trimmed.count("{") - trimmed.count("}")
    open_brackets = trimmed.count("[") - trimmed.count("]")
    trimmed += "]" * max(0, open_brackets)
    trimmed += "}" * max(0, open_braces)
    return trimmed if trimmed.endswith("}") else None
