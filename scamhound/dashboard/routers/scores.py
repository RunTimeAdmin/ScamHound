"""
Score listing and score-clear endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database


def create_scores_router(
    get_current_user_fn,
    verify_auth_fn,
    check_api_key_fn,
    add_rate_limit_headers_fn,
    logger,
) -> APIRouter:
    """Create scores router with app-level dependency injection."""
    router = APIRouter()

    @router.get("/api/scores")
    @router.get("/api/_legacy/scores")
    async def api_scores(
        request: Request,
        limit: int = 50,
        page: int = None,
        search: str = None,
        risk_level: str = None,
        min_score: int = None,
        max_score: int = None,
        creator: str = None,
        from_date: str = None,
        to_date: str = None,
        sort_by: str = "scored_at",
        order: str = "desc",
    ):
        """
        API endpoint for scores.
        Returns JSON array of last N scores, or paginated/filtered results.
        """
        # Check API key for programmatic access
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error

        # For browser (non-API-key) access, require user auth
        user = None
        if not key_row:
            user = get_current_user_fn(request)
            if not user:
                return JSONResponse(
                    {"error": "Authentication required"}, status_code=401
                )

        # Validate risk_level
        allowed_risk_levels = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNSCORED"]
        if risk_level is not None and risk_level not in allowed_risk_levels:
            return JSONResponse(
                content={
                    "success": False,
                    "error": (
                        f"Invalid risk_level. Allowed: {allowed_risk_levels}"
                    ),
                },
                status_code=400,
            )

        # Validate sort_by
        allowed_sort_by = ["scored_at", "risk_score", "symbol", "name"]
        if sort_by not in allowed_sort_by:
            return JSONResponse(
                content={
                    "success": False,
                    "error": f"Invalid sort_by. Allowed: {allowed_sort_by}",
                },
                status_code=400,
            )

        # Validate order
        allowed_order = ["asc", "desc"]
        if order not in allowed_order:
            return JSONResponse(
                content={
                    "success": False,
                    "error": f"Invalid order. Allowed: {allowed_order}",
                },
                status_code=400,
            )

        # Determine if we should use paginated search
        use_search = page is not None or any(
            v is not None
            for v in [
                search,
                risk_level,
                min_score,
                max_score,
                creator,
                from_date,
                to_date,
            ]
        )

        if use_search:
            # Determine per_page cap based on tier
            per_page = limit
            tier = key_row.get("tier", "free") if key_row else "free"
            max_per_page = (
                500 if tier in ("pro", "builder", "enterprise") else 100
            )
            per_page = min(per_page, max_per_page)

            result = database.search_scored_tokens(
                search=search,
                risk_level=risk_level,
                min_score=min_score,
                max_score=max_score,
                creator=creator,
                from_date=from_date,
                to_date=to_date,
                sort_by=sort_by,
                order=order,
                page=page if page is not None else 1,
                per_page=per_page,
            )
            response = JSONResponse(content=result)
        else:
            # If regular (non-admin) user via browser, show only their scans
            if user and not user.get("is_admin"):
                scores = database.get_scores_for_user(user["id"], limit=limit)
            else:
                scores = database.get_recent_scores(limit=limit)
            response = JSONResponse(content=scores)

        if key_row:
            database.increment_api_key_usage(key_row["id"], "/api/scores")
            response = add_rate_limit_headers_fn(response, key_row)

        return response

    @router.delete("/api/scores/clear")
    @router.delete("/api/_legacy/scores/clear")
    async def clear_scores(request: Request):
        """Clear scan results. Admin clears all; regular user clears own."""
        request_id = getattr(request.state, "request_id", "")
        try:
            user = get_current_user_fn(request)
            if not user:
                # Fallback to old Bearer token auth (admin only)
                if not verify_auth_fn(request):
                    return JSONResponse(
                        content={
                            "success": False,
                            "error": "Unauthorized. Authentication required.",
                        },
                        status_code=401,
                    )
                # Old-style admin auth — clear all
                result = database.clear_all_scores()
                logger.info(
                    f"[SCAMHOUND] Cleared all scans (admin token): {result}"
                )
                return JSONResponse(content={"status": "cleared", **result})

            if user["is_admin"]:
                result = database.clear_all_scores()
                logger.info(
                    "[SCAMHOUND] Cleared all scans by admin "
                    f"{user['email']}: {result}"
                )
            else:
                result = database.clear_user_scans(user["id"])
                logger.info(
                    "[SCAMHOUND] Cleared user scans for "
                    f"{user['email']}: {result}"
                )
            return JSONResponse(content={"status": "cleared", **result})
        except Exception as e:
            logger.error(f"[SCAMHOUND] Failed to clear scans: {e}")
            return JSONResponse(
                content={
                    "success": False,
                    "error": "Internal Server Error",
                    "request_id": request_id,
                },
                status_code=500,
            )

    return router
