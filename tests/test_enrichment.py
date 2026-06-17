"""Enrichment chain with mocked HTTP — OpenAlex primary, Crossref tertiary."""
import httpx
import respx

from app.services.pipeline import enrichment

OA = "https://api.openalex.org"


@respx.mock
async def test_openalex_primary_with_abstract_reconstruction():
    respx.get(url__startswith=f"{OA}/authors").mock(
        return_value=httpx.Response(200, json={"results": [{
            "id": f"{OA}/A1",
            "cited_by_count": 1000,
            "summary_stats": {"h_index": 42, "i10_index": 10},
        }]})
    )
    respx.get(url__startswith=f"{OA}/works").mock(
        return_value=httpx.Response(200, json={"results": [{
            "title": "Graphs and Things",
            "publication_year": 2020,
            "cited_by_count": 5,
            "primary_location": {"source": {"display_name": "NeurIPS"},
                                 "landing_page_url": "http://x"},
            "authorships": [{"author": {"display_name": "Jane Doe"}}],
            "abstract_inverted_index": {"Hello": [0], "world": [1]},
        }]})
    )

    out = await enrichment.run([{"name": "Jane Doe", "university": "Stanford University"}])
    prof = out[0]
    assert prof["metrics"]["h_index"] == 42
    assert prof["metrics"]["citations"] == 1000
    pub = prof["publications"][0]
    assert pub["title"] == "Graphs and Things"
    assert pub["venue"] == "NeurIPS"
    assert pub["abstract"] == "Hello world"  # reconstructed from inverted index
    assert "Graphs and Things" in prof["research_corpus"]


@respx.mock
async def test_falls_back_to_crossref_when_openalex_and_s2_empty():
    respx.get(url__startswith=f"{OA}/authors").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(url__startswith="https://pub.orcid.org").mock(
        return_value=httpx.Response(200, json={"expanded-result": []})
    )
    respx.get(url__startswith="https://api.semanticscholar.org").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.get(url__startswith="https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": [{
            "title": ["Postcolonial Readings"],
            "author": [{"given": "A", "family": "Scholar"}],
            "issued": {"date-parts": [[2019]]},
            "container-title": ["Journal of Theory"],
            "is-referenced-by-count": 7,
            "URL": "http://doi/x",
        }]}})
    )

    out = await enrichment.run([{"name": "A Scholar", "university": "Harvard University"}])
    prof = out[0]
    assert prof["metrics"] == {}  # Crossref carries no author metrics
    assert prof["publications"][0]["title"] == "Postcolonial Readings"
    assert prof["publications"][0]["citation_count"] == 7


@respx.mock
async def test_no_match_leaves_empty_but_uses_listed_interests_as_corpus():
    respx.get(url__startswith=f"{OA}/authors").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(url__startswith="https://pub.orcid.org").mock(
        return_value=httpx.Response(200, json={"expanded-result": []})
    )
    respx.get(url__startswith="https://api.semanticscholar.org").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.get(url__startswith="https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )

    out = await enrichment.run([{
        "name": "Unknown Person", "university": "Nowhere",
        "listed_interests": ["medieval history", "manuscripts"],
    }])
    prof = out[0]
    assert prof["publications"] == []
    assert prof["research_corpus"] == "medieval history manuscripts"
