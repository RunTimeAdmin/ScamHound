"""
Google OAuth authentication and JWT session management for ScamHound.
"""
import os
import logging
from datetime import datetime, timedelta

import jwt
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

logger = logging.getLogger(__name__)

# --- OAuth Setup ---
oauth = OAuth()


def init_oauth():
    """Initialize Google OAuth client. Call after env vars are loaded."""
    oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        server_metadata_url=(
            'https://accounts.google.com/.well-known/openid-configuration'
        ),
        client_kwargs={
            'scope': 'openid email profile'
        }
    )


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
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
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
