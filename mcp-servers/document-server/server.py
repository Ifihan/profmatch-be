import json
import os
import re
from pathlib import Path

from google import genai
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("document-server")

gemini_client: genai.Client | None = None


def get_gemini() -> genai.Client:
    """Get Gemini client."""
    global gemini_client
    if gemini_client is None:
        gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return gemini_client


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from PDF file."""
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from DOCX file."""
    from docx import Document
    doc = Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])


def extract_text_from_txt(file_path: str) -> str:
    """Extract text from TXT file."""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text(file_path: str) -> str:
    """Extract text from file based on extension."""
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".txt":
        return extract_text_from_txt(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


async def llm_extract(prompt: str, content: str) -> str:
    """Use Gemini to extract structured data."""
    client = get_gemini()
    full_prompt = f"{prompt}\n\nDocument Content:\n{content[:12000]}"
    response = client.models.generate_content(model="gemini-3-flash-preview", contents=full_prompt)
    return response.text or ""


async def parse_cv(file_path: str) -> dict:
    """Parse CV and extract structured data."""
    text = extract_text(file_path)

    prompt = """Parse this CV/resume and extract structured information.
Return JSON with:
- name: string
- email: string or null
- phone: string or null
- education: array of {institution, degree, field, year}
- experience: array of {organization, role, description, start_year, end_year}
- publications: array of {title, authors, year, venue}
- skills: array of strings
- research_interests: array of strings (inferred from content)

Be thorough in extracting all education, experience, and publications."""

    result = await llm_extract(prompt, text)

    try:
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass
    return {}


async def extract_research_interests(text: str) -> list[str]:
    """Extract research interests from text."""
    prompt = """Analyze this text and extract research interests, topics, and keywords.
Return JSON array of strings representing research areas and topics.
Include both broad areas (e.g., "machine learning") and specific topics (e.g., "transformer architectures").
Return 5-15 relevant keywords/phrases."""

    result = await llm_extract(prompt, text)

    try:
        json_match = re.search(r'\[.*\]', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass
    return []


async def extract_publications(text: str) -> list[dict]:
    """Extract publications from text."""
    prompt = """Extract all academic publications mentioned in this text.
Return JSON array with objects having:
- title: string
- authors: array of strings
- year: integer or null
- venue: string or null (journal/conference name)
- citation_count: integer or null

Only include actual publications, not references to other works."""

    result = await llm_extract(prompt, text)

    try:
        json_match = re.search(r'\[.*\]', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass
    return []


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="parse_cv",
            description="Parse a CV/resume and extract structured data",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to CV file (PDF, DOCX, or TXT)"},
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="extract_research_interests",
            description="Extract research interests and keywords from text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to analyze"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="extract_publications",
            description="Extract publications from text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text containing publications"},
                },
                "required": ["text"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name == "parse_cv":
        file_path = arguments["file_path"]
        if not Path(file_path).exists():
            return [TextContent(type="text", text=json.dumps({"error": "File not found"}))]

        result = await parse_cv(file_path)
        return [TextContent(type="text", text=json.dumps(result))]

    elif name == "extract_research_interests":
        interests = await extract_research_interests(arguments["text"])
        return [TextContent(type="text", text=json.dumps(interests))]

    elif name == "extract_publications":
        publications = await extract_publications(arguments["text"])
        return [TextContent(type="text", text=json.dumps(publications))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    def _suppress_genai_errors(loop, context):
        if "BaseApiClient" in str(context.get("exception", "")):
            return
        loop.default_exception_handler(context)

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_suppress_genai_errors)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
