"""Stage 1: turn raw CV text + typed interests into a structured student profile."""
from app.services.gemini import generate_json

_PROMPT = """You are parsing a student's CV to help match them with research supervisors.
Return ONLY JSON with this shape:
{{
  "education": [string],
  "research_experience": [string],
  "skills": [string],
  "stated_interests": [string],
  "key_topics": [string]
}}

Capture themes, methods, and theories from ANY academic discipline equally —
e.g. "postcolonial literature" or "constitutional law" are as valid as
"graph neural networks". Do not bias toward technical fields.

Typed research interests: {interests}

CV text:
{cv}
"""


async def run(cv_text: str, research_interests: str) -> dict:
    profile = await generate_json(
        _PROMPT.format(interests=research_interests, cv=cv_text[:20000])
    )
    # canonical profile string used for embedding later
    parts = (
        profile.get("stated_interests", [])
        + profile.get("key_topics", [])
        + [research_interests]
    )
    profile["profile_text"] = " ; ".join(p for p in parts if p)
    return profile
