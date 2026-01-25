from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "ProfMatch"
    debug: bool = False

    redis_url: str = "redis://localhost:6379"
    database_url: str = "postgresql+asyncpg://localhost:5432/profmatch"

    cors_origins: list[str] = ["http://localhost:3000"]

    max_upload_size_mb: int = 10
    session_ttl_hours: int = 24

    gemini_api_key: str = ""
    tavily_api_key: str = ""

    # Google Cloud Storage settings
    gcs_bucket_name: str = ""
    gcs_project_id: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
