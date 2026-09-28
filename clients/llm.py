"""
Unified LLM client — supports OpenAI and Gemini via LLM_PROVIDER env var.

Set in .env.local:
  LLM_PROVIDER=gemini
  GEMINI_API_KEY=your-key
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Any, Union, cast

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionUserMessageParam

from clients.openai_client import get_openai_client
from clients.settings import get_chat_model, get_provider
from utils.llm_json import parse_chat_json

UserContent = Union[str, list[dict[str, Any]]]


@lru_cache(maxsize=1)
def _get_gemini_client():
    import os

    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env.local for Gemini testing."
        )
    return genai.Client(api_key=api_key)


def _gemini_parts(user_content: UserContent) -> list[Any]:
    from google.genai import types

    if isinstance(user_content, str):
        return [types.Part.from_text(text=user_content)]

    parts: list[Any] = []
    for block in user_content:
        if block.get("type") == "text":
            parts.append(types.Part.from_text(text=block["text"]))
        elif block.get("type") == "image_url":
            url = block["image_url"]["url"]
            header, b64 = url.split(",", 1)
            mime = header.split(":")[1].split(";")[0]
            parts.append(
                types.Part.from_bytes(data=base64.b64decode(b64), mime_type=mime)
            )
    return parts


def _openai_messages(
    system_prompt: str,
    user_content: UserContent,
) -> list[ChatCompletionMessageParam]:
    if isinstance(user_content, str):
        user_message: ChatCompletionUserMessageParam = {
            "role": "user",
            "content": user_content,
        }
    else:
        # Vision payloads are built by context_parser (text + image_url blocks).
        user_message = cast(
            ChatCompletionUserMessageParam,
            {"role": "user", "content": user_content},
        )

    return [
        {"role": "system", "content": system_prompt},
        user_message,
    ]


_COMPACT_JSON_SUFFIX = (
    "\n\nIMPORTANT: Return ONLY valid JSON (no markdown). "
    "Keep every string value under 120 characters. No newlines inside strings."
)


def _append_compact_json_hint(user_content: UserContent) -> UserContent:
    if isinstance(user_content, str):
        return user_content + _COMPACT_JSON_SUFFIX
    hinted: list[dict[str, Any]] = []
    appended = False
    for block in user_content:
        if not appended and block.get("type") == "text":
            hinted.append(
                {"type": "text", "text": block.get("text", "") + _COMPACT_JSON_SUFFIX}
            )
            appended = True
        else:
            hinted.append(block)
    if not appended:
        hinted.insert(0, {"type": "text", "text": _COMPACT_JSON_SUFFIX.strip()})
    return hinted


async def complete_json(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float = 0.2,
    max_tokens: int = 4000,
) -> dict:
    provider = get_provider()
    try:
        if provider == "gemini":
            return await _gemini_complete_json(
                system_prompt, user_content, temperature=temperature, max_tokens=max_tokens
            )
        return await _openai_complete_json(
            system_prompt, user_content, temperature=temperature, max_tokens=max_tokens
        )
    except ValueError:
        retry_content = _append_compact_json_hint(user_content)
        retry_temp = min(temperature, 0.08)
        if provider == "gemini":
            return await _gemini_complete_json(
                system_prompt,
                retry_content,
                temperature=retry_temp,
                max_tokens=max(max_tokens, 8192),
            )
        return await _openai_complete_json(
            system_prompt,
            retry_content,
            temperature=retry_temp,
            max_tokens=max(max_tokens, 8192),
        )


async def _openai_complete_json(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float,
    max_tokens: int,
) -> dict:
    response = await get_openai_client().chat.completions.create(
        model=get_chat_model(),
        messages=_openai_messages(system_prompt, user_content),
        response_format={"type": "json_object"},
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not response.choices:
        raise ValueError("Model returned no choices — retry or lower max_tokens")
    return parse_chat_json(response.choices[0].message.content)


async def _gemini_complete_json(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float,
    max_tokens: int,
) -> dict:
    from google.genai import types

    client = _get_gemini_client()
    response = await client.aio.models.generate_content(
        model=get_chat_model(),
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"{system_prompt}\n\n"),
                    *_gemini_parts(user_content),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    text = _gemini_text(response)
    return parse_chat_json(text)


def _gemini_text(response: Any) -> str:
    """
    Pull text out of a Gemini response, explaining *why* when there is none.

    An empty `response.text` usually means the answer was blocked by a safety filter
    or truncated at the token limit. Surfacing the finish/block reason turns an
    opaque "empty response" into something the operator can act on.
    """
    text = getattr(response, "text", None)
    if text and str(text).strip():
        return str(text)

    reasons: list[str] = []
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        reasons.append(f"prompt blocked ({block_reason})")

    for candidate in getattr(response, "candidates", None) or []:
        finish = getattr(candidate, "finish_reason", None)
        if finish:
            reasons.append(f"finish_reason={finish}")
        # Parts can carry text even when the convenience accessor returns nothing.
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        joined = "".join(str(getattr(p, "text", "") or "") for p in parts).strip()
        if joined:
            return joined

    detail = "; ".join(dict.fromkeys(reasons))
    raise ValueError(
        "Model returned empty response content"
        + (f" ({detail})" if detail else " — try again or reduce the page count")
    )


async def complete_text(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float = 0.1,
    max_tokens: int = 8000,
) -> str:
    provider = get_provider()
    if provider == "gemini":
        return await _gemini_complete_text(
            system_prompt, user_content, temperature=temperature, max_tokens=max_tokens
        )
    return await _openai_complete_text(
        system_prompt, user_content, temperature=temperature, max_tokens=max_tokens
    )


async def _openai_complete_text(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    response = await get_openai_client().chat.completions.create(
        model=get_chat_model(),
        messages=_openai_messages(system_prompt, user_content),
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if not response.choices:
        raise ValueError("Model returned no choices — retry or lower max_tokens")
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise ValueError("Model returned empty response content")
    return content.strip()


async def _gemini_complete_text(
    system_prompt: str,
    user_content: UserContent,
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    from google.genai import types

    client = _get_gemini_client()
    response = await client.aio.models.generate_content(
        model=get_chat_model(),
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"{system_prompt}\n\n"),
                    *_gemini_parts(user_content),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    return _gemini_text(response).strip()
