"""Stage 5: LLM re-rank + explanation over the shortlist; emits MatchResult dicts."""
import hashlib
import json
from datetime import datetime, timezone
from app.services.gemini import generate_json

_PROMPT = """A student is looking for a research supervisor.

Student profile:
{profile}

Candidate professors (JSON):
{candidates}

For EACH candidate, produce a final match assessment. Return ONLY JSON:
{{"matches": [
  {{"name": str,
    "match_score": float (0.0-1.0),
    "alignment_reasons": [str],
    "shared_keywords": [str],
    "recommendation_text": str,
    "relevant_publication_titles": [str]
  }}
]}}

Base alignment_reasons and shared_keywords on genuine overlap between the
student's themes/methods and the professor's publications. recommendation_text
is one or two sentences addressed to the student. Order by match_score desc.
"""


def _prof_id(name: str, university: str | None) -> str:
    raw = f"{name}|{university or ''}".lower().encode()
    return "prof_" + hashlib.sha1(raw).hexdigest()[:10]


def _to_publication(pub: dict) -> dict:
    return {
        "title": pub.get("title") or "",
        "authors": pub.get("authors", []),
        "year": pub.get("year"),
        "venue": pub.get("venue"),
        "abstract": pub.get("abstract"),
        "citation_count": pub.get("citation_count"),
        "url": pub.get("url"),
    }


async def run(profile_text: str, shortlist: list[dict], top_n: int = 15) -> list[dict]:
    shortlist = shortlist[:top_n]
    slim = [
        {
            "name": p.get("name"),
            "title": p.get("designation"),
            "department": p.get("faculty"),
            "research_corpus": (p.get("research_corpus") or "")[:1500],
            "publications": [pp.get("title") for pp in p.get("publications", [])[:5]],
        }
        for p in shortlist
    ]
    result = await generate_json(
        _PROMPT.format(profile=profile_text[:4000], candidates=json.dumps(slim))
    )
    by_name = {m.get("name"): m for m in result.get("matches", [])}

    now = datetime.now(timezone.utc)
    final: list[dict] = []
    for p in shortlist:
        m = by_name.get(p.get("name"), {})
        all_pubs = [_to_publication(pub) for pub in p.get("publications", [])]

        # relevant_publications: ones the LLM flagged by title, else top 3
        flagged_titles = {t.lower() for t in m.get("relevant_publication_titles", [])}
        relevant = [pub for pub in all_pubs if pub["title"].lower() in flagged_titles]
        if not relevant:
            relevant = all_pubs[:3]

        metrics = p.get("metrics") or {}
        citation_metrics = None
        if metrics.get("h_index") is not None or metrics.get("citations") is not None:
            citation_metrics = {
                "h_index": metrics.get("h_index"),
                "total_citations": metrics.get("citations"),
            }

        professor = {
            "id": _prof_id(p.get("name", ""), p.get("university")),
            "name": p.get("name"),
            "title": p.get("designation"),
            "department": p.get("faculty"),
            "university": p.get("university"),
            "email": p.get("email"),
            "scholar_id": p.get("scholar_id"),
            "research_areas": p.get("listed_interests", []),
            "publications": all_pubs,
            "citation_metrics": citation_metrics,
            "last_updated": now.isoformat(),
        }

        match_score = m.get("match_score")
        if match_score is None:
            match_score = round(float(p.get("_score", 0.0)), 4)

        final.append({
            "professor": professor,
            "match_score": match_score,
            "alignment_reasons": m.get("alignment_reasons", []),
            "relevant_publications": relevant,
            "shared_keywords": m.get("shared_keywords", []),
            "recommendation_text": m.get("recommendation_text", ""),
        })

    final.sort(key=lambda x: x["match_score"], reverse=True)
    return final
