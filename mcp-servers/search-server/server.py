import os
import sys
import json
import logging
from typing import Any
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from tavily import TavilyClient

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("search-server")

server = Server("search-server")

tavily_api_key = os.environ.get("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=tavily_api_key) if tavily_api_key else None

if not tavily_client:
    logger.warning("TAVILY_API_KEY not found. Search tools will fail.")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="search_web",
            description="Perform a high-quality web search to find relevant URLs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query to find information or URLs"},
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    if name == "search_web":
        if not tavily_client:
            return [TextContent(type="text", text=json.dumps({"error": "TAVILY_API_KEY not configured"}))]
            
        print(f"[DEBUG] Searching web (Tavily) for: {arguments['query']}", file=sys.stderr)
        
        try:
            # max_results=5 to get a good spread of directories/PDFs
            response = tavily_client.search(arguments["query"], search_depth="basic", max_results=5)
            print(f"[DEBUG] Tavily Results: {response}", file=sys.stderr)
            
            results = response.get('results', [])
            urls = []
            if results:
                # Collect up to 5 valid URLs
                for res in results:
                    u = res.get('url', '')
                    if u and u not in urls:
                        urls.append(u)
                        
            if urls:
                print(f"[DEBUG] Found URLs: {urls}", file=sys.stderr)
                return [TextContent(type="text", text=json.dumps(urls))]
            
            print("[DEBUG] No valid URL found.", file=sys.stderr)
            return [TextContent(type="text", text=json.dumps([]))]
            
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
