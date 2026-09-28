"""Lazy OpenAI client — avoids crashing on import when OPENAI_API_KEY is unset."""

from __future__ import annotations

import os
from functools import lru_cache

from openai import AsyncOpenAI


@lru_cache(maxsize=1)
def get_openai_client() -> AsyncOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return AsyncOpenAI(api_key=api_key)
