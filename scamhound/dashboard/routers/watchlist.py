"""
Watchlist endpoint wrappers.
"""

from fastapi import APIRouter, Request


def create_watchlist_router(
    api_watchlist_fn,
    api_add_to_watchlist_fn,
    api_remove_from_watchlist_fn,
    api_user_watchlist_fn,
    api_user_watchlist_add_fn,
    api_user_watchlist_remove_fn,
) -> APIRouter:
    """Create watchlist router by delegating to extracted handlers."""
    router = APIRouter()

    @router.get("/api/watchlist")
    async def api_watchlist(request: Request):
        return await api_watchlist_fn(request)

    @router.post("/api/watchlist")
    async def api_add_to_watchlist(request: Request):
        return await api_add_to_watchlist_fn(request)

    @router.delete("/api/watchlist/{wallet_address}")
    async def api_remove_from_watchlist(request: Request, wallet_address: str):
        return await api_remove_from_watchlist_fn(request, wallet_address)

    @router.get("/api/user/watchlist")
    async def api_user_watchlist(request: Request):
        return await api_user_watchlist_fn(request)

    @router.post("/api/user/watchlist")
    async def api_user_watchlist_add(request: Request):
        return await api_user_watchlist_add_fn(request)

    @router.delete("/api/user/watchlist/{wallet_address}")
    async def api_user_watchlist_remove(request: Request, wallet_address: str):
        return await api_user_watchlist_remove_fn(request, wallet_address)

    return router
