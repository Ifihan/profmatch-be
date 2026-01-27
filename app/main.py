import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.middleware import TimingMiddleware
from app.routes import match, professor, session, upload
from app.services.cleanup import start_cleanup_task, stop_cleanup_task
from app.services.database import close_db, init_db
from app.services.mcp_client import (
    DocumentClient,
    ScholarClient,
    SearchClient,
    UniversityClient,
    server_manager,
)
from app.services.redis import close_redis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting ProfMatch in {settings.env} environment")
    await init_db()

    await asyncio.gather(
        server_manager.start_server(UniversityClient.SERVER_SCRIPT),
        server_manager.start_server(ScholarClient.SERVER_SCRIPT),
        server_manager.start_server(DocumentClient.SERVER_SCRIPT),
        server_manager.start_server(SearchClient.SERVER_SCRIPT),
    )
    logging.info("All MCP servers started in parallel")

    # Start background cleanup task
    await start_cleanup_task()

    yield

    # Stop cleanup task
    await stop_cleanup_task()

    # Close MCP servers (suppress shutdown errors)
    try:
        await server_manager.close_all()
    except Exception:
        # Suppress MCP shutdown errors - known issue with stdio clients
        # Servers are properly terminated via process cleanup
        pass

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

# Add timing middleware first (executes last due to middleware stack)
app.add_middleware(TimingMiddleware)

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
