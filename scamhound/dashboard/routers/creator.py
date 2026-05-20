"""
Creator and leaderboard API endpoint wrappers.
"""

from fastapi import APIRouter, Request


def create_creator_router(
    api_leaderboard_fn,
    api_creator_reputation_fn,
) -> APIRouter:
    """Create creator API router by delegating to extracted handlers."""
    router = APIRouter()

    @router.get("/api/leaderboard")
    async def api_leaderboard(
        request: Request,
        sort_by: str = "avg_risk",
        order: str = "desc",
        limit: int = 50,
        min_tokens: int = 2,
    ):
        return await api_leaderboard_fn(
            request, sort_by=sort_by, order=order, limit=limit, min_tokens=min_tokens
        )

    @router.get("/api/creator/{wallet_address}")
    async def api_creator_reputation(request: Request, wallet_address: str):
        return await api_creator_reputation_fn(request, wallet_address)

    return router
