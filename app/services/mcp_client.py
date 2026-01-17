import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings
from app.utils.validators import ensure_protocol

logger = logging.getLogger(__name__)


class MCPConnection:
    """Holds connection state for an MCP server."""
    def __init__(self, session: ClientSession):
        self.session = session


class MCPServerManager:
    """Manages persistent MCP server connections."""
    def __init__(self):
        self.stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}

    async def start_server(self, script_path: str):
        """Start an MCP server and keep the connection open."""
        log_name = os.path.basename(script_path)
        logger.info(f"Starting MCP server: {log_name}")
        
        env = {
            **os.environ, 
            "GEMINI_API_KEY": settings.gemini_api_key,
            "PYTHONUNBUFFERED": "1"
        }
        
        server_params = StdioServerParameters(
            command="uv",
            args=["run", "python", script_path],
            env=env,
        )

        try:
            # Enter contexts and keep them alive via the ExitStack
            read, write = await self.stack.enter_async_context(stdio_client(server_params))
            session = await self.stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            
            self.sessions[script_path] = session
            logger.info(f"MCP server started: {log_name}")
        except Exception as e:
            logger.error(f"Failed to start MCP server {log_name}: {e}")
            raise

    async def call_tool(self, script_path: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Call a tool on a persistent MCP server session."""
        session = self.sessions.get(script_path)
        if not session:
            logger.error(f"MCP session not found for {script_path}. Call start_server first.")
            return {}

        logger.info(f"MCP (Persistent) call: {os.path.basename(script_path)} -> {tool_name}")
        
        try:
            result = await session.call_tool(tool_name, arguments)

            if result.content and len(result.content) > 0:
                text = result.content[0].text
                # logger.debug(f"MCP result: {text[:200]}...") # reduce noise
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"raw": text}
            return {}
        except Exception as e:
            logger.error(f"MCP call failed [{tool_name}]: {e}")
            return {}

    async def close_all(self):
        """Close all MCP server connections."""
        logger.info("Closing all MCP servers...")
        await self.stack.aclose()
        self.sessions.clear()


# Global manager instance
server_manager = MCPServerManager()


class ScholarClient:
    """Client for Scholar MCP server."""
    SERVER_SCRIPT = "mcp-servers/scholar-server/server.py"

    async def search_scholar(self, name: str, affiliation: str | None = None) -> list[dict]:
        """Search for a scholar."""
        args = {"name": name}
        if affiliation:
            args["affiliation"] = affiliation
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "search_scholar", args)
        return result if isinstance(result, list) else []

    async def get_publications(self, scholar_id: str, limit: int = 20, years: int = 5) -> list[dict]:
        """Get scholar publications."""
        result = await server_manager.call_tool(
            self.SERVER_SCRIPT,
            "get_publications",
            {"scholar_id": scholar_id, "limit": limit, "years": years},
        )
        return result if isinstance(result, list) else []

    async def get_citation_metrics(self, scholar_id: str) -> dict:
        """Get citation metrics."""
        return await server_manager.call_tool(self.SERVER_SCRIPT, "get_citation_metrics", {"scholar_id": scholar_id})

    async def get_coauthors(self, scholar_id: str) -> list[str]:
        """Get coauthors."""
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "get_coauthors", {"scholar_id": scholar_id})
        return result if isinstance(result, list) else []


class UniversityClient:
    """Client for University MCP server."""
    SERVER_SCRIPT = "mcp-servers/university-server/server.py"

    def _ensure_protocol(self, url: str) -> str:
        """Ensure URL has a protocol."""
        return ensure_protocol(url)

    async def get_departments(self, university_url: str) -> list[dict]:
        """Get departments from university."""
        normalized_url = self._ensure_protocol(university_url)
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "get_departments", {"university_url": normalized_url})
        return result if isinstance(result, list) else []

    async def get_faculty(self, department_url: str) -> list[dict]:
        """Get faculty from department."""
        normalized_url = self._ensure_protocol(department_url)
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "get_faculty", {"department_url": normalized_url})
        return result if isinstance(result, list) else []

    async def get_professor_page(self, professor_url: str) -> dict:
        """Get professor details."""
        normalized_url = self._ensure_protocol(professor_url)
        return await server_manager.call_tool(self.SERVER_SCRIPT, "get_professor_page", {"professor_url": normalized_url})

    async def search_faculty(self, university_url: str, research_area: str) -> list[dict] | dict:
        """Search faculty by research area."""
        normalized_url = self._ensure_protocol(university_url)
        result = await server_manager.call_tool(
            self.SERVER_SCRIPT,
            "search_faculty",
            {"university_url": normalized_url, "research_area": research_area},
        )
        return result if isinstance(result, list) else result

    async def search_web(self, query: str) -> list[str]:
        """Search the web for a URL."""
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "search_web", {"query": query})
        return result if isinstance(result, list) else []


class DocumentClient:
    """Client for Document MCP server."""
    SERVER_SCRIPT = "mcp-servers/document-server/server.py"

    async def parse_cv(self, file_path: str) -> dict:
        """Parse CV file."""
        return await server_manager.call_tool(self.SERVER_SCRIPT, "parse_cv", {"file_path": file_path})

    async def extract_research_interests(self, text: str) -> list[str]:
        """Extract research interests from text."""
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "extract_research_interests", {"text": text})
        return result if isinstance(result, list) else []

    async def extract_publications(self, text: str) -> list[dict]:
        """Extract publications from text."""
        result = await server_manager.call_tool(self.SERVER_SCRIPT, "extract_publications", {"text": text})
        return result if isinstance(result, list) else []


scholar_client = ScholarClient()
university_client = UniversityClient()
document_client = DocumentClient()
