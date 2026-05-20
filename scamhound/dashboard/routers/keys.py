"""
API key management endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database


def create_keys_router(
    get_current_user_fn, verify_auth_fn, tier_limits
) -> APIRouter:
    """Create API key management router."""
    router = APIRouter()

    @router.post("/api/keys/generate")
    async def generate_api_key(request: Request):
        """Generate a new free-tier API key for the authenticated user."""
        user = get_current_user_fn(request)
        if not user:
            return JSONResponse(
                content={"success": False, "error": "Authentication required"},
                status_code=401,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                content={"success": False, "error": "Invalid JSON body"},
                status_code=400,
            )

        if not isinstance(body, dict):
            return JSONResponse(
                content={"success": False, "error": "Invalid request body"},
                status_code=400,
            )

        user_email = str(user.get("email", "")).strip().lower()
        requested_email = str(body.get("email", "")).strip().lower()
        name = body.get("name", "").strip()

        email = requested_email or user_email
        if not email or "@" not in email:
            return JSONResponse(
                content={"success": False, "error": "Valid email required"},
                status_code=400,
            )

        # Non-admin users can only generate keys for their own account.
        if (
            requested_email
            and requested_email != user_email
            and not user.get("is_admin")
        ):
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Cannot generate keys for another user",
                },
                status_code=403,
            )

        # Limit: max 3 free keys per email
        existing = database.get_api_keys_by_email(email)
        active_keys = [k for k in existing if k.get("is_active")]
        if len(active_keys) >= 3:
            return JSONResponse(
                content={
                    "success": False,
                    "error": (
                        "Maximum 3 active keys per email. "
                        "Revoke an existing key first."
                    ),
                },
                status_code=400,
            )

        result = database.create_api_key(email=email, tier="free", name=name)
        daily_limit = tier_limits.get("free", 100)

        return JSONResponse(
            content={
                "success": True,
                "key": result["key"],
                "key_prefix": result["key_prefix"],
                "tier": "free",
                "daily_limit": daily_limit,
                "message": "Save this key — it cannot be retrieved again.",
            }
        )

    @router.get("/api/keys/status")
    async def api_key_status(request: Request):
        """Get status and usage for the provided API key."""
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                content={
                    "success": False,
                    "error": "X-API-Key header required",
                },
                status_code=401,
            )

        key_row = database.validate_api_key(api_key)
        if not key_row:
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Invalid or expired API key",
                },
                status_code=401,
            )

        tier = key_row.get("tier", "free")
        daily_limit = tier_limits.get(tier, 100)

        return JSONResponse(
            content={
                "success": True,
                "key_prefix": key_row["key_prefix"],
                "email": key_row["email"],
                "tier": tier,
                "name": key_row.get("name", ""),
                "calls_today": key_row.get("calls_today", 0),
                "calls_total": key_row.get("calls_total", 0),
                "daily_limit": (
                    daily_limit if daily_limit != -1 else "unlimited"
                ),
                "last_used_at": key_row.get("last_used_at"),
                "created_at": key_row.get("created_at"),
                "expires_at": key_row.get("expires_at"),
                "is_active": key_row.get("is_active"),
            }
        )

    @router.delete("/api/keys/revoke")
    async def revoke_key(request: Request):
        """Revoke an API key (admin only)."""
        if not verify_auth_fn(request):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                content={"success": False, "error": "Invalid JSON body"},
                status_code=400,
            )

        key_prefix = body.get("key_prefix", "").strip()
        if not key_prefix:
            return JSONResponse(
                content={"success": False, "error": "key_prefix required"},
                status_code=400,
            )

        key_data = database.get_api_key_by_prefix(key_prefix)
        if not key_data:
            return JSONResponse(
                content={"success": False, "error": "Key not found"},
                status_code=404,
            )

        database.revoke_api_key(key_data["id"])
        return JSONResponse(
            content={
                "success": True,
                "message": f"Key {key_prefix} revoked",
            }
        )

    @router.get("/api/keys/admin/list")
    async def list_api_keys(request: Request):
        """List all API keys (admin only)."""
        if not verify_auth_fn(request):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        keys = database.get_all_api_keys()
        return JSONResponse(
            content={"success": True, "keys": keys, "total": len(keys)}
        )

    return router
