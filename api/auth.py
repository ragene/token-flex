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


_SHARED_TOKEN = os.environ.get("TOKEN_FLOW_AUTH_TOKEN", "")


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Optional[dict]:
    """
    Verify bearer token. Accepts two forms:
      1. Shared static TOKEN_FLOW_AUTH_TOKEN — used by the local service for
         push/record calls. Returns None (no user identity, treated as service account).
      2. Internal HS256 JWT (minted by /auth/exchange) — used by browser clients.
         Returns the decoded payload containing 'email', 'role', etc.

    If AUTH0_DOMAIN is not set, all requests pass through (dev mode).
    Raises 401 if the token is present but invalid.
    """
    if not AUTH0_DOMAIN:
        return None  # dev mode passthrough

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    raw = credentials.credentials

    # Accept shared static token (local service / poller) — no user context
    if _SHARED_TOKEN and raw == _SHARED_TOKEN:
        return None

    # Otherwise expect a signed internal JWT
    return decode_token(raw)


def get_current_user_email(token_payload: Optional[dict]) -> Optional[str]:
    """Extract email from a decoded token payload. Returns None in dev mode (no auth)."""
    if token_payload is None:
        return None  # dev mode — no filtering
    return token_payload.get("email")
