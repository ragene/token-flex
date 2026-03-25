"""Auth for token-flow: internal HS256 JWTs + Auth0 exchange endpoint."""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

security = HTTPBearer(auto_error=False)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 8


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate an internal HS256 JWT. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Optional[dict]:
    """
    Verify bearer token — accepts only internal HS256 JWTs minted by /auth/exchange.
    Returns the decoded payload containing 'email', 'role', etc.

    If AUTH0_DOMAIN is not set, all requests pass through (dev mode).
    Raises 401 if the token is missing or invalid.
    """
    if not AUTH0_DOMAIN:
        return None  # dev mode passthrough

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    return decode_token(credentials.credentials)


def get_current_user_email(token_payload: Optional[dict]) -> Optional[str]:
    """Extract email from a decoded token payload. Returns None in dev mode (no auth)."""
    if token_payload is None:
        return None  # dev mode — no filtering
    return token_payload.get("email")


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory: raises 403 if the authenticated user's role
    is not in allowed_roles. None payload (dev mode / no auth) always passes.

    Usage:
        @router.post("/foo", dependencies=[Depends(require_role("admin", "owner"))])
        @router.post("/bar", dependencies=[Depends(require_role("admin"))])
    """
    def _check(credentials: HTTPAuthorizationCredentials = Security(security)) -> None:
        if not AUTH0_DOMAIN:
            return  # dev mode passthrough
        if not credentials:
            raise HTTPException(status_code=401, detail="Authorization required")
        try:
            payload = decode_token(credentials.credentials)
        except HTTPException:
            raise
        role = payload.get("role", "")
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{role}' is not permitted. Required: {list(allowed_roles)}"
            )
    return _check
