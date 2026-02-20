"""Gemini LLM integration using native structured output.

Each function wraps a single generate_content() call with a Pydantic
response_schema, replacing fragile regex+json.loads extraction with
constrained decoding that guarantees valid output.
"""

import json
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import settings
from app.models.agent_models import (
    FilterOutput,
    MatchOutput,
    ParsedCV,
    ProfessorPageOutput,
    FacultyMember,
)

T = TypeVar("T", bound=BaseModel)

MODEL = "gemini-3-flash-preview"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Get lazily-initialized Gemini client."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def _generate_structured(
    prompt: str,
    schema: type[T],
    system_instruction: str | None = None,
) -> T:
    """Generate structured output from Gemini with a Pydantic schema."""
    client = _get_client()
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
    )
    if system_instruction:
        config.system_instruction = system_instruction

    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )
    return response.parsed


# ===================================================================
# CV Parsing
# ===================================================================


async def parse_cv(text: str) -> ParsedCV:
    """Parse CV text into structured data."""
    return await _generate_structured(
        prompt=f"Parse this CV/resume:\n\n{text[:12000]}",
        schema=ParsedCV,
        system_instruction=(
            "You are a CV/resume parser. Extract structured information "
            "including name, email, education, experience, publications, "
            "skills, and research interests. Be thorough."
        ),
    )


# ===================================================================
# Faculty Extraction
# ===================================================================


async def extract_faculty(page_content: str, url: str) -> list[FacultyMember]:
    """Extract faculty members from page content."""
    result = await _generate_structured(
        prompt=f"Page URL: {url}\n\n{page_content}",
        schema=list[FacultyMember],
        system_instruction=(
            "You are a university faculty directory parser. Extract all "
            "faculty/professor information from this page. For each person, "
            "extract: name, title, department, email (if available), and "
            "profile_url (if available). Only include actual faculty members, "
            "not staff or students."
        ),
    )
    return result


async def extract_professor_details(
    page_content: str,
) -> ProfessorPageOutput:
    """Extract professor details from their profile page."""
    return await _generate_structured(
        prompt=page_content,
        schema=ProfessorPageOutput,
        system_instruction=(
            "You are a professor profile parser. Extract name, title, "
            "department, email, research areas, and bio from this profile "
            "page. Use null for missing fields."
        ),
    )


async def find_faculty_directory_url(
    page_content: str, base_url: str
) -> str | None:
    """Find faculty directory URL from page content.

    Returns the URL string or None if not found.
    Uses plain text generation since the output is a single URL.
    """
    client = _get_client()
    prompt = (
        "Find the URL to the faculty directory, people page, or faculty listing.\n"
        "The text contains '[Link Text](URL)' formatted links.\n"
        "Look for links containing words like: faculty, people, directory, "
        "staff, professors, team, members.\n"
        "Prefer links that look like directories (e.g. /faculty/, /people/).\n"
        "Return ONLY the full absolute URL, nothing else. If not found, return 'NOT_FOUND'.\n\n"
        f"Base URL: {base_url}\n\n{page_content}"
    )
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    result = (response.text or "").strip()
    if result == "NOT_FOUND" or not result.startswith("http"):
        return None
    return result


# ===================================================================
# Faculty Filtering
# ===================================================================


async def filter_faculty(
    faculty_summaries: str, interests: str
) -> FilterOutput:
    """Filter faculty list by research relevance."""
    prompt = (
        f"Select the 30 professors most likely to research: {interests}\n\n"
        f"Faculty:\n{faculty_summaries}"
    )
    return await _generate_structured(
        prompt=prompt,
        schema=FilterOutput,
        system_instruction=(
            "You are a research relevance filter. Given a list of faculty "
            "members (each with an index number, name, title, and department) "
            "and research interests, select the 30 most relevant professors. "
            "Return their index numbers in the selected_indices field."
        ),
    )


# ===================================================================
# Match Generation
# ===================================================================


async def generate_match_rankings(
    professors_json: str,
    interests: str,
    student_context: str,
) -> MatchOutput:
    """Generate ranked professor matches."""
    prompt = (
        f"Student Research Interests: {interests}\n"
        f"{student_context}\n\n"
        f"Professors:\n{professors_json}"
    )
    return await _generate_structured(
        prompt=prompt,
        schema=MatchOutput,
        system_instruction=(
            "You are a research matching expert. Analyze professors and rank "
            "them by research alignment with student interests. For each match, "
            "provide a score (0-100), specific alignment reasons, relevant "
            "publication titles the student could cite, shared keywords, and a "
            "recommendation. Return the top 10 matches maximum."
        ),
    )
