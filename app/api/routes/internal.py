"""Internal endpoint hit by Cloud Tasks to run the pipeline. Secured by verifying
the task's Google-signed OIDC token (expected audience + invoker service account)."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.core.config import settings
from app.workers.job import process_match_job

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


async def _verify_oidc(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, auth[7:], google_requests.Request(), settings.service_url,
        )
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    if settings.cloud_tasks_invoker_sa and claims.get("email") != settings.cloud_tasks_invoker_sa:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unexpected identity")


@router.post("/run/{job_id}")
async def run(job_id: str, _: None = Depends(_verify_oidc)):
    await process_match_job(job_id)
    return {"ok": True}
