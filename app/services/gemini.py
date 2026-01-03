from google import genai

from app.config import settings

client: genai.Client | None = None


def get_gemini_client() -> genai.Client:
    """Get Gemini client instance."""
    global client
    if client is None:
        client = genai.Client(api_key=settings.gemini_api_key)
    return client


async def generate_text(prompt: str, model: str = "gemini-2.5-flash") -> str:
    """Generate text using Gemini."""
    gemini = get_gemini_client()
    response = gemini.models.generate_content(model=model, contents=prompt)
    return response.text or ""


async def extract_structured_data(prompt: str, content: str, model: str = "gemini-2.5-flash") -> str:
    """Extract structured data from content using Gemini."""
    full_prompt = f"{prompt}\n\nContent:\n{content}"
    return await generate_text(full_prompt, model)
