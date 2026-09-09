from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import KnowledgeChunk
from app.rag.embeddings import BM25, cosine, hashed_embedding, tokenize


def retrieve(db: Session, query: str, limit: int = 5) -> list[dict]:
    chunks = db.query(KnowledgeChunk).all()
    if not chunks:
        return []
    q_tokens = tokenize(query)
    q_vec = hashed_embedding(query)
    bm25 = BM25([tokenize(c.content) for c in chunks])
    ranked: list[tuple[float, KnowledgeChunk]] = []
    for i, chunk in enumerate(chunks):
        lexical = bm25.score(q_tokens, i)
        try:
            vec = json.loads(chunk.embedding) if chunk.embedding else []
        except json.JSONDecodeError:
            vec = []
        dense = cosine(q_vec, vec)
        score = 0.65 * lexical + 0.35 * dense * 10
        ranked.append((score, chunk))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, chunk in ranked[:limit]:
        if score <= 0:
            continue
        out.append(
            {
                "id": chunk.id,
                "title": chunk.title,
                "source": chunk.source,
                "content": chunk.content,
                "score": round(float(score), 3),
            }
        )
    return out
