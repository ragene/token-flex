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
    Verify internal HS256 JWT. If AUTH0_DOMAIN is not set, auth is disabled (pass-through).
    Returns decoded token payload or None if auth disabled.
    Raises 401 if token is invalid.
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
