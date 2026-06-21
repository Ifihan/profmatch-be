"""Stage 1: turn raw CV text + typed interests into a structured student profile."""
from app.services.gemini import generate_json

_PROMPT = """You are parsing a student's CV to help match them with research supervisors.
Return ONLY JSON with this shape:
{{
  "education": [string],
  "research_experience": [string],
  "skills": [string],
  "stated_interests": [string],
  "key_topics": [string],
  "research_focus": string
}}

- "key_topics": the student's RESEARCH themes/methods/theories only (e.g. "few-shot
  learning", "postcolonial literature", "constitutional law"). EXCLUDE generic tools
  and skills (e.g. "data analysis", "automation", "Python", "Excel").
- "research_focus": 1-2 sentences describing the student's research direction.
Capture themes from ANY discipline equally — do not bias toward technical fields.

Typed research interests: {interests}

CV text:
{cv}
"""


async def run(cv_text: str, research_interests: str) -> dict:
    profile = await generate_json(
        _PROMPT.format(interests=research_interests, cv=cv_text[:20000])
    )
    # Embedding signal: lead with the stated interests so research direction
    # dominates, then the focus summary and research topics — not CV skill noise.
    parts = (
        [research_interests, profile.get("research_focus", "")]
        + profile.get("stated_interests", [])
        + profile.get("key_topics", [])
    )
    profile["profile_text"] = " ; ".join(p for p in parts if p)
    return profile
