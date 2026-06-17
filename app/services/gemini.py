"""Thin async wrapper around the Gemini SDK for text, JSON, and embeddings.

Centralised so the rest of the code never imports the SDK directly.
"""
import json
from app.core.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _extract_json(text: str) -> str:
    """Strip markdown fences and trim to the outermost JSON bounds — models
    occasionally wrap or append stray characters even in JSON mode."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip() if "```" in text[3:] else text[3:]
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=0)
    end = max(text.rfind("}"), text.rfind("]"))
    return text[start:end + 1] if end >= start else text


async def generate_json(prompt: str) -> dict | list:
    """Call Gemini and parse a JSON response. The prompt MUST instruct the model
    to return only JSON. Retries once if the response isn't valid JSON."""
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