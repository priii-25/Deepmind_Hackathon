"""
Central feature flags. One file controls every external dependency.

Set via environment variables (prefix FF_) or .env file.
When a flag is OFF, the system uses a local/mock fallback. Nothing crashes.
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FeatureFlags(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Auth ─────────────────────────────────────────────────────────
    use_auth0: bool = Field(default=True, alias="FF_USE_AUTH0")
    # ON  → JWT validated via Auth0 JWKS. Needs AUTH0_DOMAIN, AUTH0_AUDIENCE.
    # OFF → Dev user injected (tenant_id="dev-tenant"). No token needed.

    # ── Storage ──────────────────────────────────────────────────────
    use_s3: bool = Field(default=True, alias="FF_USE_S3")
    # ON  → Files go to AWS S3. Needs AWS creds + S3_BUCKET_NAME.
    # OFF → Files saved to ./local_storage/{tenant_id}/. Returns local paths.

    # ── Cache / Realtime ─────────────────────────────────────────────
    use_redis: bool = Field(default=True, alias="FF_USE_REDIS")
    # ON  → Redis pub/sub for realtime frontend notifications. Needs REDIS_URL.
    # OFF → Notifications silently skipped. Nothing breaks.

    # ── Web Search ───────────────────────────────────────────────────
    use_web_search: bool = Field(default=True, alias="FF_USE_WEB_SEARCH")
    # ON  → LLM can call Tavily search. Needs TAVILY_API_KEY.
    # OFF → Search tool not registered. LLM uses its own knowledge.

    # ── OCR ──────────────────────────────────────────────────────────
    use_ocr: bool = Field(default=True, alias="FF_USE_OCR")
    # ON  → Scanned PDFs processed via AIML OCR. Needs AIML_API_KEY.
    # OFF → Only pdfplumber. Scanned PDFs → empty text.

    # ── Brandfetch ───────────────────────────────────────────────────
    use_brandfetch: bool = Field(default=True, alias="FF_USE_BRANDFETCH")
    # ON  → Onboarding fetches brand data. Needs BRANDFETCH_API_KEY.
    # OFF → Brand fetch skipped. User enters info manually.

    # ── LLM Provider ─────────────────────────────────────────────────
    llm_provider: str = Field(default="gemini", alias="FF_LLM_PROVIDER")
    # "gemini" → Google Gemini (default). Needs GEMINI_API_KEY.
    # "aiml"   → AIML API proxy. Needs AIML_API_KEY.
    # "openai" → Direct OpenAI. Needs OPENAI_API_KEY.

    # ── Document Search ──────────────────────────────────────────────
    use_full_text_search: bool = Field(default=True, alias="FF_USE_FULL_TEXT_SEARCH")
    # ON  → tsvector + pg_trgm (needs pg_trgm extension).
    # OFF → Basic ILIKE queries. Slower but no extensions.

    # ── Agents ───────────────────────────────────────────────────────
    enable_ugc_video: bool = Field(default=True, alias="FF_ENABLE_UGC_VIDEO")
    # Requires: GEMINI_API_KEY (Veo 3.1 for video generation)

    enable_fashion_photo: bool = Field(default=True, alias="FF_ENABLE_FASHION_PHOTO")
    # Requires: AIML_API_KEY

    enable_social_media: bool = Field(default=True, alias="FF_ENABLE_SOCIAL_MEDIA")
    # YouTube video upload + publishing.
    # Requires: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, YOUTUBE_REDIRECT_URI

    enable_presentation: bool = Field(default=True, alias="FF_ENABLE_PRESENTATION")
    # Requires: GEMINI_API_KEY (uses Gemini 2.5 Flash Image / nanobanana)

    enable_notetaker: bool = Field(default=True, alias="FF_ENABLE_NOTETAKER")
    # Requires: MEETINGBAAS_API_KEY

    # ── Sub-features (within agents) ─────────────────────────────────
    use_elevenlabs: bool = Field(default=True, alias="FF_USE_ELEVENLABS")
    # OFF → UGC skips audio generation.

    use_lipsync: bool = Field(default=True, alias="FF_USE_LIPSYNC")
    # OFF → UGC returns video without lip-sync.


@lru_cache
def get_flags() -> FeatureFlags:
    return FeatureFlags()
