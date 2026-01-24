import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.routes import match, professor, session, upload
from app.services.database import close_db, init_db
from app.services.mcp_client import DocumentClient, ScholarClient, UniversityClient, server_manager
from app.services.redis import close_redis

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database (non-blocking on failure)
    try:
        await init_db()
    except Exception as e:
        logging.error(f"Database init failed: {e}")

    # Start MCP servers in background task to not block startup
    async def start_mcp_servers():
        for script in [UniversityClient.SERVER_SCRIPT, ScholarClient.SERVER_SCRIPT, DocumentClient.SERVER_SCRIPT]:
            try:
                await server_manager.start_server(script)
                logging.info(f"Started MCP server: {script}")
            except Exception as e:
                logging.error(f"Failed to start MCP server {script}: {e}")

    asyncio.create_task(start_mcp_servers())

    yield

    await server_manager.close_all()
    await close_redis()
    await close_db()

app = FastAPI(
    title=settings.app_name,
    description="Professor-Student Research Matching API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(session.router)
app.include_router(upload.router)
app.include_router(match.router)
app.include_router(professor.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API docs."""
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
