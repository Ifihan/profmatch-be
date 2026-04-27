from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from app.services.matching.route_config import (
    ACADEMIC_TITLE_KEYWORDS,
    INELIGIBLE_TITLE_KEYWORDS,
    MATCHING_ROUTES,
    NON_ACADEMIC_TITLE_KEYWORDS,
    TITLE_NORMALIZATION_RULES,
    TITLE_PREFIXES,
    AcademicUnitRule,
    MatchingRouteRule,
)


def _extract_domain(url: str) -> str:
    """Extract domain from a URL."""
    try:
        domain = urlparse(url).netloc
        return domain.replace("www.", "").split("/")[0]
    except Exception:
        return ""


def _normalize_text(value: str | None) -> str:
    """Normalize free text for fuzzy comparisons."""
    if not value:
        return ""
    lowered = value.lower().replace("&", " and ").replace("/", " ")
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _normalize_person_name(name: str | None) -> str:
    """Normalize names so directory data can be merged reliably."""
    normalized = _normalize_text(name)
    tokens = [token for token in normalized.split() if token not in TITLE_PREFIXES]
    return " ".join(tokens)


def _normalize_title(title: str | None) -> str | None:
    """Normalize common academic titles while preserving unknown values."""
    normalized = _normalize_text(title)
    if not normalized:
        return None
    for needle, label in TITLE_NORMALIZATION_RULES:
        if needle in normalized:
            return label
    return " ".join(word.capitalize() for word in normalized.split())


def _is_academic_title(title: str | None) -> bool:
    """Return True when a title looks like an academic appointment."""
    normalized = _normalize_text(title)
    if not normalized:
        return True
    if any(keyword in normalized for keyword in NON_ACADEMIC_TITLE_KEYWORDS):
        return False
    return any(keyword in normalized for keyword in ACADEMIC_TITLE_KEYWORDS)


def _keyword_score(*, text: str, keywords: set[str] | frozenset[str]) -> int:
    """Score normalized keyword matches against a normalized text blob."""
    if not text or not keywords:
        return 0

    tokens = set(text.split())
    score = 0
    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword)
        if not normalized_keyword:
            continue
        if " " in normalized_keyword:
            if normalized_keyword in text:
                score += 3
        elif normalized_keyword in tokens:
            score += 1
    return score


def _all_unit_rules() -> dict[str, AcademicUnitRule]:
    """Flatten all academic-unit rules across routes."""
    rules: dict[str, AcademicUnitRule] = {}
    for route in MATCHING_ROUTES.values():
        rules.update(route.units)
    return rules


def _infer_matching_route(research_interests: list[str]) -> str:
    """Classify the search into a broad discipline route."""
    combined = " ".join(_normalize_text(interest) for interest in research_interests)
    if not combined:
        return "generic"

    route_scores: list[tuple[str, int]] = []
    for route_name, route in MATCHING_ROUTES.items():
        score = _keyword_score(text=combined, keywords=route.keywords)
        for unit in route.units.values():
            score += _keyword_score(text=combined, keywords=unit.keywords)
            score += _keyword_score(text=combined, keywords=unit.synonyms)
        if score:
            route_scores.append((route_name, score))

    if not route_scores:
        logger.debug(f"No route matched interests: {research_interests}")
        return "generic"

    route_scores.sort(key=lambda item: item[1], reverse=True)
    top_route, top_score = route_scores[0]
    second_score = route_scores[1][1] if len(route_scores) > 1 else 0

    logger.debug(
        f"Route scores: {dict(route_scores[:3])} (top={top_route}, score={top_score}, "
        f"second={second_score}, threshold=2, diff_threshold=1)"
    )

    if top_score < 2 or top_score - second_score <= 1:
        logger.debug(f"Threshold not met, falling back to generic (score={top_score})")
        return "generic"

    logger.debug(f"Matched route: {top_route} (score={top_score})")
    return top_route


def _infer_target_academic_units(
    research_interests: list[str],
    route_name: str | None = None,
) -> list[str]:
    """Infer the most relevant academic units within the selected route."""
    selected_route = route_name or _infer_matching_route(research_interests)
    combined = " ".join(_normalize_text(interest) for interest in research_interests)

    if selected_route == "generic":
        unit_rules = _all_unit_rules()
    else:
        unit_rules = MATCHING_ROUTES.get(
            selected_route,
            MatchingRouteRule(keywords=frozenset(), units={}, discovery_terms=()),
        ).units

    scores: list[tuple[str, int]] = []
    for unit_name, unit_rule in unit_rules.items():
        score = _keyword_score(text=combined, keywords=unit_rule.keywords)
        score += _keyword_score(text=combined, keywords=unit_rule.synonyms)
        if score:
            scores.append((unit_name, score))

    scores.sort(key=lambda item: item[1], reverse=True)
    return [unit for unit, _ in scores[:3]]


