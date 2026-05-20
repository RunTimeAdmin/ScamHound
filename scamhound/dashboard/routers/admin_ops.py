"""
Admin ops/export endpoint wrappers.
"""

from fastapi import APIRouter, Request


def create_admin_ops_router(
    api_save_settings_fn,
    api_autoscan_status_fn,
    api_toggle_autoscan_fn,
    export_csv_fn,
    export_pdf_fn,
) -> APIRouter:
    """Create admin ops router by delegating to extracted handlers."""
    router = APIRouter()

    @router.post("/api/settings")
    async def api_save_settings(request: Request):
        return await api_save_settings_fn(request)

    @router.get("/api/autoscan/status")
    async def api_autoscan_status(request: Request):
        return await api_autoscan_status_fn(request)

    @router.post("/api/autoscan/toggle")
    async def api_toggle_autoscan(request: Request):
        return await api_toggle_autoscan_fn(request)

    @router.get("/api/export/csv")
    async def export_csv(request: Request):
        return await export_csv_fn(request)

    @router.get("/api/export/pdf")
    async def export_pdf(request: Request):
        return await export_pdf_fn(request)

    return router
