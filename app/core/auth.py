"""
Auth0 JWT validation OR dev-mode bypass. Controlled by FF_USE_AUTH0 flag.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from jose import jwt, JWTError

from .config import get_settings
from .flags import get_flags

logger = logging.getLogger(__name__)


@dataclass
class AuthenticatedUser:
    user_id: str
    email: str = ""
    name: str = ""
    tenant_id: str = ""
    roles: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


# Dev-mode user — returned when FF_USE_AUTH0=false
DEV_USER = AuthenticatedUser(
    user_id="dev-user",
    email="dev@local",
    name="Dev User",
    tenant_id="dev-tenant",
    roles=["admin"],
    permissions=["all"],
)


class Auth0Client:
    """Validates Auth0 JWT tokens. Caches JWKS keys."""

    def __init__(self):
        self._jwks: Optional[dict] = None
        self._jwks_fetched_at: float = 0
        self._jwks_ttl: int = 600  # 10 minutes

    async def _get_jwks(self, domain: str) -> dict:
        now = time.time()
        if self._jwks and (now - self._jwks_fetched_at) < self._jwks_ttl:
            return self._jwks

        url = f"https://{domain}/.well-known/jwks.json"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            resp.raise_for_status()
            self._jwks = resp.json()
            self._jwks_fetched_at = now
            return self._jwks

    async def verify_token(self, token: str) -> AuthenticatedUser:
        settings = get_settings()
        domain = settings.auth0_domain
        audience = settings.auth0_audience
        algorithm = settings.auth0_algorithm

        jwks = await self._get_jwks(domain)
        unverified_header = jwt.get_unverified_header(token)

        rsa_key = {}
        for key in jwks.get("keys", []):
            if key["kid"] == unverified_header.get("kid"):
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                }
                break

        if not rsa_key:
            raise JWTError("Unable to find matching key in JWKS")

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=[algorithm],
            audience=audience,
            issuer=f"https://{domain}/",
        )

        return AuthenticatedUser(
            user_id=payload.get("sub", ""),
            email=payload.get("email", payload.get("https://teems.ai/email", "")),
            name=payload.get("name", payload.get("https://teems.ai/name", "")),
            tenant_id=payload.get("https://teems.ai/tenant_id", ""),
            roles=payload.get("https://teems.ai/roles", []),
            permissions=payload.get("permissions", []),
        )


# Singleton
_auth0_client = Auth0Client()


async def get_current_user(authorization: str = "") -> AuthenticatedUser:
    """
    Resolve the current user from the Authorization header.
    If FF_USE_AUTH0 is false, returns a dev user.
    """
    flags = get_flags()

    if not flags.use_auth0:
        return DEV_USER

    if not authorization:
        raise PermissionError("Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise PermissionError("Invalid Authorization header. Use: Bearer <token>")

    try:
        user = await _auth0_client.verify_token(token)
    except JWTError as e:
        raise PermissionError(f"Invalid token: {e}")

    if not user.tenant_id:
        raise PermissionError("Token missing tenant_id claim")

    return user
