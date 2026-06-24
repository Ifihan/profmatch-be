"""Stage 4 scoring: embeddings are batched and results sorted by similarity."""
from app.services.pipeline import scoring


async def test_embeddings_are_batched(monkeypatch):
    sizes: list[int] = []

    async def fake_embed(texts):
        sizes.append(len(texts))
        # deterministic vectors; longer corpus -> larger first component
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    monkeypatch.setattr(scoring, "embed", fake_embed)
    enriched = [{"name": f"P{i}", "research_corpus": "x" * (i % 11)} for i in range(150)]
    out = await scoring.run("student", enriched)

    assert len(out) == 150
    # profile embedded alone, then corpora in 100-sized batches
    assert sizes == [1, 100, 50]
    scores = [p["_score"] for p in out]
    assert scores == sorted(scores, reverse=True)


async def test_empty_faculty_short_circuits(monkeypatch):
    called = False

    async def fake_embed(texts):
        nonlocal called
        called = True
        return [[0.0]]

    monkeypatch.setattr(scoring, "embed", fake_embed)
    assert await scoring.run("student", []) == []
    assert called is False
