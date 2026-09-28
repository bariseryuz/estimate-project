"""
RAG utilities — shared by all 4 agents.

Responsibilities:
  - chunk_text      : split raw text into overlapping segments at natural boundaries
  - embed_chunks    : attach embeddings to every chunk (batched)
  - cosine_sim      : cosine similarity between two embedding vectors
  - retrieve_relevant: embed a query and return the top-K most relevant chunks
"""

from __future__ import annotations

import re

import numpy as np

from clients.embeddings import embed_query, embed_texts


def _word_count(text: str) -> int:
    return len(text.split())


def _split_long_block(block: str, max_words: int) -> list[str]:
    """Break an oversized block on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", block.strip())
    parts: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_words = _word_count(sentence)
        if current and current_words + sentence_words > max_words:
            parts.append(" ".join(current))
            current = [sentence]
            current_words = sentence_words
        else:
            current.append(sentence)
            current_words += sentence_words

    if current:
        parts.append(" ".join(current))

    return parts or [block]


def _natural_blocks(text: str, max_words: int) -> list[str]:
    """Split text into paragraph-sized blocks, respecting natural boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text.strip()) if p.strip()]
    if not paragraphs:
        return []

    blocks: list[str] = []
    for paragraph in paragraphs:
        if _word_count(paragraph) <= max_words:
            blocks.append(paragraph)
        else:
            blocks.extend(_split_long_block(paragraph, max_words))
    return blocks


def chunk_text(text: str, chunk_size: int = 450, overlap: int = 90) -> list[dict]:
    """
    Split text into overlapping chunks at paragraph/sentence boundaries
    so embeddings capture natural document structure.
    """
    text = text.strip()
    if not text:
        return []

    blocks = _natural_blocks(text, chunk_size)
    if not blocks:
        return [{"id": 0, "text": text, "word_start": 0}]

    chunks: list[dict] = []
    start = 0

    while start < len(blocks):
        selected: list[str] = []
        words = 0
        idx = start

        while idx < len(blocks):
            block_words = _word_count(blocks[idx])
            if selected and words + block_words > chunk_size:
                break
            selected.append(blocks[idx])
            words += block_words
            idx += 1

        if not selected:
            selected = [blocks[start]]
            idx = start + 1

        chunks.append({
            "id": len(chunks),
            "text": "\n\n".join(selected),
            "word_start": start,
        })

        if idx >= len(blocks):
            break

        # Overlap: carry trailing blocks (~overlap words) into the next chunk
        overlap_blocks = 0
        overlap_words = 0
        for block in reversed(selected):
            overlap_words += _word_count(block)
            overlap_blocks += 1
            if overlap_words >= overlap:
                break

        start = max(start + 1, idx - overlap_blocks)

    return chunks


async def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Attach an embedding vector to every chunk. Processes in batches of 100."""
    if not chunks:
        return []

    batch_size = 100
    all_embeddings: list[list[float]] = []

    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i : i + batch_size]
        batch_texts = [c["text"] for c in batch_chunks]
        all_embeddings.extend(await embed_texts(batch_texts))

    return [
        {**chunk, "embedding": all_embeddings[idx]}
        for idx, chunk in enumerate(chunks)
    ]


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float arrays."""
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr) + 1e-10
    return float(np.dot(a_arr, b_arr) / denom)


async def retrieve_relevant(
    query: str,
    embedded_chunks: list[dict],
    top_k: int = 6,
) -> list[dict]:
    """
    Embed the query and return the top_k most similar chunks.
    Strips the 'embedding' field from returned objects to keep payloads small.
    """
    if not embedded_chunks:
        return []

    query_vec = await embed_query(query)

    scored = [
        {
            **{k: v for k, v in chunk.items() if k != "embedding"},
            "score": cosine_sim(query_vec, chunk["embedding"]),
        }
        for chunk in embedded_chunks
    ]

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
