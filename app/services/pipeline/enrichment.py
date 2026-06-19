"""Stage 3: enrich professors with publications + metrics concurrently (OpenAlex → Semantic Scholar → Crossref)."""
import asyncio
import httpx
from app.services.enrichment import openalex, fallback

_CONCURRENCY = 8


async def run(faculty: list[dict]) -> list[dict]:
    sem = asyncio.Semaphore(_CONCURRENCY)
    # Discovery resolved + verified the institution by domain; scope lookups to it.
    institution_id = next((f.get("institution_id") for f in faculty if f.get("institution_id")), None)
    async with httpx.AsyncClient() as client:
        async def enrich_one(prof: dict) -> dict:
            async with sem:
                return await _enrich(client, prof, institution_id)
        return await asyncio.gather(*(enrich_one(p) for p in faculty))


async def _resolve_openalex_author(
    client: httpx.AsyncClient, prof: dict, institution_id: str | None
) -> dict | None:
    """Find the OpenAlex author record, preferring precise ids over name search."""
    # Discovery's OpenAlex fallback already knows the exact author.
    if prof.get("openalex_id"):
        try:
            author = await openalex.get_author(client, prof["openalex_id"])
            if author:
                return author
        except Exception:
            pass
    name = prof.get("name", "")
    # Institution-scoped name search is the reliable primary path.
    try:
        author = await openalex.find_author(client, name, institution_id)
        if author:
            return author
    except Exception:
        pass
    # ORCID disambiguates common names the scoped search missed; fallback-only.
    try:
        orcid = await fallback.orcid_id(client, name, prof.get("university"))
        if orcid:
            author = await openalex.find_author_by_orcid(client, orcid)
            if author:
                return author
    except Exception:
        pass
    # Last resort: name-only search.
    try:
        return await openalex.find_author(client, name) if institution_id else None
    except Exception:
        return None


async def _enrich(client: httpx.AsyncClient, prof: dict, institution_id: str | None) -> dict:
    name = prof.get("name", "")
    out = dict(prof)

    author = await _resolve_openalex_author(client, prof, institution_id)
    if author:
        try:
            works = await openalex.author_works(client, author["id"], limit=8)
            out["metrics"] = openalex.parse_metrics(author)
            out["publications"] = [openalex.parse_work(w) for w in works]
            out["research_corpus"] = " . ".join(
                w.get("title", "") for w in works if w.get("title")
            )
            return out
        except Exception:
            pass

    try:
        s2 = await fallback.semantic_scholar_author(client, name)
        if s2:
            out["metrics"] = {
                "h_index": s2.get("hIndex"),
                "citations": s2.get("citationCount"),
                "i10_index": None,
            }
            papers = s2.get("papers", []) or []
            out["publications"] = [
                {
                    "title": p.get("title"),
                    "authors": [],
                    "year": p.get("year"),
                    "venue": p.get("venue"),
                    "abstract": None,
                    "citation_count": p.get("citationCount"),
                    "url": p.get("url"),
                }
                for p in papers[:8]
            ]
            out["research_corpus"] = " . ".join(
                p.get("title", "") for p in papers if p.get("title")
            )
            return out
    except Exception:
        pass

    try:
        pubs = [p for p in await fallback.crossref_works(client, name) if p.get("title")]
        if pubs:
            out["metrics"] = {}
            out["publications"] = pubs
            out["research_corpus"] = " . ".join(p["title"] for p in pubs)
            return out
    except Exception:
        pass

    out.setdefault("metrics", {})
    out.setdefault("publications", [])
    out["research_corpus"] = " ".join(prof.get("listed_interests", []))
    return out
