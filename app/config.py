from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=(".env.production", ".env.development", ".env"),
        extra="ignore",
    )

    env: str = "development"
    app_name: str = "ProfMatch"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://localhost:5432/profmatch"

    cors_origins: list[str] = [
        "http://localhost:3000",
        "https://profmatch-912048666815.us-central1.run.app",
    ]

    max_upload_size_mb: int = 10
    session_ttl_hours: int = 24

    gemini_api_key: str = ""
    serper_api_key: str = ""

    # Google Cloud Storage settings
    gcs_bucket_name: str = ""
    gcs_project_id: str = ""


settings = Settings()
