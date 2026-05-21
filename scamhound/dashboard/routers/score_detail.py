"""
Score detail and score-history endpoints.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database


def create_score_detail_router(
    check_api_key_fn,
    add_rate_limit_headers_fn,
) -> APIRouter:
    """Create score detail router with injected auth/rate-limit helpers."""
    router = APIRouter()

    @router.get("/api/score/{token_mint}")
    @router.get("/api/_legacy/score/{token_mint}")
    async def api_score(request: Request, token_mint: str):
        """
        API endpoint for a single token score.
        """
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error

        token = database.get_token_score(token_mint)

        if not token:
            return JSONResponse(
                content={"error": "Token not found"},
                status_code=404,
            )

        response = JSONResponse(content=token)

        if key_row:
            database.increment_api_key_usage(
                key_row["id"], f"/api/score/{token_mint}"
            )
            response = add_rate_limit_headers_fn(response, key_row)

        return response

    @router.get("/api/score/{token_mint}/history")
    @router.get("/api/_legacy/score/{token_mint}/history")
    async def api_score_history(request: Request, token_mint: str):
        """Get score history for a token across rescoring events."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error

        history = database.get_score_history(token_mint)

        if not history:
            return JSONResponse(
                status_code=404,
                content={"error": "No score history found for this token"},
            )

        response_data = {
            "token_mint": token_mint,
            "scores": history,
            "total_scores": len(history),
            "score_change": (
                history[0]["risk_score"] - history[-1]["risk_score"]
                if len(history) > 1
                else 0
            ),
        }

        response = JSONResponse(content=response_data)

        if key_row:
            database.increment_api_key_usage(
                key_row["id"], f"/api/score/{token_mint}/history"
            )
            response = add_rate_limit_headers_fn(response, key_row)

        return response

    return router
