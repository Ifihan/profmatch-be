from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14

    database_url: str = "postgresql+asyncpg://profmatch:profmatch@localhost:5432/profmatch"
    redis_url: str = "redis://localhost:6379/0"
    arq_queue_name: str = "arq:queue"  # set per deploy to isolate preview queues

    gemini_api_key: str = ""
    gemini_gen_model: str = "gemini-2.5-flash"
    gemini_embed_model: str = "gemini-embedding-001"

    openalex_mailto: str = ""
    semantic_scholar_api_key: str = ""
    crossref_mailto: str = ""

    anon_free_searches: int = 1
    registered_start_credits: int = 3
    registered_max_credits: int = 3
    credit_regen_interval_hours: int = 48


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
