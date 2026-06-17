"""Stage 4: embedding-based semantic scoring (student profile vs each professor)."""
import numpy as np
from app.services.gemini import embed

# Gemini's embed endpoint caps texts per call, so batch large faculty lists.
_EMBED_BATCH = 100


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


async def _embed_batched(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        vectors.extend(await embed(texts[i:i + _EMBED_BATCH]))
    return vectors


async def run(profile_text: str, enriched: list[dict]) -> list[dict]:
    if not enriched:
        return []
    corpora = [p.get("research_corpus") or p.get("name", "") for p in enriched]
    student_vec = np.array((await embed([profile_text]))[0])
    vectors = await _embed_batched(corpora)
    scored = []
    for prof, vec in zip(enriched, vectors):
        prof = dict(prof)
        prof["_score"] = _cosine(student_vec, np.array(vec))
        scored.append(prof)
    scored.sort(key=lambda p: p["_score"], reverse=True)
    return scored
