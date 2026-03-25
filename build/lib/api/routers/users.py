"""Users management router for token-flow."""
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional

from api.auth import verify_token
from api.db_helper import get_conn

router = APIRouter(tags=["users"])


def require_admin(payload):
    """Raise 403 if user is not admin. None payload (dev passthrough) allows all."""
    if payload is None:
        return  # dev mode passthrough
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin required")


def _row_to_dict(row) -> dict:
    last_login = row["last_login"]
    if last_login and hasattr(last_login, "isoformat"):
        last_login = last_login.isoformat()
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": row["is_active"],
        "auth0_sub": row["auth0_sub"],
        "last_login": last_login,
    }


class RoleBody(BaseModel):
    role: str


@router.get("/users/")
def list_users(request: Request, payload: dict = Depends(verify_token)):
    require_admin(payload)
    conn = get_conn(request)
    try:
        cur = conn.execute(
            "SELECT id, email, name, role, is_active, auth0_sub, last_login FROM tf_users ORDER BY created_at ASC"
        )
        rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/users/me")
def get_me(request: Request, payload: dict = Depends(verify_token)):
    conn = get_conn(request)
    try:
        if payload is None:
            # dev passthrough — return a fake admin
            return {"id": 0, "email": "dev@local", "name": "Dev", "role": "admin", "is_active": True, "auth0_sub": None, "last_login": None}
        user_id = int(payload["sub"])
        cur = conn.execute(
            "SELECT id, email, name, role, is_active, auth0_sub, last_login FROM tf_users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return _row_to_dict(row)
    finally:
        conn.close()


@router.patch("/users/{user_id}/role")
def update_role(user_id: int, body: RoleBody, request: Request, payload: dict = Depends(verify_token)):
    require_admin(payload)
    if body.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")
    conn = get_conn(request)
    try:
        cur = conn.execute(
            "UPDATE tf_users SET role = %s WHERE id = %s RETURNING id, email, name, role, is_active, auth0_sub, last_login",
            (body.role, user_id)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.patch("/users/{user_id}/activate")
def activate_user(user_id: int, request: Request, payload: dict = Depends(verify_token)):
    require_admin(payload)
    conn = get_conn(request)
    try:
        cur = conn.execute(
            "UPDATE tf_users SET is_active = TRUE WHERE id = %s RETURNING id, email, name, role, is_active, auth0_sub, last_login",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.patch("/users/{user_id}/deactivate")
def deactivate_user(user_id: int, request: Request, payload: dict = Depends(verify_token)):
    require_admin(payload)
    # Cannot deactivate self
    if payload is not None and str(user_id) == str(payload.get("sub")):
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    conn = get_conn(request)
    try:
        cur = conn.execute(
            "UPDATE tf_users SET is_active = FALSE WHERE id = %s RETURNING id, email, name, role, is_active, auth0_sub, last_login",
            (user_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return _row_to_dict(row)
    finally:
        conn.close()


@router.delete("/users/{user_id}")
def delete_user(user_id: int, request: Request, payload: dict = Depends(verify_token)):
    require_admin(payload)
    # Cannot delete self
    if payload is not None and str(user_id) == str(payload.get("sub")):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    conn = get_conn(request)
    try:
        # Must be inactive first
        cur = conn.execute("SELECT is_active FROM tf_users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if row["is_active"]:
            raise HTTPException(status_code=400, detail="User must be deactivated before deletion")
        conn.execute("DELETE FROM tf_users WHERE id = %s", (user_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
