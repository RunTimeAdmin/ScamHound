"""
Health and readiness endpoints.
"""

import os
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine import database


def create_health_router() -> APIRouter:
    """Create health router."""
    router = APIRouter()

    @router.get("/health")
    async def health():
        """
        Health check endpoint.
        Used for uptime monitoring.
        """
        stats = database.get_stats()
        return JSONResponse(
            content={
                "status": "ok",
                "tokens_scored": stats.get("total_scanned", 0),
            }
        )

    @router.get("/api/health/deep")
    async def deep_health(probe: bool = False):
        """
        Deep health endpoint for dependency visibility.
        By default returns configuration/readiness checks only.
        Set probe=true to run live dependency probes.
        """
        from clients import helius_client
        from clients import birdeye_client
        from clients import bubblemaps_client

        checks: Dict[str, Dict[str, Any]] = {}

        # Database readiness
        try:
            stats = database.get_stats()
            checks["database"] = {
                "status": "healthy",
                "detail": {"tokens_scored": stats.get("total_scanned", 0)},
            }
        except Exception as e:
            checks["database"] = {"status": "unhealthy", "error": str(e)}

        # Configuration readiness
        checks["helius"] = {
            "status": (
                "configured"
                if os.environ.get("HELIUS_API_KEY")
                else "unconfigured"
            )
        }
        checks["birdeye"] = {
            "status": (
                "configured"
                if os.environ.get("BIRDEYE_API_KEY")
                else "unconfigured"
            )
        }
        checks["bubblemaps"] = {
            "status": (
                "configured"
                if os.environ.get("BUBBLEMAPS_API_KEY")
                else "unconfigured"
            ),
            "detail": bubblemaps_client.get_quota_status(),
        }

        llm_provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
        llm_key_present = bool(
            os.environ.get("DEEPSEEK_API_KEY")
            if llm_provider == "deepseek"
            else os.environ.get("ANTHROPIC_API_KEY")
        )
        checks["llm"] = {
            "status": "configured" if llm_key_present else "unconfigured",
            "provider": llm_provider,
        }

        # Optional live probes (network/costly)
        if probe:
            try:
                probe_result = helius_client.get_wallet_transaction_history(
                    "11111111111111111111111111111111", limit=1
                )
                checks["helius"]["live_probe"] = (
                    "ok" if probe_result is not None else "failed"
                )
            except Exception as e:
                checks["helius"]["live_probe"] = "failed"
                checks["helius"]["probe_error"] = str(e)

            try:
                probe_result = birdeye_client.get_token_overview(
                    "So11111111111111111111111111111111111111112"
                )
                checks["birdeye"]["live_probe"] = (
                    "ok" if probe_result is not None else "failed"
                )
            except Exception as e:
                checks["birdeye"]["live_probe"] = "failed"
                checks["birdeye"]["probe_error"] = str(e)

        overall_status = "ok"
        if any(v.get("status") == "unhealthy" for v in checks.values()):
            overall_status = "degraded"
        elif any(v.get("status") == "unconfigured" for v in checks.values()):
            overall_status = "degraded"
        elif probe and any(
            v.get("live_probe") == "failed" for v in checks.values()
        ):
            overall_status = "degraded"

        return JSONResponse(
            content={
                "status": overall_status,
                "probe_enabled": probe,
                "checks": checks,
            }
        )

    @router.get("/api/health/bubblemaps")
    async def bubblemaps_quota():
        """
        BubbleMaps API quota status endpoint.
        Returns daily quota usage and reset information.
        """
        from clients import bubblemaps_client

        return JSONResponse(content=bubblemaps_client.get_quota_status())

    return router
