"""Auth0 JWT verification for token-flow API."""
import os
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

security = HTTPBearer(auto_error=False)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_AUDIENCE = os.environ.get("AUTH0_AUDIENCE", "")
ALGORITHMS = ["RS256"]

_jwks_cache: Optional[dict] = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    if not AUTH0_DOMAIN:
        return {}
    resp = httpx.get(f"https://{AUTH0_DOMAIN}/.well-known/jwks.json", timeout=10)
    resp.raise_for_status()
    _jwks_cache = resp.json()
    return _jwks_cache


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> Optional[dict]:
    """
    Verify Auth0 JWT. If AUTH0_DOMAIN is not set, auth is disabled (pass-through).
    Returns decoded token payload or None if auth disabled.
    Raises 401 if token is invalid.
    """
    if not AUTH0_DOMAIN:
        # Auth not configured — allow all (dev mode)
        return None

    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = credentials.credentials
    try:
        jwks = _get_jwks()
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == unverified_header.get("kid"):
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                }
                break

        if not rsa_key:
            raise HTTPException(status_code=401, detail="Invalid token: key not found")

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=ALGORITHMS,
            audience=AUTH0_AUDIENCE or None,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
