import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import BaseModel

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"

server = Server("scholar-server")


class ScholarProfile(BaseModel):
    """Scholar profile data."""
    author_id: str
    name: str
    affiliations: list[str] = []
    paper_count: int = 0
    citation_count: int = 0
    h_index: int = 0


class Publication(BaseModel):
    """Publication data."""
    paper_id: str
    title: str
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    citation_count: int = 0
    url: str | None = None


async def search_author(name: str, affiliation: str | None = None) -> list[dict]:
    """Search for author on Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        query = f"{name} {affiliation}" if affiliation else name
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/search",
            params={"query": query, "limit": 5, "fields": "name,affiliations,paperCount,citationCount,hIndex"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


async def get_author_details(author_id: str) -> dict | None:
    """Get author details from Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/{author_id}",
            params={"fields": "name,affiliations,paperCount,citationCount,hIndex"},
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


async def get_author_papers(author_id: str, limit: int = 20, years: int = 5) -> list[dict]:
    """Get author's papers from Semantic Scholar."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_API}/author/{author_id}/papers",
            params={
                "fields": "title,authors,year,venue,abstract,citationCount,url",
                "limit": limit,
            },
            timeout=30,
        )
        resp.raise_for_status()
        papers = resp.json().get("data", [])

        if years:
            from datetime import datetime
            current_year = datetime.now().year
            papers = [p for p in papers if p.get("year") and p["year"] >= current_year - years]

        return papers


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search_scholar",
            description="Search for a scholar by name and optional affiliation",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Scholar name"},
                    "affiliation": {"type": "string", "description": "University or institution"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="get_publications",
            description="Get publications for a scholar",
            inputSchema={
                "type": "object",
                "properties": {
                    "scholar_id": {"type": "string", "description": "Semantic Scholar author ID"},
                    "limit": {"type": "integer", "description": "Max publications to return", "default": 20},
                    "years": {"type": "integer", "description": "Only papers from last N years", "default": 5},
                },
                "required": ["scholar_id"],
            },
        ),
        Tool(
            name="get_citation_metrics",
            description="Get citation metrics for a scholar",
            inputSchema={
                "type": "object",
                "properties": {
                    "scholar_id": {"type": "string", "description": "Semantic Scholar author ID"},
                },
                "required": ["scholar_id"],
            },
        ),
        Tool(
            name="get_coauthors",
            description="Get frequent coauthors for a scholar",
            inputSchema={
                "type": "object",
                "properties": {
                    "scholar_id": {"type": "string", "description": "Semantic Scholar author ID"},
                },
                "required": ["scholar_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    import json

    if name == "search_scholar":
        results = await search_author(arguments["name"], arguments.get("affiliation"))
        profiles = []
        for r in results:
            profiles.append({
                "author_id": r.get("authorId"),
                "name": r.get("name"),
                "affiliations": r.get("affiliations", []),
            })
        return [TextContent(type="text", text=json.dumps(profiles))]

    elif name == "get_publications":
        papers = await get_author_papers(
            arguments["scholar_id"],
            arguments.get("limit", 20),
            arguments.get("years", 5),
        )
        publications = []
        for p in papers:
            publications.append({
                "paper_id": p.get("paperId"),
                "title": p.get("title"),
                "authors": [a.get("name") for a in p.get("authors", [])],
                "year": p.get("year"),
                "venue": p.get("venue"),
                "abstract": p.get("abstract"),
                "citation_count": p.get("citationCount", 0),
                "url": p.get("url"),
            })
        return [TextContent(type="text", text=json.dumps(publications))]

    elif name == "get_citation_metrics":
        details = await get_author_details(arguments["scholar_id"])
        if not details:
            return [TextContent(type="text", text=json.dumps({"error": "Author not found"}))]
        metrics = {
            "h_index": details.get("hIndex", 0),
            "total_citations": details.get("citationCount", 0),
            "paper_count": details.get("paperCount", 0),
        }
        return [TextContent(type="text", text=json.dumps(metrics))]

    elif name == "get_coauthors":
        papers = await get_author_papers(arguments["scholar_id"], limit=50, years=10)
        coauthor_counts: dict[str, int] = {}
        for p in papers:
            for author in p.get("authors", []):
                author_name = author.get("name")
                if author_name:
                    coauthor_counts[author_name] = coauthor_counts.get(author_name, 0) + 1

        sorted_coauthors = sorted(coauthor_counts.items(), key=lambda x: x[1], reverse=True)
        top_coauthors = [name for name, _ in sorted_coauthors[1:11]]  # Skip self, get top 10
        return [TextContent(type="text", text=json.dumps(top_coauthors))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
