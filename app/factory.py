"""
FastAPI application factory.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from .core.config import get_settings
from .core.database import init_db, close_db
from .core.redis import close_redis
from .api.router import router

logger = logging.getLogger(__name__)

# Paths for static files & templates
BASE_DIR = Path(__file__).resolve().parent.parent          # services/eve/
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Eve",
        description="AI Agent Orchestrator",
        version="2.0.0",
        docs_url="/docs" if settings.env == "development" else None,
        redoc_url="/redoc" if settings.env == "development" else None,
    )

    # ── CORS ─────────────────────────────────────────────────────
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins != ["*"] else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Static files ─────────────────────────────────────────────
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ── Startup ──────────────────────────────────────────────────
    @app.on_event("startup")
    async def on_startup():
        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper(), logging.INFO),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        logger.info("Starting Eve (env=%s)", settings.env)

        # Create database tables
        await init_db()

        # Initialize tools
        from .tools.registry import init_tools
        init_tools()

        # Initialize agent registry
        from .orchestrator.registry import get_registry
        get_registry()

        # Log feature flag state
        from .core.flags import get_flags
        flags = get_flags()
        logger.info(
            "Flags: auth0=%s s3=%s redis=%s web_search=%s ocr=%s llm=%s",
            flags.use_auth0, flags.use_s3, flags.use_redis,
            flags.use_web_search, flags.use_ocr, flags.llm_provider,
        )
        logger.info(
            "Agents: ugc=%s fashion=%s social=%s presentation=%s notetaker=%s",
            flags.enable_ugc_video, flags.enable_fashion_photo,
            flags.enable_social_media, flags.enable_presentation,
            flags.enable_notetaker,
        )

        logger.info("Eve is ready")

    # ── Shutdown ─────────────────────────────────────────────────
    @app.on_event("shutdown")
    async def on_shutdown():
        from .services.llm import close_client
        await close_client()
        await close_db()
        await close_redis()
        logger.info("Eve shut down")

    # ── Chat UI ──────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def chat_ui():
        """Serve the chat UI."""
        html_path = TEMPLATES_DIR / "chat.html"
        if html_path.exists():
            import time
            content = html_path.read_text().replace("{{ cache_bust }}", str(int(time.time())))
            return HTMLResponse(content=content)
        return HTMLResponse(content="<h1>Eve is running.</h1><p>Chat UI not found.</p>")

    # ── Routes ───────────────────────────────────────────────────
    app.include_router(router)

    return app
