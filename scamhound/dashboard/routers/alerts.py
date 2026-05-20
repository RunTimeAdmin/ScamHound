"""
Admin alert approval endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database


def create_alerts_router(get_current_user_fn) -> APIRouter:
    """Create alerts admin router."""
    router = APIRouter()

    @router.get("/api/alerts/pending")
    async def list_pending_alerts(request: Request, limit: int = 100):
        """List high-risk alerts that require admin tweet approval."""
        user = get_current_user_fn(request)
        if not user or not user.get("is_admin"):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        capped_limit = min(max(limit, 1), 500)
        pending = database.get_pending_tweet_approvals(limit=capped_limit)
        return JSONResponse(
            content={"success": True, "pending": pending, "count": len(pending)}
        )

    @router.post("/api/alerts/{token_mint}/approve")
    async def approve_alert_for_tweet(request: Request, token_mint: str):
        """Approve a token alert for Twitter posting."""
        user = get_current_user_fn(request)
        if not user or not user.get("is_admin"):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        token_mint = (token_mint or "").strip()
        if not token_mint:
            return JSONResponse(
                content={"success": False, "error": "token_mint required"},
                status_code=400,
            )

        approved = database.approve_tweet(
            token_mint, approved_by=user.get("email", "")
        )
        if not approved:
            return JSONResponse(
                content={"success": False, "error": "Token not found"},
                status_code=404,
            )

        return JSONResponse(
            content={
                "success": True,
                "message": f"Approved tweet for {token_mint}",
            }
        )

    return router