def _directory_search_terms(
    *,
    research_interests: list[str],
    route_name: str | None = None,
) -> list[str]:
    """Build pragmatic search terms for directory discovery."""
    selected_route = route_name or _infer_matching_route(research_interests)
    terms: list[str] = []

    if selected_route != "generic":
        route = MATCHING_ROUTES[selected_route]
        terms.extend(route.discovery_terms)

    terms.extend(_infer_target_academic_units(research_interests, selected_route))
    terms.extend(research_interests)

    seen: set[str] = set()
    deduped_terms: list[str] = []
    for term in terms:
        normalized = _normalize_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped_terms.append(term)
    return deduped_terms


def _department_relevance_score(
    *,
    department: str | None,
    target_units: list[str],
    route_name: str | None = None,
) -> float:
    """Score how well a department aligns with the inferred academic unit."""
    normalized_department = _normalize_text(department)
    if not normalized_department or not target_units:
        return 0.0

    all_units = _all_unit_rules()
    best_score = 0.0
    for unit in target_units:
        unit_rule = all_units.get(unit)
        synonyms = unit_rule.synonyms if unit_rule else frozenset({unit})
        if any(_normalize_text(synonym) in normalized_department for synonym in synonyms):
            best_score = max(best_score, 4.0)
            continue

        unit_tokens = {token for token in _normalize_text(unit).split() if len(token) > 3}
        overlap = sum(1 for token in unit_tokens if token in normalized_department)
        if overlap >= 2:
            best_score = max(best_score, 1.5 + overlap)

    if best_score > 0:
        return best_score

    if route_name and route_name != "generic":
        candidate_units = MATCHING_ROUTES[route_name].units
    else:
        candidate_units = all_units

    for other_unit, other_unit_rule in candidate_units.items():
        if other_unit in target_units:
            continue
        if any(
            _normalize_text(synonym) in normalized_department
            for synonym in other_unit_rule.synonyms
        ):
            return -4.0

    for other_unit, other_unit_rule in all_units.items():
        if other_unit in target_units or other_unit in candidate_units:
            continue
        if any(
            _normalize_text(synonym) in normalized_department
            for synonym in other_unit_rule.synonyms
        ):
            return -2.5

    return 0.0


