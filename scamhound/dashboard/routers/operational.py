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

    @router.get("/api/soak/audit")
    @router.get("/api/_legacy/soak/audit")
    async def api_soak_audit(limit: int = 200):
        """Get recent scoring quality/stability summary for soak monitoring."""
        summary = database.get_soak_audit_summary(limit=limit)
        return JSONResponse(content=summary)

    @router.get("/api/soak/audit/samples")
    @router.get("/api/_legacy/soak/audit/samples")
    async def api_soak_audit_samples(
        limit: int = 50,
        risk_level: str = None,
        randomize: bool = True,
    ):
        """Get sample token rows for manual soak-window review."""
        samples = database.get_soak_audit_samples(
            limit=limit,
            risk_level=risk_level,
            randomize=randomize,
        )
        return JSONResponse(
            content={
                "count": len(samples),
                "limit": max(1, min(int(limit), 200)),
                "risk_level_filter": (
                    str(risk_level).upper().strip() if risk_level else None
                ),
                "randomize": randomize,
                "samples": samples,
            }
        )

    return router
