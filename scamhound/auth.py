"""
Google OAuth authentication and JWT session management for ScamHound.
"""
import os
import logging
from datetime import datetime, timedelta, timezone

import jwt
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

logger = logging.getLogger(__name__)

# --- OAuth Setup ---
oauth = OAuth()
_oauth_initialized = False
_oauth_enabled = False


def init_oauth() -> bool:
    """Initialize Google OAuth client. Call after env vars are loaded.

    Returns:
        bool: True when Google OAuth is enabled and registered.

    Raises:
        RuntimeError: If OAuth variables are partially configured.
    """
    global _oauth_initialized, _oauth_enabled

    if _oauth_initialized:
        return _oauth_enabled

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id and not client_secret:
        _oauth_initialized = True
        _oauth_enabled = False
        logger.info(
            "[AUTH] Google OAuth disabled "
            "(missing GOOGLE_CLIENT_ID/SECRET)"
        )
        return False

    if not client_id or not client_secret:
        raise RuntimeError(
            "Google OAuth misconfigured: both GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET must be set together."
        )

    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=(
            "https://accounts.google.com/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid email profile"
        },
    )
    _oauth_initialized = True
    _oauth_enabled = True
    return True


def is_oauth_enabled() -> bool:
    """Return whether Google OAuth is configured and initialized."""
    return _oauth_enabled


# --- JWT Utilities ---
JWT_SECRET = None
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


def _get_jwt_secret():
    global JWT_SECRET
    if JWT_SECRET is None:
        secret = os.environ.get("JWT_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError(
                "JWT_SECRET must be set and at least 32 characters long."
            )
        JWT_SECRET = secret
    return JWT_SECRET


def get_jwt_secret() -> str:
    """Public accessor for shared JWT/session secret."""
    return _get_jwt_secret()


def create_jwt(user_id: int, email: str, is_admin: bool) -> str:
    """Create a signed JWT token for a user."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_admin": is_admin,
        "exp": (
            datetime.now(timezone.utc)
            + timedelta(hours=JWT_EXPIRATION_HOURS)
        ),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(
            token,
            _get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT validation failed: {e}")
        return None


# --- Request Helpers ---

def get_current_user(request: Request) -> dict:
    """
    Extract current user from JWT cookie.
    Returns user dict with id, email, is_admin or None if not authenticated.
    """
    token = request.cookies.get("scamhound_session")
    if not token:
        return None

    payload = decode_jwt(token)
    if not payload:
        return None

    return {
        "id": int(payload["sub"]),
        "email": payload["email"],
        "is_admin": payload.get("is_admin", False)
    }


def require_auth(request: Request):
    """
    Check if user is authenticated. Returns user dict or None.
    Use in routes that need login - redirect to /login if None.
    """
    return get_current_user(request)


def require_admin(request: Request) -> dict:
    """
    Check if user is authenticated AND admin.
    Returns user dict or None.
    """
    user = get_current_user(request)
    if user and user.get("is_admin"):
        return user
    return None
