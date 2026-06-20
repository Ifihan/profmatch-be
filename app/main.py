from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.config import settings
from app.core.rate_limit import limiter
from app.api.routes import auth, matches, admin, credits, promo

app = FastAPI(title="ProfMatch API", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,  # required so the anon_session_id cookie round-trips
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (auth.router, matches.router, admin.router, credits.router, promo.router):
    app.include_router(router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok"}


# Match endpoints accept anonymous (cookie) OR bearer auth — advertise bearer as optional.
_OPTIONAL_AUTH = {
    ("/api/matches", "post"),
    ("/api/matches/{job_id}", "get"),
    ("/api/matches/{job_id}/events", "get"),
}


def _custom_openapi():
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for path, method in _OPTIONAL_AUTH:
            schema["paths"][path][method]["security"] = [{}, {"HTTPBearer": []}]
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi
