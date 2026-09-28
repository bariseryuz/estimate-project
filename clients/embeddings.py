"""
Gemini embeddings via the official embed_content API:

    from google import genai
    client = genai.Client()
    result = client.models.embed_content(
        model="gemini-embedding-2",
        contents="What is the meaning of life?",
    )
    print(result.embeddings)

Each text chunk gets its own embed_content call so RAG receives
one vector per document segment (gemini-embedding-2 aggregates
multiple inputs when passed in a single request).
"""

from __future__ import annotations

from clients.openai_client import get_openai_client
from clients.llm import _get_gemini_client
from clients.settings import get_embedding_dimensions, get_embedding_model, get_provider


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if get_provider() == "gemini":
        return await _gemini_embed_texts(texts)
    return await _openai_embed_texts(texts)


async def embed_query(query: str) -> list[float]:
    vectors = await embed_texts([query])
    return vectors[0]


async def _gemini_embed_one(text: str) -> list[float]:
    client = _get_gemini_client()
    model = get_embedding_model()

    kwargs: dict = {"model": model, "contents": text}

    dimensions = get_embedding_dimensions()
    if dimensions is not None:
        from google.genai import types

        kwargs["config"] = types.EmbedContentConfig(output_dimensionality=dimensions)

    response = await client.aio.models.embed_content(**kwargs)

    if not response.embeddings:
        raise ValueError("Gemini returned no embeddings")

    values = response.embeddings[0].values
    if not values:
        raise ValueError("Gemini returned an empty embedding vector")

    return list(values)


async def _gemini_embed_texts(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        vectors.append(await _gemini_embed_one(text))
    return vectors


async def _openai_embed_texts(texts: list[str]) -> list[list[float]]:
    response = await get_openai_client().embeddings.create(
        model=get_embedding_model(),
        input=texts,
    )
    return [item.embedding for item in response.data]
