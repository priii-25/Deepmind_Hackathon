"""
FastAPI dependencies. Injected into route handlers.
"""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AuthenticatedUser, get_current_user
from .database import get_db as _get_db
from .storage import StorageBackend, get_storage as _get_storage


async def get_db() -> AsyncSession:
    """Yields an async DB session per request."""
    async for session in _get_db():
        yield session


async def get_user(
    authorization: str = Header(default=""),
) -> AuthenticatedUser:
    """
    Resolve authenticated user from Authorization header.
    Returns dev user if FF_USE_AUTH0=false.
    """
    try:
        return await get_current_user(authorization)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_tenant(
    user: AuthenticatedUser = Depends(get_user),
) -> AuthenticatedUser:
    """Same as get_user, but enforces tenant_id is present."""
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenant_id associated with this user",
        )
    return user


def get_storage_dep() -> StorageBackend:
    """Returns the active storage backend (S3 or local)."""
    return _get_storage()
