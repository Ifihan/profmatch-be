import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.models import HealthResponse
from app.middleware import TimingMiddleware
from app.routes import auth, match, professor, session, upload
from app.services.cleanup import start_cleanup_task, stop_cleanup_task
from app.services.database import close_db, init_db
from app.services.openalex import close_client as close_openalex

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(json.dumps({"event": "app_startup", "env": settings.env}))
    await init_db()
    await start_cleanup_task()

    yield

    await stop_cleanup_task()
    await close_openalex()
    await close_db()


app = FastAPI(
    title=settings.app_name,
    description="Professor-Student Research Matching API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
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
    logger.error(json.dumps({
        "event": "unhandled_exception",
        "method": request.method,
        "path": request.url.path,
        "error": {"type": type(exc).__name__, "message": str(exc)},
    }))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to API docs."""
    return RedirectResponse(url="/docs")


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="healthy")
