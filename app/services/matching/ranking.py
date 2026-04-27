from __future__ import annotations

import json
import logging

from app.models import MatchResult, ProfessorProfile, StudentProfile

logger = logging.getLogger(__name__)
from app.services import gemini
from app.services.matching.interests import ResearchInterestProfile
from app.services.matching.routing import (
    _compute_topic_score,
    _infer_matching_route,
    _infer_target_academic_units,
    _normalize_text,
)


async def generate_matches(
    *,
    professors: list[ProfessorProfile],
    research_interests: list[str],
    student_profile: StudentProfile | None,
    research_profile: ResearchInterestProfile | None = None,
) -> list[MatchResult]:
    """Generate ranked matches using deterministic scoring plus LLM reranking."""
    if not professors:
        return []

    effective_phrases = (
        research_profile.normalized_phrases
        if research_profile and research_profile.normalized_phrases
        else research_interests
    )
    effective_keywords = (
        research_profile.keywords
        if research_profile and research_profile.keywords
        else []
    )
    interests_str = ", ".join(effective_phrases or research_interests)
    route_name = research_profile.route_name if research_profile else _infer_matching_route(research_interests)
    target_units = (
        research_profile.target_units
        if research_profile and research_profile.target_units
        else _infer_target_academic_units(research_interests, route_name)
    )
    interest_tokens = {
        token
        for interest in effective_phrases
        for token in _normalize_text(interest).split()
        if len(token) > 2
    }
    interest_tokens.update(_normalize_text(keyword) for keyword in effective_keywords if _normalize_text(keyword))
    interest_phrases = [_normalize_text(interest) for interest in effective_phrases]

    student_context = ""
    if student_profile:
        student_context = (
            "\nStudent Background:\n"
            f"- Education: {json.dumps([e.model_dump() for e in student_profile.education], default=str)}\n"
            f"- Skills: {', '.join(student_profile.skills)}\n"
            f"- Publications: {len(student_profile.publications)} papers\n"
            f"- Keywords: {', '.join(student_profile.extracted_keywords[:10])}"
        )

    base_scores: dict[str, float] = {}
    professor_summaries = []
    for professor in professors:
        score_input = {
            "topics": professor.research_areas,
            "department": professor.department,
            "title": professor.title,
            "directory_verified": bool(professor.directory_url or professor.website or professor.title),
        }
        base_score = _compute_topic_score(
            faculty=score_input,
            interest_tokens=interest_tokens,
            interest_phrases=interest_phrases,
            target_units=target_units,
            route_name=route_name,
        )
        publication_text = " ".join(publication.title for publication in professor.publications[:5])
        normalized_publication_text = _normalize_text(publication_text)
        for token in interest_tokens:
            if token in normalized_publication_text:
                base_score += 0.25
        base_scores[str(professor.id)] = base_score

        professor_summaries.append({
            "id": str(professor.id),
            "name": professor.name,
            "title": professor.title,
            "department": professor.department,
            "research_areas": professor.research_areas[:5],
            "recent_papers": [publication.title for publication in professor.publications[:3]],
            "h_index": professor.citation_metrics.h_index if professor.citation_metrics else 0,
            "directory_verified": bool(professor.directory_url or professor.website or professor.title),
            "base_relevance_score": round(base_score, 2),
        })

    professor_summaries.sort(key=lambda summary: summary["base_relevance_score"], reverse=True)
    professors_json = json.dumps(professor_summaries, indent=2)

    logger.info(
        f"Matching pipeline: {len(professors)} professors, route={route_name}, "
        f"target_units={target_units}, interests={interests_str}"
    )

    try:
        result = await gemini.generate_match_rankings(
            professors_json=professors_json,
            interests=interests_str,
            student_context=student_context,
        )
        logger.info(f"LLM ranking succeeded: {len(result.matches)} matches returned")
        return _merge_llm_and_deterministic_scores(
            professors=professors,
            llm_matches=result.matches,
            base_scores=base_scores,
        )
    except Exception as e:
        logger.error(
            f"LLM ranking failed: {type(e).__name__}: {str(e)}\n"
            f"Fallback: using deterministic ranking with {len(professors)} professors"
        )
        return _deterministic_fallback_matches(
            professors=professors,
            base_scores=base_scores,
            research_interests=effective_phrases or research_interests,
            research_keywords=effective_keywords,
        )


