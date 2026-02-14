"""
Central configuration. All API keys and settings in one place.
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment ---
    env: str = Field(default="development", alias="ENV")
    log_level: str = Field(default="info", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/teems",
        alias="DATABASE_URL",
    )

    # --- Auth0 ---
    auth0_domain: str = Field(default="", alias="AUTH0_DOMAIN")
    auth0_audience: str = Field(default="", alias="AUTH0_AUDIENCE")
    auth0_algorithm: str = Field(default="RS256", alias="AUTH0_ALGORITHM")

    # --- LLM ---
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    aiml_api_key: str = Field(default="", alias="AIML_API_KEY")
    aiml_base_url: str = Field(default="https://api.aimlapi.com/v1", alias="AIML_BASE_URL")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    default_llm_model: str = Field(default="gemini-3-flash-preview", alias="DEFAULT_LLM_MODEL")
    default_llm_temperature: float = Field(default=0.7, alias="DEFAULT_LLM_TEMPERATURE")
    default_llm_max_tokens: int = Field(default=4096, alias="DEFAULT_LLM_MAX_TOKENS")

    # --- AWS S3 ---
    aws_access_key_id: str = Field(default="", alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(default="", alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    s3_bucket_name: str = Field(default="teems-agents", alias="S3_BUCKET_NAME")

    # --- Redis ---
    redis_url: str = Field(default="", alias="REDIS_URL")

    # --- Web Search ---
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    # --- OCR ---
    aiml_ocr_model: str = Field(default="google/gc-document-ai", alias="AIML_OCR_MODEL")

    # --- Brandfetch ---
    brandfetch_api_key: str = Field(default="", alias="BRANDFETCH_API_KEY")
    brandfetch_endpoint: str = Field(
        default="https://api.brandfetch.io/v2/brands/",
        alias="BRANDFETCH_ENDPOINT",
    )

    # --- UGC Video ---
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    lipsync_api_key: str = Field(default="", alias="LIPSYNC_API_KEY")

    # --- Social Media ---
    tiktok_client_key: str = Field(default="", alias="TIKTOK_CLIENT_KEY")
    tiktok_client_secret: str = Field(default="", alias="TIKTOK_CLIENT_SECRET")
    tiktok_redirect_uri: str = Field(default="", alias="TIKTOK_REDIRECT_URI")
    facebook_app_id: str = Field(default="", alias="FACEBOOK_APP_ID")
    facebook_app_secret: str = Field(default="", alias="FACEBOOK_APP_SECRET")
    facebook_redirect_uri: str = Field(default="", alias="FACEBOOK_REDIRECT_URI")

    # --- Presentation ---
    slidespeak_api_key: str = Field(default="", alias="SLIDESPEAK_API_KEY")
    slidespeak_base_url: str = Field(
        default="https://api.slidespeak.co/api/v1",
        alias="SLIDESPEAK_BASE_URL",
    )

    # --- Notetaker (Meeting BaaS) ---
    meetingbaas_api_key: str = Field(default="", alias="MEETINGBAAS_API_KEY")
    meetingbaas_base_url: str = Field(
        default="https://api.meetingbaas.com",
        alias="MEETINGBAAS_BASE_URL",
    )
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")

    # --- API ---
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
