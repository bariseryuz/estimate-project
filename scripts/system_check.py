#!/usr/bin/env python3
"""
Local system check — run before demos or CI smoke.

  cd Estimator && source .venv/bin/activate && python scripts/system_check.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "OK" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


async def main() -> int:
    print("\nEstimator AI — system check\n")
    all_ok = True

    all_ok &= check("project root", ROOT.is_dir(), str(ROOT))
    all_ok &= check("public/index.html", (ROOT / "public" / "index.html").is_file())
    all_ok &= check("data/catalogue.json", (ROOT / "data" / "catalogue.json").is_file())

    try:
        import json_repair  # noqa: F401

        all_ok &= check("json-repair", True)
    except ImportError:
        all_ok &= check("json-repair", False, "pip install -r requirements.txt")

    from clients.settings import get_chat_model, get_provider

    provider = get_provider()
    model = get_chat_model()
    all_ok &= check("LLM provider", provider in ("gemini", "openai"), provider)

    if provider == "gemini":
        has_key = bool(os.environ.get("GEMINI_API_KEY"))
        all_ok &= check("GEMINI_API_KEY", has_key)
    else:
        has_key = bool(os.environ.get("OPENAI_API_KEY"))
        all_ok &= check("OPENAI_API_KEY", has_key)

    try:
        from pipeline.graph import pipeline_graph

        all_ok &= check("LangGraph pipeline", pipeline_graph is not None)
    except Exception as exc:
        all_ok &= check("LangGraph pipeline", False, str(exc))

    from utils.llm_json import parse_chat_json

    try:
        parse_chat_json('{"smoke": true}')
        all_ok &= check("JSON parser", True)
    except Exception as exc:
        all_ok &= check("JSON parser", False, str(exc))

    if has_key and provider == "gemini":
        from clients.llm import complete_json

        try:
            sample = await complete_json(
                'Respond with JSON only: {"ping": "pong"}',
                "Return the ping object.",
                temperature=0,
                max_tokens=256,
            )
            all_ok &= check(
                "Gemini JSON call",
                isinstance(sample, dict) and "ping" in sample,
                model,
            )
        except Exception as exc:
            all_ok &= check("Gemini JSON call", False, str(exc)[:200])

    print()
    if all_ok:
        print("All checks passed.\n")
        return 0
    print("Some checks failed — fix above before demo.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
