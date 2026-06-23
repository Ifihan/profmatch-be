"""ARQ worker (dev/preview). The pipeline itself lives in job.process_match_job so
the Cloud Tasks handler shares it. Run: arq app.workers.worker.WorkerSettings"""
from arq.connections import RedisSettings
from app.core.config import settings
from app.workers.job import process_match_job


async def run_match_job(ctx, job_id: str):
    await process_match_job(job_id)


class WorkerSettings:
    functions = [run_match_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.arq_queue_name
    max_jobs = 10
    job_timeout = 600  # the crawl + enrichment + LLM re-rank can exceed 5 min
    # Poll less often so idle polling doesn't burn managed-Redis commands; a few
    # seconds' pickup latency is nothing against a 1-3 min pipeline.
    poll_delay = 3.0
    health_check_interval = 300
