from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import gemini
from app.services.matching.routing import (
    _infer_matching_route,
    _infer_target_academic_units,
    _normalize_text,
)

_INTEREST_BOILERPLATE_PATTERNS = [
    r"\bi am interested in\b",
    r"\bi'm interested in\b",
    r"\bmy research interests include\b",
    r"\bi would like to research\b",
    r"\bi want to research\b",
    r"\bi am focused on\b",
    r"\bi'm focused on\b",
    r"\bapplications of\b",
    r"\busing\b",
    r"\bbased on\b",
    r"\bwith emphasis on\b",
]
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True)
class ResearchInterestProfile:
    """Compact structured representation of a student's interests."""

    original_interests: list[str]
    cleaned_text: str
    normalized_phrases: list[str]
    keywords: list[str]
    route_name: str
    target_units: list[str]
    discovery_terms: list[str]


def _strip_boilerplate(text: str) -> str:
    cleaned = text
    for pattern in _INTEREST_BOILERPLATE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _split_interest_segments(text: str) -> list[str]:
    text = re.sub(r"\b(and|or)\b", "|", text, flags=re.IGNORECASE)
    text = re.sub(r"[;/]", "|", text)
    text = re.sub(r",", "|", text)
    segments = []
    for raw_segment in text.split("|"):
        segment = _normalize_text(raw_segment)
        if segment and len(segment) > 2:
            segments.append(segment)
    return segments


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _extract_keywords(phrases: list[str]) -> list[str]:
    keywords: list[str] = []
    for phrase in phrases:
        for token in phrase.split():
            if len(token) <= 2 or token in _STOPWORDS:
                continue
            keywords.append(token)
    return _dedupe_preserve_order(keywords)[:12]


def _deterministic_interest_profile(research_interests: list[str]) -> ResearchInterestProfile:
    raw_text = " ".join(interest.strip() for interest in research_interests if interest.strip())
    cleaned_text = _strip_boilerplate(raw_text)
    normalized_phrases = _dedupe_preserve_order(_split_interest_segments(cleaned_text))
    if not normalized_phrases and cleaned_text:
        normalized_phrases = [cleaned_text]

    route_name = _infer_matching_route(normalized_phrases or research_interests)
    target_units = _infer_target_academic_units(normalized_phrases or research_interests, route_name)
    keywords = _extract_keywords(normalized_phrases or [cleaned_text])

    discovery_terms = _dedupe_preserve_order([
        *(normalized_phrases[:3]),
        *target_units,
        *keywords[:6],
    ])[:8]

    return ResearchInterestProfile(
        original_interests=research_interests,
        cleaned_text=_normalize_text(cleaned_text),
        normalized_phrases=normalized_phrases[:4],
        keywords=keywords,
        route_name=route_name,
        target_units=target_units,
        discovery_terms=discovery_terms,
    )


def _should_use_gemini(research_interests: list[str], deterministic: ResearchInterestProfile) -> bool:
    raw_text = " ".join(research_interests)
    if len(raw_text) > 80:
        return True
    if len(deterministic.normalized_phrases) >= 3:
        return True
    if len(deterministic.normalized_phrases) <= 1 and len(deterministic.keywords) <= 4:
        return True
    return False


async def build_research_interest_profile(
    research_interests: list[str],
) -> ResearchInterestProfile:
    """Build a structured interest profile using deterministic parsing plus Gemini."""
    deterministic = _deterministic_interest_profile(research_interests)
    if not _should_use_gemini(research_interests, deterministic):
        return deterministic

    try:
        llm_profile = await gemini.parse_research_interest_profile(
            interests_text="; ".join(research_interests)
        )
        normalized_phrases = _dedupe_preserve_order(
            llm_profile.primary_themes or deterministic.normalized_phrases
        )[:4]
        keywords = _dedupe_preserve_order(
            (llm_profile.related_keywords or []) + deterministic.keywords
        )[:12]
        route_name = llm_profile.route_hint or deterministic.route_name
        if route_name == "generic":
            route_name = deterministic.route_name
        target_units = _dedupe_preserve_order(
            (llm_profile.target_units or []) + deterministic.target_units
        )[:4]
        if not target_units:
            target_units = deterministic.target_units
        discovery_terms = _dedupe_preserve_order([
            *normalized_phrases[:3],
            *target_units,
            *keywords[:6],
        ])[:8]
        return ResearchInterestProfile(
            original_interests=research_interests,
            cleaned_text=deterministic.cleaned_text,
            normalized_phrases=normalized_phrases or deterministic.normalized_phrases,
            keywords=keywords or deterministic.keywords,
            route_name=route_name,
            target_units=target_units,
            discovery_terms=discovery_terms,
        )
    except Exception:
        return deterministic
