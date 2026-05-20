"""
Operational status endpoints.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine import database


def create_operational_router() -> APIRouter:
    """Create operational status router."""
    router = APIRouter()

    @router.get("/api/rescore/status")
    async def api_rescore_status():
        """Get info about the re-scoring system."""
        eligible = database.get_tokens_for_rescore(
            max_age_days=7, min_score=40, limit=100
        )
        return JSONResponse(
            content={
                "eligible_for_rescore": len(eligible),
                "rescore_interval_hours": 24,
                "min_score_threshold": 40,
                "max_age_days": 7,
            }
        )

    @router.get("/api/stats")
    async def api_stats():
        """
        API endpoint for statistics.
        """
        stats = database.get_stats()
        return JSONResponse(content=stats)

    @router.get("/api/platforms")
    async def api_platforms():
        """Get status of registered token feed platforms."""
        from clients import platform_router

        return JSONResponse(content=platform_router.get_platform_status())

    return router
