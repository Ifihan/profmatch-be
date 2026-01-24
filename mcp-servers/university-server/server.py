import json
import os
import re

import httpx
from google import genai
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("university-server")

gemini_client: genai.Client | None = None


def get_gemini() -> genai.Client:
    """Get Gemini client."""
    global gemini_client
    if gemini_client is None:
        gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
    return gemini_client


async def fetch_page(url: str) -> str:
    """Fetch webpage content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30, headers=headers)
        resp.raise_for_status()
        return resp.text


def extract_text_from_html(html: str, base_url: str = "") -> str:
    """extract text and links from HTML."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    def replace_link(match):
        href = match.group(1)
        content = match.group(2)
        # Clean content
        content = re.sub(r'<[^>]+>', '', content).strip()
        
        # Resolve relative URLs if base_url is provided
        if base_url and not href.startswith(('http', 'https', 'mailto:', 'tel:')):
            if href.startswith('/'):
                # Handle root-relative
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            else:
                # Handle relative
                href = f"{base_url.rstrip('/')}/{href}"
                
        return f" [{content}]({href}) "

    text = re.sub(r'<a\s+[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', replace_link, text, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text[:25000]


async def llm_extract(prompt: str, content: str) -> str:
    """Use Gemini to extract structured data."""
    client = get_gemini()
    full_prompt = f"{prompt}\n\nContent:\n{content}"
    response = client.models.generate_content(model="gemini-3-flash-preview", contents=full_prompt)
    return response.text or ""


async def find_faculty_directory(university_url: str) -> str | None:
    """Find faculty/people directory link on university page."""
    html = await fetch_page(university_url)
    text = extract_text_from_html(html, base_url=university_url)

    prompt = """Find the URL to the faculty directory, people page, or faculty listing on this university website.
The text contains '[Link Text](URL)' formatted links.
Look for links containing words like: faculty, people, directory, staff, professors, team, members.
Prefer links that look like directories (e.g. /faculty/, /people/).
Return ONLY the full absolute URL, nothing else. If not found, return "NOT_FOUND"."""

    result = await llm_extract(prompt, f"Base URL: {university_url}\n\n{text}")
    result = result.strip()
    if result == "NOT_FOUND" or not result.startswith("http"):
        return None
    return result


async def extract_faculty_list(page_url: str) -> list[dict]:
    """Extract faculty members from a page."""
    html = await fetch_page(page_url)    
    text = extract_text_from_html(html)

    prompt = """Extract all faculty/professor information from this page.
For each person, extract: name, title, department, email (if available), profile_url (if available).
Return as JSON array with objects having keys: name, title, department, email, profile_url.
Only include actual faculty members, not staff or students."""

    result = await llm_extract(prompt, f"Page URL: {page_url}\n\n{text}")
        
    try:
        result = result.replace('```json', '').replace('```', '').strip() 
        start = result.find('[')
        end = result.rfind(']') + 1
        if start != -1 and end != -1:
            json_str = result[start:end]
            return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    return []


async def extract_professor_details(professor_url: str) -> dict:
    """Extract detailed professor information from their page."""
    html = await fetch_page(professor_url)
    text = extract_text_from_html(html)

    prompt = """Extract professor information from this profile page.
Return JSON with: name, title, department, email, phone, office, research_areas (list), bio, education (list), publications_url.
Use null for missing fields."""

    result = await llm_extract(prompt, text)

    try:
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError:
        pass
    return {}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_departments",
            description="Get list of academic departments from a university",
            inputSchema={
                "type": "object",
                "properties": {
                    "university_url": {"type": "string", "description": "University homepage URL"},
                },
                "required": ["university_url"],
            },
        ),
        Tool(
            name="get_faculty",
            description="Get list of faculty members from a department or faculty page",
            inputSchema={
                "type": "object",
                "properties": {
                    "department_url": {"type": "string", "description": "Department or faculty listing URL"},
                },
                "required": ["department_url"],
            },
        ),
        Tool(
            name="get_professor_page",
            description="Get detailed professor information from their profile page",
            inputSchema={
                "type": "object",
                "properties": {
                    "professor_url": {"type": "string", "description": "Professor profile page URL"},
                },
                "required": ["professor_url"],
            },
        ),
        Tool(
            name="search_faculty",
            description="Search for faculty by research area at a university",
            inputSchema={
                "type": "object",
                "properties": {
                    "university_url": {"type": "string", "description": "University homepage URL"},
                    "research_area": {"type": "string", "description": "Research area to search for"},
                },
                "required": ["university_url", "research_area"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name == "get_departments":
        html = await fetch_page(arguments["university_url"])
        text = extract_text_from_html(html)

        prompt = """Extract all academic departments from this university page.
Return JSON array with objects having: name, url.
Focus on academic/research departments like Computer Science, Engineering, etc."""

        result = await llm_extract(prompt, f"Base URL: {arguments['university_url']}\n\n{text}")

        try:
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                return [TextContent(type="text", text=json_match.group())]
        except:
            pass
        return [TextContent(type="text", text="[]")]

    elif name == "get_faculty":
        faculty = await extract_faculty_list(arguments["department_url"])
        return [TextContent(type="text", text=json.dumps(faculty))]

    elif name == "get_professor_page":
        details = await extract_professor_details(arguments["professor_url"])
        return [TextContent(type="text", text=json.dumps(details))]

    elif name == "search_faculty":
        faculty_url = await find_faculty_directory(arguments["university_url"])
        if not faculty_url:
            return [TextContent(type="text", text=json.dumps({"error": "Could not find faculty directory"}))]

        faculty = await extract_faculty_list(faculty_url)

        research_area = arguments["research_area"].lower()
        filtered = []
        for f in faculty:
            name = (f.get("name") or "").lower()
            title = (f.get("title") or "").lower()
            dept = (f.get("department") or "").lower()
            if research_area in name or research_area in title or research_area in dept:
                filtered.append(f)

        if not filtered:
            filtered = faculty[:20]

        return [TextContent(type="text", text=json.dumps(filtered))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

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
