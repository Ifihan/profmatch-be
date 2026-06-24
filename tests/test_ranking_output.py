"""Stage 5 (ranking) must emit objects that satisfy the MatchResult contract."""
import asyncio
from app.services.pipeline import ranking
from app.schemas.match import MatchResult


async def _fake_generate_json(prompt, pro=False):
    return {"matches": [{
        "name": "Dr. Jane Smith", "match_score": 0.87,
        "alignment_reasons": ["Both focus on NLP"],
        "shared_keywords": ["transformers", "NLP"],
        "recommendation_text": "Strong fit for your NLP interests.",
        "relevant_publication_titles": ["Transformers in Low-Resource Settings"],
    }]}


def test_stage5_output_conforms(monkeypatch):
    monkeypatch.setattr(ranking, "generate_json", _fake_generate_json)
    shortlist = [{
        "name": "Dr. Jane Smith", "designation": "Associate Professor",
        "faculty": "Computer Science", "university": "MIT",
        "email": "jsmith@mit.edu", "scholar_id": "abc123xyz",
        "listed_interests": ["machine learning", "natural language processing"],
        "metrics": {"h_index": 18, "citations": 3400, "i10_index": 30},
        "research_corpus": "Transformers in Low-Resource Settings",
        "_score": 0.81,
        "publications": [{
            "title": "Transformers in Low-Resource Settings",
            "authors": ["Jane Smith", "John Doe"], "year": 2023, "venue": "NeurIPS",
            "abstract": "We explore...", "citation_count": 42,
            "url": "https://arxiv.org/abs/xxxx"
        }],
    }]
    results = asyncio.run(ranking.run("NLP and low-resource learning", shortlist))
    mr = MatchResult.model_validate(results[0])  # raises if non-conformant
    assert mr.professor.id.startswith("prof_")
    assert mr.professor.university == "MIT"
    assert mr.professor.citation_metrics.h_index == 18
    assert mr.match_score == 0.87
    assert mr.relevant_publications[0].venue == "NeurIPS"
