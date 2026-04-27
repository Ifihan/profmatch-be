from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app.models import Education, Experience, Publication, StudentProfile
from app.services import gemini, tools
from app.services.session_store import get_session
from app.utils.storage import get_file_path


def to_str(val) -> str | None:
    """Convert value to string, joining lists if needed."""
    if val is None:
        return None
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    return str(val)


def to_list(val) -> list[str]:
    """Convert value to list, splitting strings if needed."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [v.strip() for v in val.split(",")]
    return [str(val)]


def to_int(val) -> int:
    """Convert value to int safely."""
    if val is None:
        return 0
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


async def parse_student_documents(
    *,
    session_id: str,
    file_ids: list[str],
    research_interests: list[str],
) -> StudentProfile:
    """Parse uploaded documents to build a student profile."""
    education = []
    experience = []
    publications = []
    skills = []
    extracted_keywords = list(research_interests)

    session_data = await get_session(session_id=session_id) or {}
    pre_parsed = session_data.get("parsed_cvs", {})

    async def parse_single_file(file_id: str) -> dict | None:
        if file_id in pre_parsed:
            return pre_parsed[file_id]
        file_path = await get_file_path(session_id, file_id)
        if not file_path:
            return None
        text = tools.extract_text_from_file(file_path=str(file_path))
        parsed = await gemini.parse_cv(text=text)
        return parsed.model_dump()

    results = await asyncio.gather(
        *(parse_single_file(file_id) for file_id in file_ids),
        return_exceptions=True,
    )

    for result in results:
        if not isinstance(result, dict):
            continue
        cv_data = result

        for edu in cv_data.get("education", []) or []:
            if not isinstance(edu, dict):
                continue
            education.append(
                Education(
                    institution=to_str(edu.get("institution")) or "",
                    degree=to_str(edu.get("degree")) or "",
                    field=to_str(edu.get("field")),
                    year=to_int(edu.get("year")) or None,
                )
            )

        for exp in cv_data.get("experience", []) or []:
            if not isinstance(exp, dict):
                continue
            experience.append(
                Experience(
                    organization=to_str(exp.get("organization")) or "",
                    role=to_str(exp.get("role")) or "",
                    description=to_str(exp.get("description")),
                    start_year=to_int(exp.get("start_year")) or None,
                    end_year=to_int(exp.get("end_year")) or None,
                )
            )

        for pub in cv_data.get("publications", []) or []:
            if not isinstance(pub, dict):
                continue
            publications.append(
                Publication(
                    title=to_str(pub.get("title")) or "",
                    authors=to_list(pub.get("authors")),
                    year=to_int(pub.get("year")),
                    venue=to_str(pub.get("venue")),
                )
            )

        raw_skills = cv_data.get("skills", [])
        if isinstance(raw_skills, list):
            skills.extend(str(s) for s in raw_skills if s)
        elif raw_skills:
            skills.extend(to_list(raw_skills))

        raw_interests = cv_data.get("research_interests", [])
        if isinstance(raw_interests, list):
            extracted_keywords.extend(str(i) for i in raw_interests if i)
        elif raw_interests:
            extracted_keywords.extend(to_list(raw_interests))

    return StudentProfile(
        session_id=uuid4(),
        stated_interests=research_interests,
        education=education,
        experience=experience,
        publications=publications,
        skills=list(set(skills)),
        extracted_keywords=list(set(extracted_keywords)),
    )
