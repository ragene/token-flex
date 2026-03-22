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

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
