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

    # JWT settings
    jwt_secret_key: str = "change-me-in-production"
    jwt_expiry_hours: int = 168  # 7 days
    jwt_algorithm: str = "HS256"

    # Frontend URL (for password reset links)
    frontend_url: str = "http://localhost:3000"

    # Google Cloud Storage settings
    gcs_bucket_name: str = ""
    gcs_project_id: str = ""


settings = Settings()
