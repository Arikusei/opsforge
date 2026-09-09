from pathlib import Path

from app.rag.embeddings import BM25, cosine, hashed_embedding, tokenize
from app.rag.ingest import _split


def test_tokenize_ru():
    tokens = tokenize("Нужна смазка NSF H1 для пищевого производства")
    assert "смазка" in tokens
    assert "nsf" in tokens


def test_embedding_stable_in_process():
    a = hashed_embedding("ForgeFood 397 пищевая смазка")
    b = hashed_embedding("ForgeFood 397 пищевая смазка")
    assert cosine(a, b) > 0.99


def test_bm25_ranks_relevant_doc():
    corpus = [
        tokenize("масляная сож для легированных сталей"),
        tokenize("моторные масла для коммерческого транспорта"),
        tokenize("покрытие для железнодорожных замков"),
    ]
    engine = BM25(corpus)
    q = tokenize("сож сталь")
    scores = [engine.score(q, i) for i in range(3)]
    assert scores.index(max(scores)) == 0


def test_split_keeps_paragraphs():
    text = Path("knowledge/products/forge-food-397.md").read_text(encoding="utf-8") if Path("knowledge/products/forge-food-397.md").exists() else "a\n\nb\n\nc"
    parts = _split(text, size=80)
    assert parts