def _merge_llm_and_deterministic_scores(
    *,
    professors: list[ProfessorProfile],
    llm_matches: list,
    base_scores: dict[str, float],
) -> list[MatchResult]:
    """Combine LLM scores with deterministic base scores."""
    matches = []
    professor_map = {str(professor.id): professor for professor in professors}
    max_base_score = max(base_scores.values(), default=0.0)

    for llm_match in llm_matches[:10]:
        professor = professor_map.get(llm_match.professor_id)
        if not professor:
            continue

        relevant_publications = []
        gemini_titles_lower = [title.lower() for title in llm_match.relevant_publication_titles]
        for publication in professor.publications:
            normalized_title = publication.title.lower()
            if any(
                gemini_title in normalized_title or normalized_title in gemini_title
                for gemini_title in gemini_titles_lower
            ):
                relevant_publications.append(publication)

        if not relevant_publications and professor.publications:
            relevant_publications = sorted(
                professor.publications,
                key=lambda publication: publication.citation_count,
                reverse=True,
            )[:5]

        base_score = base_scores.get(str(professor.id), 0.0)
        normalized_base_score = (base_score / max_base_score) * 100 if max_base_score > 0 else 0.0
        final_score = round((llm_match.match_score * 0.75) + (normalized_base_score * 0.25), 1)

        matches.append(
            MatchResult(
                professor=professor,
                match_score=final_score,
                alignment_reasons=llm_match.alignment_reasons,
                relevant_publications=relevant_publications,
                shared_keywords=llm_match.shared_keywords,
                recommendation_text=llm_match.recommendation_text,
            )
        )

    return sorted(matches, key=lambda match: match.match_score, reverse=True)


def _deterministic_fallback_matches(
    *,
    professors: list[ProfessorProfile],
    base_scores: dict[str, float],
    research_interests: list[str] | None = None,
    research_keywords: list[str] | None = None,
) -> list[MatchResult]:
    """Produce deterministic matches if the LLM step fails.

    Uses base_scores (publication/citation metrics) combined with keyword
    matching against research interests for better ranking.
    """
    # Build keyword set from research interests
    interest_keywords = set()
    if research_interests:
        for interest in research_interests:
            interest_keywords.update(_normalize_text(interest).split())
    if research_keywords:
        for keyword in research_keywords:
            normalized_keyword = _normalize_text(keyword)
            if normalized_keyword:
                interest_keywords.add(normalized_keyword)

    # Score professors by base relevance + keyword overlap
    scored_professors = []
    for professor in professors:
        base_score = base_scores.get(str(professor.id), 0.0)

        # Count keyword matches in research areas
        keyword_matches = 0
        if interest_keywords and professor.research_areas:
            research_text = " ".join(professor.research_areas).lower()
            keyword_matches = sum(
                1 for keyword in interest_keywords
                if keyword in research_text
            )

        # Combined score: 70% base relevance + 30% keyword match
        combined_score = (base_score * 0.7) + (keyword_matches * 10 * 0.3)
        scored_professors.append((professor, base_score, combined_score, keyword_matches))

    # Sort by combined score and take top 10
    ranked_professors = sorted(
        scored_professors,
        key=lambda x: x[2],
        reverse=True,
    )[:10]

    max_base_score = max((score for _, score, _, _ in scored_professors), default=1.0)

    fallback_matches = []
    for professor, base_score, combined_score, keyword_matches in ranked_professors:
        # Normalize combined score to 0-100 range
        max_combined = max((score for _, _, score, _ in scored_professors), default=1.0)
        normalized_score = (combined_score / max_combined * 100) if max_combined > 0 else 50.0

        reasons = ["Ranked using publication metrics and research interest keywords."]
        if keyword_matches > 0:
            reasons.append(f"Research areas match {keyword_matches} keywords from your interests.")

        fallback_matches.append(
            MatchResult(
                professor=professor,
                match_score=round(normalized_score, 1),
                alignment_reasons=reasons,
                relevant_publications=sorted(
                    professor.publications,
                    key=lambda publication: publication.citation_count,
                    reverse=True,
                )[:5],
                shared_keywords=[kw for kw in interest_keywords if kw in " ".join(professor.research_areas or []).lower()],
                recommendation_text=(
                    "This result was generated with the deterministic fallback ranker "
                    "because the language-model ranking step was unavailable."
                ),
            )
        )

    return fallback_matches
