"""
GET /health — liveness check.
"""
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    return {
        "status": "ok",
        "db": request.app.state.db_path,
        "version": "0.1.0",
    }
