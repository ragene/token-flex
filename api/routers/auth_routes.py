"""Auth0 exchange and config endpoints for token-flow."""
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.auth import create_access_token
from api.db_helper import get_conn

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")


@router.get("/auth/config")
def get_auth_config():
    return {
        "domain": os.environ.get("AUTH0_DOMAIN", ""),
        "clientId": os.environ.get("AUTH0_CLIENT_ID", ""),
        "audience": os.environ.get("AUTH0_AUDIENCE", ""),
        "configured": bool(os.environ.get("AUTH0_DOMAIN") and os.environ.get("AUTH0_CLIENT_ID")),
    }


@router.post("/auth/exchange")
def exchange_auth0_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
):
    domain = os.environ.get("AUTH0_DOMAIN", "")
    if not domain:
        raise HTTPException(status_code=503, detail="Auth0 not configured")
    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")

    # Call Auth0 userinfo to validate the token and get user info
    try:
        resp = httpx.get(
            f"https://{domain}/userinfo",
            headers={"Authorization": f"Bearer {credentials.credentials}"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Auth0 userinfo failed: {resp.text}")
        userinfo = resp.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach Auth0: {str(e)}")

    email = userinfo.get("email", "")
    name = userinfo.get("name") or userinfo.get("nickname") or email
    auth0_sub = userinfo.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Auth0 userinfo")

    # Get DB connection via the standard app pattern
    conn = get_conn(request)
    try:
        # Check if user exists
        cur = conn.execute(
            "SELECT id, role, is_active FROM tf_users WHERE email = %s",
            (email,)
        )
        row = cur.fetchone()

        if row is None:
            # Check if this is the very first user — bootstrap as admin if so
            count_cur = conn.execute("SELECT COUNT(*) FROM tf_users")
            count_row = count_cur.fetchone()
            total_users = count_row[0] if count_row else 0

            if total_users == 0:
                # First user ever — make them admin and activate immediately
                cur = conn.execute(
                    """INSERT INTO tf_users (email, name, auth0_sub, role, is_active, last_login)
                       VALUES (%s, %s, %s, 'admin', TRUE, NOW())
                       RETURNING id, role, is_active""",
                    (email, name, auth0_sub)
                )
            else:
                # Normal new SSO user — pending approval
                cur = conn.execute(
                    """INSERT INTO tf_users (email, name, auth0_sub, role, is_active, last_login)
                       VALUES (%s, %s, %s, 'viewer', FALSE, NOW())
                       RETURNING id, role, is_active""",
                    (email, name, auth0_sub)
                )
            row = cur.fetchone()
        else:
            conn.execute(
                "UPDATE tf_users SET auth0_sub=%s, last_login=NOW(), name=%s WHERE email=%s",
                (auth0_sub, name, email)
            )
        conn.commit()

        user_id = row[0]
        role = row[1]
        is_active = row[2]
    finally:
        conn.close()

    if not is_active:
        raise HTTPException(status_code=403, detail="Your account is pending admin approval.")

    internal = create_access_token({
        "sub": str(user_id),
        "email": email,
        "role": role,
        "name": name,
    })
    return {"access_token": internal, "token_type": "bearer"}
