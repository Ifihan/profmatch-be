"""Thin async wrapper around the Gemini SDK so nothing else imports it directly."""
import json

from google import genai
from google.genai import types
from app.core.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        # Bound each call so a hung request can't stall the whole job (ms).
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=120_000),
        )
    return _client


def _extract_json(text: str) -> str:
    """Strip markdown fences and trim to the outermost JSON bounds."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip() if "```" in text[3:] else text[3:]
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=0)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start:end + 1] if end >= start else text


async def generate_json(prompt: str) -> dict | list:
    """Call Gemini and parse a JSON response (prompt must ask for JSON only); retries once."""
    client = _get_client()
    error: json.JSONDecodeError | None = None
    for _ in range(2):
        resp = await client.aio.models.generate_content(
            model=settings.gemini_gen_model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        try:
            return json.loads(_extract_json(resp.text or ""))
        except json.JSONDecodeError as e:
            error = e
    raise error


async def embed(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    resp = await client.aio.models.embed_content(
        model=settings.gemini_embed_model,
        contents=texts,
    )
    return [e.values for e in resp.embeddings]