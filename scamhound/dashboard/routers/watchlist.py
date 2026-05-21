"""Watchlist API routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database


def create_watchlist_router(
    is_admin_authenticated_fn,
    check_api_key_fn,
    add_rate_limit_headers_fn,
    is_valid_solana_address_fn,
    logger,
) -> APIRouter:
    """Create watchlist router with app-level dependency injection."""
    router = APIRouter()

    @router.get("/api/watchlist")
    @router.get("/api/_legacy/watchlist")
    async def api_watchlist(request: Request):
        """Return global watchlist entries (admin only)."""
        if not is_admin_authenticated_fn(request):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        watchlist = database.get_watchlist()
        return JSONResponse(content=watchlist)

    @router.post("/api/watchlist")
    @router.post("/api/_legacy/watchlist")
    async def api_add_to_watchlist(request: Request):
        """Add wallet to global watchlist (admin only)."""
        if not is_admin_authenticated_fn(request):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        try:
            data = await request.json()

            if not isinstance(data, dict):
                return JSONResponse(
                    content={"success": False, "error": "Invalid request body"},
                    status_code=400,
                )

            wallet_address = data.get("wallet_address", "").strip()
            label = data.get("label", "").strip()
            notes = data.get("notes", "").strip()

            if not wallet_address:
                return JSONResponse(
                    content={"success": False, "error": "Missing wallet_address"},
                    status_code=400,
                )

            if not is_valid_solana_address_fn(wallet_address):
                return JSONResponse(
                    content={
                        "success": False,
                        "error": "Invalid wallet address format",
                    },
                    status_code=400,
                )

            success = database.add_to_watchlist(wallet_address, label, notes)

            if success:
                return JSONResponse(
                    content={
                        "success": True,
                        "message": "Wallet added to watchlist",
                    }
                )
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Wallet already on watchlist",
                },
                status_code=409,
            )

        except Exception as e:
            logger.error(f"[WATCHLIST] Error adding to watchlist: {e}")
            return JSONResponse(
                content={"success": False, "error": str(e)},
                status_code=500,
            )

    @router.delete("/api/watchlist/{wallet_address}")
    @router.delete("/api/_legacy/watchlist/{wallet_address}")
    async def api_remove_from_watchlist(request: Request, wallet_address: str):
        """Remove wallet from global watchlist (admin only)."""
        if not is_admin_authenticated_fn(request):
            return JSONResponse(
                content={"success": False, "error": "Admin access required"},
                status_code=401,
            )

        if not is_valid_solana_address_fn(wallet_address):
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Invalid wallet address format",
                },
                status_code=400,
            )

        try:
            success = database.remove_from_watchlist(wallet_address)

            if success:
                return JSONResponse(
                    content={
                        "success": True,
                        "message": "Wallet removed from watchlist",
                    }
                )
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Wallet not found on watchlist",
                },
                status_code=404,
            )

        except Exception as e:
            logger.error(f"[WATCHLIST] Error removing from watchlist: {e}")
            return JSONResponse(
                content={"success": False, "error": str(e)},
                status_code=500,
            )

    @router.get("/api/user/watchlist")
    @router.get("/api/_legacy/user/watchlist")
    async def api_user_watchlist(request: Request):
        """Get authenticated API key's personal watchlist (Pro+)."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error
        if not key_row:
            return JSONResponse(
                status_code=401,
                content={"error": "API key required"},
            )
        if key_row["tier"] == "free":
            return JSONResponse(
                status_code=403,
                content={"error": "Pro tier or higher required for personal watchlist"},
            )

        watchlist = database.get_user_watchlist(key_row["id"])
        response = JSONResponse(
            content={"watchlist": watchlist, "count": len(watchlist)}
        )
        database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
        return add_rate_limit_headers_fn(response, key_row)

    @router.post("/api/user/watchlist")
    @router.post("/api/_legacy/user/watchlist")
    async def api_user_watchlist_add(request: Request):
        """Add wallet to personal watchlist (Pro+)."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error
        if not key_row:
            return JSONResponse(
                status_code=401,
                content={"error": "API key required"},
            )
        if key_row["tier"] == "free":
            return JSONResponse(
                status_code=403,
                content={"error": "Pro tier or higher required"},
            )

        body = await request.json()
        wallet_address = body.get("wallet_address", "").strip()
        label = body.get("label", "").strip()
        notes = body.get("notes", "").strip()

        if not wallet_address:
            return JSONResponse(
                status_code=400,
                content={"error": "wallet_address required"},
            )

        if not is_valid_solana_address_fn(wallet_address):
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid wallet address format"},
            )

        current = database.get_user_watchlist(key_row["id"])
        if len(current) >= 50:
            return JSONResponse(
                status_code=400,
                content={"error": "Watchlist limit reached (50 wallets max)"},
            )

        success = database.add_to_user_watchlist(
            key_row["id"], wallet_address, label, notes
        )
        if not success:
            return JSONResponse(
                status_code=409,
                content={"error": "Wallet already on watchlist"},
            )

        database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
        return JSONResponse(content={"success": True, "wallet_address": wallet_address})

    @router.delete("/api/user/watchlist/{wallet_address}")
    @router.delete("/api/_legacy/user/watchlist/{wallet_address}")
    async def api_user_watchlist_remove(request: Request, wallet_address: str):
        """Remove wallet from personal watchlist (Pro+)."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error
        if not key_row:
            return JSONResponse(
                status_code=401,
                content={"error": "API key required"},
            )
        if key_row["tier"] == "free":
            return JSONResponse(
                status_code=403,
                content={"error": "Pro tier or higher required"},
            )

        if not is_valid_solana_address_fn(wallet_address):
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid wallet address format"},
            )

        success = database.remove_from_user_watchlist(key_row["id"], wallet_address)
        if not success:
            return JSONResponse(
                status_code=404,
                content={"error": "Wallet not found on watchlist"},
            )

        database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
        return JSONResponse(content={"success": True})

    return router
