"""Job dispatch. Two backends: ARQ/Redis (dev/preview) and Cloud Tasks push (prod).
Selected by settings.queue_backend; the API only calls enqueue_match_job."""
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings

_pool = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue_match_job(job_id: str) -> None:
    if settings.queue_backend == "cloudtasks":
        await _enqueue_cloud_task(job_id)
    else:
        pool = await get_pool()
        await pool.enqueue_job("run_match_job", job_id, _queue_name=settings.arq_queue_name)


async def _enqueue_cloud_task(job_id: str) -> None:
    """Create a Cloud Task that POSTs to the internal run endpoint, authenticated
    with an OIDC token. Cloud Run runs the pipeline then scales back to zero."""
    from google.cloud import tasks_v2
    from google.protobuf import duration_pb2

    client = tasks_v2.CloudTasksAsyncClient()
    parent = client.queue_path(
        settings.cloud_tasks_project, settings.cloud_tasks_location, settings.cloud_tasks_queue,
    )
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{settings.service_url}/api/internal/run/{job_id}",
            "oidc_token": {
                "service_account_email": settings.cloud_tasks_invoker_sa,
                "audience": settings.service_url,
            },
        },
        "dispatch_deadline": duration_pb2.Duration(seconds=900),
    }
    await client.create_task(parent=parent, task=task)
