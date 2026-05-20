"""
Scan endpoint wrappers.
"""

from fastapi import APIRouter, Request


def create_scan_router(scan_token_fn, api_scan_batch_fn) -> APIRouter:
    """Create scan API router by delegating to extracted handlers."""
    router = APIRouter()

    @router.post("/api/scan")
    async def scan_token(request: Request):
        return await scan_token_fn(request)

    @router.post("/api/scan/batch")
    async def api_scan_batch(request: Request):
        return await api_scan_batch_fn(request)

    return router
