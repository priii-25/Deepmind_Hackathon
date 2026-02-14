"""
Async SQLAlchemy engine and session management. Single connection pool for everything.
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base. All models inherit from this."""
    pass


# Lazy globals — initialized on first call to get_engine()
_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url

        # Ensure we're using asyncpg driver for PostgreSQL
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

        # SQLite doesn't support pool_size / max_overflow
        is_sqlite = "sqlite" in url
        kwargs = {
            "echo": settings.debug,
        }
        if not is_sqlite:
            kwargs["pool_size"] = 20
            kwargs["max_overflow"] = 10
            kwargs["pool_pre_ping"] = True

        _engine = create_async_engine(url, **kwargs)
        logger.info("Database engine created (%s)", "sqlite" if is_sqlite else "postgresql")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields a DB session per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables. Called on startup."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Import all models so they register with Base.metadata
        from ..models import (  # noqa: F401
            user,
            conversation,
            document,
            agent,
            onboarding,
            agent_session,
            meeting,
            ugc,
            fashion,
            social_media,
            presentation,
        )
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")


async def close_db():
    """Dispose engine. Called on shutdown."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine disposed")