def _merge_faculty_sources(
    *,
    openalex_faculty: list[dict[str, Any]],
    scraped_faculty: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge scraped directory metadata into OpenAlex candidates by name."""
    scraped_by_name: dict[str, dict[str, Any]] = {}
    for member in scraped_faculty:
        name_key = _normalize_person_name(member.get("name"))
        if not name_key:
            continue

        merged_member = dict(member)
        merged_member["title"] = _normalize_title(merged_member.get("title"))
        merged_member["directory_verified"] = True

        existing = scraped_by_name.get(name_key)
        if not existing:
            scraped_by_name[name_key] = merged_member
            continue

        existing_has_department = bool(existing.get("department"))
        member_has_department = bool(merged_member.get("department"))
        if member_has_department and not existing_has_department:
            scraped_by_name[name_key] = merged_member

    merged: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for faculty in openalex_faculty:
        merged_member = dict(faculty)
        name_key = _normalize_person_name(merged_member.get("name"))
        scraped_match = scraped_by_name.get(name_key)
        if scraped_match:
            merged_member["title"] = scraped_match.get("title") or merged_member.get("title")
            merged_member["department"] = scraped_match.get("department") or merged_member.get("department")
            merged_member["email"] = scraped_match.get("email") or merged_member.get("email")
            merged_member["profile_url"] = scraped_match.get("profile_url") or merged_member.get("profile_url")
            merged_member["directory_verified"] = True
        else:
            merged_member["title"] = _normalize_title(merged_member.get("title"))
            merged_member["directory_verified"] = bool(merged_member.get("directory_verified"))

        if merged_member.get("title") and not _is_academic_title(merged_member.get("title")):
            continue

        merged.append(merged_member)
        if name_key:
            seen_names.add(name_key)

    for member in scraped_faculty:
        name_key = _normalize_person_name(member.get("name"))
        if not name_key or name_key in seen_names:
            continue

        merged_member = dict(member)
        merged_member["title"] = _normalize_title(merged_member.get("title"))
        merged_member["directory_verified"] = True
        if merged_member.get("title") and not _is_academic_title(merged_member.get("title")):
            continue

        merged.append(merged_member)
        seen_names.add(name_key)

    return merged


def _is_eligible_faculty(title: str | None) -> bool:
    """Check if a faculty member is eligible."""
    if not title:
        return True
    title_lower = _normalize_text(title)
    if any(keyword in title_lower for keyword in INELIGIBLE_TITLE_KEYWORDS):
        return False
    return _is_academic_title(title)


def filter_faculty_by_relevance(
    *,
    faculty_data: list[dict[str, Any]],
    research_interests: list[str],
) -> list[dict[str, Any]]:
    """Filter faculty by topic overlap with research interests."""
    faculty_data = [faculty for faculty in faculty_data if _is_eligible_faculty(faculty.get("title"))]

    route_name = _infer_matching_route(research_interests)
    target_units = _infer_target_academic_units(research_interests, route_name)
    interest_tokens = {
        token
        for interest in research_interests
        for token in _normalize_text(interest).split()
        if len(token) > 2
    }
    interest_phrases = [_normalize_text(interest) for interest in research_interests]

    scored: list[tuple[dict[str, Any], float]] = []
    for faculty in faculty_data:
        score = _compute_topic_score(
            faculty=faculty,
            interest_tokens=interest_tokens,
            interest_phrases=interest_phrases,
            target_units=target_units,
            route_name=route_name,
        )
        scored.append((faculty, score))

    scored.sort(key=lambda item: item[1], reverse=True)
    positive_scored = [(faculty, score) for faculty, score in scored if score > 0]
    selected = positive_scored if positive_scored else scored
    limit = 20 if len(selected) > 20 else len(selected)
    return [faculty for faculty, _ in selected[:limit]]


def shortlist_faculty_for_enrichment(
    *,
    faculty_data: list[dict[str, Any]],
    research_interests: list[str],
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Keep a small high-value shortlist for expensive enrichment work."""
    if len(faculty_data) <= limit:
        return faculty_data

    route_name = _infer_matching_route(research_interests)
    target_units = _infer_target_academic_units(research_interests, route_name)
    interest_tokens = {
        token
        for interest in research_interests
        for token in _normalize_text(interest).split()
        if len(token) > 2
    }
    interest_phrases = [_normalize_text(interest) for interest in research_interests]

    scored: list[tuple[dict[str, Any], float]] = []
    for faculty in faculty_data:
        base_score = _compute_topic_score(
            faculty=faculty,
            interest_tokens=interest_tokens,
            interest_phrases=interest_phrases,
            target_units=target_units,
            route_name=route_name,
        )
        citation_bonus = min((faculty.get("cited_by_count") or 0) / 5000, 0.5) if base_score > 0 else 0.0
        scored.append((faculty, base_score + citation_bonus))

    scored.sort(key=lambda item: item[1], reverse=True)
    return [faculty for faculty, _ in scored[:limit]]


def _compute_topic_score(
    *,
    faculty: dict[str, Any],
    interest_tokens: set[str],
    interest_phrases: list[str],
    target_units: list[str] | None = None,
    route_name: str | None = None,
) -> float:
    """Compute relevance score for a faculty member against research interests."""
    score = 0.0
    target_units = target_units or []

    topic_details = faculty.get("topic_details", [])
    if topic_details:
        for detail in topic_details:
            topic_name = _normalize_text(detail.get("name"))
            subfield = _normalize_text(detail.get("subfield"))
            field = _normalize_text(detail.get("field"))
            domain = _normalize_text(detail.get("domain"))

            for phrase in interest_phrases:
                if phrase in topic_name or topic_name in phrase:
                    score += 3.0
                elif phrase in subfield or subfield in phrase:
                    score += 2.0
                elif phrase in field or field in phrase:
                    score += 1.0
                elif phrase in domain or domain in phrase:
                    score += 0.5

            for token in interest_tokens:
                if token in topic_name:
                    score += 1.0
                elif token in subfield:
                    score += 0.5
        score += _department_relevance_score(
            department=faculty.get("department"),
            target_units=target_units,
            route_name=route_name,
        )
        if faculty.get("directory_verified"):
            score += 0.75
        return score

    topics = faculty.get("topics", [])
    if topics:
        for topic in topics:
            normalized_topic = _normalize_text(topic)
            for phrase in interest_phrases:
                if phrase in normalized_topic or normalized_topic in phrase:
                    score += 3.0
            for token in interest_tokens:
                if token in normalized_topic:
                    score += 1.0
        score += _department_relevance_score(
            department=faculty.get("department"),
            target_units=target_units,
            route_name=route_name,
        )
        if faculty.get("directory_verified"):
            score += 0.75
        return score

    normalized_text = _normalize_text(" ".join([
        faculty.get("name") or "",
        faculty.get("title") or "",
        faculty.get("department") or "",
    ]))
    for token in interest_tokens:
        if token in normalized_text:
            score += 1.0

    score += _department_relevance_score(
        department=faculty.get("department"),
        target_units=target_units,
        route_name=route_name,
    )
    if faculty.get("directory_verified"):
        score += 0.75
    return score
