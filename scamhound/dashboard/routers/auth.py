"""
Auth and session routes for dashboard UI/API.
"""

import os
import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from auth import oauth, is_oauth_enabled, get_current_user, create_jwt
from engine import database


def create_auth_router(templates) -> APIRouter:
    """Create auth router with injected template renderer."""
    router = APIRouter()
    logger = logging.getLogger(__name__)

    @router.get("/login")
    async def login_page(request: Request):
        """Show login page."""
        user = get_current_user(request)
        if user:
            return RedirectResponse(url="/", status_code=302)
        return templates.TemplateResponse("login.html", {"request": request})

    @router.get("/auth/login/google")
    async def login_google(request: Request):
        """Redirect to Google OAuth consent screen."""
        if not is_oauth_enabled():
            return RedirectResponse(
                url="/login?error=oauth_unavailable", status_code=302
            )
        redirect_uri = os.environ.get(
            "GOOGLE_REDIRECT_URI", request.url_for("auth_callback_google")
        )
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @router.get("/auth/callback/google")
    async def auth_callback_google(request: Request):
        """Handle Google OAuth callback."""
        if not is_oauth_enabled():
            return RedirectResponse(
                url="/login?error=oauth_unavailable", status_code=302
            )
        try:
            token = await oauth.google.authorize_access_token(request)
            user_info = token.get("userinfo")
            if not user_info:
                user_info = await oauth.google.userinfo(token=token)

            # Create or update user in database
            user = database.create_or_update_user(
                google_id=user_info["sub"],
                email=user_info["email"],
                name=user_info.get("name"),
                picture_url=user_info.get("picture"),
            )

            # Create JWT and set cookie
            jwt_token = create_jwt(user["id"], user["email"], user["is_admin"])
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                key="scamhound_session",
                value=jwt_token,
                max_age=86400,  # 24 hours
                httponly=True,
                samesite="lax",
                secure=(
                    os.environ.get("ENVIRONMENT", "production") == "production"
                ),
            )
            logger.info(
                "[AUTH] User logged in: "
                f"{user['email']} (admin={user['is_admin']})"
            )
            return response
        except Exception as e:
            logger.error(f"[AUTH] OAuth callback failed: {e}")
            return RedirectResponse(
                url="/login?error=auth_failed", status_code=302
            )

    @router.post("/auth/logout")
    async def logout(request: Request):
        """Clear session cookie."""
        response = JSONResponse({"success": True})
        response.delete_cookie("scamhound_session")
        return response

    @router.get("/api/auth/me")
    async def get_me(request: Request):
        """Return current authenticated user info."""
        user = get_current_user(request)
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        full_user = database.get_user_by_id(user["id"])
        return JSONResponse(
            {
                "authenticated": True,
                "user": {
                    "id": full_user["id"] if full_user else user["id"],
                    "email": user["email"],
                    "name": full_user.get("name", "") if full_user else "",
                    "picture_url": (
                        full_user.get("picture_url", "") if full_user else ""
                    ),
                    "is_admin": user["is_admin"],
                },
            }
        )

    return router
