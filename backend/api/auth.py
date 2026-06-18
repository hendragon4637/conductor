"""Auth API routes — token refresh with rotation."""
from fastapi import APIRouter, HTTPException

from backend.schemas.auth import RefreshRequest, TokenResponse
from backend.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    """Exchange a refresh token for a new access+refresh pair (rotation)."""
    result = auth_service.rotate_refresh_token(req.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid, expired, or already-rotated refresh token",
        )
    return result
