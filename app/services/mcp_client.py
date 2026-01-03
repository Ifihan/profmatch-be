import json
import logging
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings

logger = logging.getLogger(__name__)


async def call_mcp_tool(server_script: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a tool on an MCP server."""
    logger.info(f"MCP call: {server_script} -> {tool_name}({arguments})")

    env = {**os.environ, "GEMINI_API_KEY": settings.gemini_api_key}
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", server_script],
        env=env,
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                if result.content and len(result.content) > 0:
                    text = result.content[0].text
                    logger.info(f"MCP result: {text[:200]}...")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"raw": text}
                return {}
    except Exception as e:
        logger.error(f"MCP call failed [{server_script}:{tool_name}]: {e}")
        return {}


class ScholarClient:
    """Client for Scholar MCP server."""

    SERVER_SCRIPT = "mcp-servers/scholar-server/server.py"

    async def search_scholar(self, name: str, affiliation: str | None = None) -> list[dict]:
        """Search for a scholar."""
        args = {"name": name}
        if affiliation:
            args["affiliation"] = affiliation
        result = await call_mcp_tool(self.SERVER_SCRIPT, "search_scholar", args)
        return result if isinstance(result, list) else []

    async def get_publications(self, scholar_id: str, limit: int = 20, years: int = 5) -> list[dict]:
        """Get scholar publications."""
        result = await call_mcp_tool(
            self.SERVER_SCRIPT,
            "get_publications",
            {"scholar_id": scholar_id, "limit": limit, "years": years},
        )
        return result if isinstance(result, list) else []

    async def get_citation_metrics(self, scholar_id: str) -> dict:
        """Get citation metrics."""
        return await call_mcp_tool(self.SERVER_SCRIPT, "get_citation_metrics", {"scholar_id": scholar_id})

    async def get_coauthors(self, scholar_id: str) -> list[str]:
        """Get coauthors."""
        result = await call_mcp_tool(self.SERVER_SCRIPT, "get_coauthors", {"scholar_id": scholar_id})
        return result if isinstance(result, list) else []


class UniversityClient:
    """Client for University MCP server."""

    SERVER_SCRIPT = "mcp-servers/university-server/server.py"

    async def get_departments(self, university_url: str) -> list[dict]:
        """Get departments from university."""
        result = await call_mcp_tool(self.SERVER_SCRIPT, "get_departments", {"university_url": university_url})
        return result if isinstance(result, list) else []

    async def get_faculty(self, department_url: str) -> list[dict]:
        """Get faculty from department."""
        result = await call_mcp_tool(self.SERVER_SCRIPT, "get_faculty", {"department_url": department_url})
        return result if isinstance(result, list) else []

    async def get_professor_page(self, professor_url: str) -> dict:
        """Get professor details."""
        return await call_mcp_tool(self.SERVER_SCRIPT, "get_professor_page", {"professor_url": professor_url})

    async def search_faculty(self, university_url: str, research_area: str) -> list[dict] | dict:
        """Search faculty by research area."""
        result = await call_mcp_tool(
            self.SERVER_SCRIPT,
            "search_faculty",
            {"university_url": university_url, "research_area": research_area},
        )
        return result if isinstance(result, list) else result


class DocumentClient:
    """Client for Document MCP server."""

    SERVER_SCRIPT = "mcp-servers/document-server/server.py"

    async def parse_cv(self, file_path: str) -> dict:
        """Parse CV file."""
        return await call_mcp_tool(self.SERVER_SCRIPT, "parse_cv", {"file_path": file_path})

    async def extract_research_interests(self, text: str) -> list[str]:
        """Extract research interests from text."""
        result = await call_mcp_tool(self.SERVER_SCRIPT, "extract_research_interests", {"text": text})
        return result if isinstance(result, list) else []

    async def extract_publications(self, text: str) -> list[dict]:
        """Extract publications from text."""
        result = await call_mcp_tool(self.SERVER_SCRIPT, "extract_publications", {"text": text})
        return result if isinstance(result, list) else []


scholar_client = ScholarClient()
university_client = UniversityClient()
document_client = DocumentClient()
