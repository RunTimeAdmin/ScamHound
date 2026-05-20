"""
ScamHound Dashboard Backend
FastAPI web server serving the live dashboard and widget
"""

import os
import logging
import time
import threading
import csv
import io
import re
import uuid
from xml.sax.saxutils import escape
from datetime import datetime
from typing import Dict, List, Optional
# Type imports removed - not needed

from fastapi import (
    FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from engine import database
from engine import monitor
from config import get_masked_keys, save_config, load_config
from auth import (
    oauth,
    init_oauth,
    get_current_user,
    create_jwt,
    get_jwt_secret,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Rate limiting storage: {ip: [timestamp1, timestamp2, ...]}
_rate_limit_store: Dict[str, List[float]] = {}
_MAX_SCANS_PER_MINUTE = 5
_RATE_LIMIT_WINDOW = 60  # seconds

# Rate limit store cleanup tracking
_rate_limit_last_cleanup: float = 0
_RATE_LIMIT_CLEANUP_INTERVAL = 300  # 5 minutes in seconds

# API Key tier rate limits (calls per day)
TIER_LIMITS = {
    "free": 100,
    "pro": 10000,
    "builder": 100000,
    "enterprise": -1,  # unlimited
}

# Auto-scan scheduler state
_autoscan_scheduler: Optional[BackgroundScheduler] = None
_autoscan_enabled: bool = False
_autoscan_interval: int = 60  # seconds
_autoscan_lock = threading.Lock()
_AUTO_SCAN_ALLOWED = os.environ.get("AUTO_SCAN_ENABLED", "false").lower() == "true"

# WebSocket active connections
_websocket_connections: set = set()
_websocket_lock = threading.Lock()
_main_event_loop = None
_background_scan_tasks: set = set()


def _cleanup_rate_limit_store():
    """
    Remove entries older than the rate limit window from all IPs.
    Called periodically to prevent unbounded growth.
    """
    global _rate_limit_last_cleanup
    now = time.time()
    
    # Remove old timestamps for each IP
    for ip in list(_rate_limit_store.keys()):
        _rate_limit_store[ip] = [
            ts for ts in _rate_limit_store[ip]
            if now - ts < _RATE_LIMIT_WINDOW
        ]
        # Remove IP entry if empty
        if not _rate_limit_store[ip]:
            del _rate_limit_store[ip]
    
    _rate_limit_last_cleanup = now
    remaining = len(_rate_limit_store)
    logger.debug(f"[RATE_LIMIT] Cleaned up store, remaining IPs: {remaining}")


def _check_rate_limit(ip: str) -> tuple[bool, int, int]:
    """
    Check if IP has exceeded rate limit.
    Returns: (allowed, remaining, retry_after)
    """
    global _rate_limit_last_cleanup
    now = time.time()
    
    # Periodic cleanup of old entries (every 5 minutes)
    if now - _rate_limit_last_cleanup >= _RATE_LIMIT_CLEANUP_INTERVAL:
        _cleanup_rate_limit_store()
    
    # Clean up old entries for this IP
    if ip in _rate_limit_store:
        _rate_limit_store[ip] = [
            ts for ts in _rate_limit_store[ip] 
            if now - ts < _RATE_LIMIT_WINDOW
        ]
    else:
        _rate_limit_store[ip] = []
    
    # Check if limit exceeded
    if len(_rate_limit_store[ip]) >= _MAX_SCANS_PER_MINUTE:
        oldest = min(_rate_limit_store[ip])
        retry_after = int(_RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return False, 0, retry_after
    
    # Record this request
    _rate_limit_store[ip].append(now)
    remaining = _MAX_SCANS_PER_MINUTE - len(_rate_limit_store[ip])
    return True, remaining, 0


def _verify_auth(request: Request) -> bool:
    """
    Verify authentication token via Authorization: Bearer header only.
    Returns True if authorized, False otherwise.
    If SCAMHOUND_ADMIN_TOKEN is not set, allows access (dev mode).
    """
    expected_token = os.environ.get("SCAMHOUND_ADMIN_TOKEN", "")
    
    # Dev mode: no token configured, allow access
    if not expected_token:
        return True
    
    # Check Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        provided_token = auth_header[7:]  # Remove "Bearer " prefix
        if provided_token == expected_token:
            return True
    
    return False


def _check_api_key(request: Request) -> tuple:
    """
    Check API key from X-API-Key header.
    Returns: (key_row_or_None, error_response_or_None)

    If key is present and valid: (key_row, None)
    If key is present but invalid: (None, JSONResponse with 401)
    If key is present but over limit: (None, JSONResponse with 429)
    If no key present: (None, None) -- fall back to IP rate limiting
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return None, None  # No key = use IP-based rate limiting

    key_row = database.validate_api_key(api_key)
    if not key_row:
        return None, JSONResponse(
            content={"success": False, "error": "Invalid or expired API key."},
            status_code=401
        )

    # Check daily limit
    tier = key_row.get("tier", "free")
    daily_limit = TIER_LIMITS.get(tier, 100)

    if daily_limit != -1 and key_row.get("calls_today", 0) >= daily_limit:
        return None, JSONResponse(
            content={
                "success": False,
                "error": f"Daily API limit exceeded ({daily_limit} calls/day for {tier} tier). Resets at UTC midnight."
            },
            status_code=429,
            headers={
                "X-RateLimit-Limit": str(daily_limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After": "3600"
            }
        )

    return key_row, None


def _get_client_ip(request: Request) -> str:
    """
    Resolve client IP behind reverse proxies.
    Prefers X-Forwarded-For then X-Real-IP, with socket fallback.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        first_hop = forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop

    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip

    return request.client.host if request.client else "unknown"


def _is_valid_solana_address(value: str) -> bool:
    """Validate Solana base58 address/mint format."""
    return bool(SOLANA_ADDRESS_RE.fullmatch(value or ""))


def _add_rate_limit_headers(response: JSONResponse, key_row: dict) -> JSONResponse:
    """Add X-RateLimit headers to response."""
    if key_row:
        tier = key_row.get("tier", "free")
        daily_limit = TIER_LIMITS.get(tier, 100)
        calls_today = key_row.get("calls_today", 0) + 1  # +1 for current request
        remaining = max(0, daily_limit - calls_today) if daily_limit != -1 else "unlimited"

        response.headers["X-RateLimit-Limit"] = str(daily_limit) if daily_limit != -1 else "unlimited"
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# Create FastAPI app
app = FastAPI(
    title="ScamHound",
    description="On-demand rug pull detection for Solana",
    version="1.0.0"
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """Attach a per-request ID for log and client correlation."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Session middleware required by Authlib for OAuth state
app.add_middleware(SessionMiddleware, secret_key=get_jwt_secret())

# Get the directory paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "dashboard", "templates")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/login")
async def login_page(request: Request):
    """Show login page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/auth/login/google")
async def login_google(request: Request):
    """Redirect to Google OAuth consent screen."""
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", request.url_for("auth_callback_google"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback/google")
async def auth_callback_google(request: Request):
    """Handle Google OAuth callback."""
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
            user_info = await oauth.google.userinfo(token=token)

        # Create or update user in database
        user = database.create_or_update_user(
            google_id=user_info['sub'],
            email=user_info['email'],
            name=user_info.get('name'),
            picture_url=user_info.get('picture')
        )

        # Create JWT and set cookie
        jwt_token = create_jwt(user['id'], user['email'], user['is_admin'])
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="scamhound_session",
            value=jwt_token,
            max_age=86400,  # 24 hours
            httponly=True,
            samesite="lax",
            secure=os.environ.get("ENVIRONMENT", "production") == "production"
        )
        logger.info(f"[AUTH] User logged in: {user['email']} (admin={user['is_admin']})")
        return response
    except Exception as e:
        logger.error(f"[AUTH] OAuth callback failed: {e}")
        return RedirectResponse(url="/login?error=auth_failed", status_code=302)


@app.post("/auth/logout")
async def logout(request: Request):
    """Clear session cookie."""
    response = JSONResponse({"success": True})
    response.delete_cookie("scamhound_session")
    return response


@app.get("/api/auth/me")
async def get_me(request: Request):
    """Return current authenticated user info."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)
    full_user = database.get_user_by_id(user['id'])
    return JSONResponse({
        "authenticated": True,
        "user": {
            "id": full_user['id'] if full_user else user['id'],
            "email": user['email'],
            "name": full_user.get('name', '') if full_user else '',
            "picture_url": full_user.get('picture_url', '') if full_user else '',
            "is_admin": user['is_admin']
        }
    })


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Main dashboard page.
    Shows last 50 scored tokens with auto-refresh.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    scores = database.get_recent_scores(limit=50)
    stats = database.get_stats()
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "scores": scores,
            "stats": stats,
            "user": user
        }
    )


@app.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request):
    """
    Watchlist page.
    Shows all watched wallets with management UI.
    """
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)

    watchlist = database.get_watchlist()
    
    return templates.TemplateResponse(
        "watchlist.html",
        {
            "request": request,
            "watchlist": watchlist
        }
    )


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard_page(request: Request):
    """Creator reputation leaderboard page."""
    leaderboard = database.get_creator_leaderboard(sort_by="avg_risk", order="desc", limit=50, min_tokens=2)
    stats = database.get_stats()
    return templates.TemplateResponse("leaderboard.html", {"request": request, "leaderboard": leaderboard, "stats": stats})


@app.get("/api/leaderboard")
async def api_leaderboard(request: Request, sort_by: str = "avg_risk", order: str = "desc", limit: int = 50, min_tokens: int = 2):
    """Get creator reputation leaderboard."""
    valid_sorts = ["avg_risk", "total_tokens", "high_risk_count", "last_active"]
    if sort_by not in valid_sorts:
        return JSONResponse(status_code=400, content={"error": f"Invalid sort_by. Must be one of: {valid_sorts}"})
    if order not in ["asc", "desc"]:
        return JSONResponse(status_code=400, content={"error": "Invalid order. Must be 'asc' or 'desc'"})
    
    leaderboard = database.get_creator_leaderboard(sort_by=sort_by, order=order, limit=min(limit, 100), min_tokens=min_tokens)
    
    # Track API key usage if present
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    
    response = JSONResponse(content={"leaderboard": leaderboard, "count": len(leaderboard)})
    if key_row:
        database.increment_api_key_usage(key_row["id"], "/api/leaderboard")
        response = _add_rate_limit_headers(response, key_row)
    return response


@app.get("/token/{token_mint}", response_class=HTMLResponse)
async def token_detail(request: Request, token_mint: str):
    """
    Token detail page.
    Shows full score data for a single token.
    """
    token = database.get_token_score(token_mint)
    
    if not token:
        return templates.TemplateResponse(
            "token_detail.html",
            {
                "request": request,
                "token": None,
                "score_history": [],
                "error": "Token not found"
            },
            status_code=404
        )
    
    score_history = database.get_score_history(token_mint)
    
    return templates.TemplateResponse(
        "token_detail.html",
        {
            "request": request,
            "token": token,
            "score_history": score_history
        }
    )


@app.get("/widget/{token_mint}", response_class=HTMLResponse)
async def widget(request: Request, token_mint: str):
    """
    Embeddable widget badge.
    Minimal display for embedding on Bags token pages.
    """
    token = database.get_token_score(token_mint)
    
    return templates.TemplateResponse(
        "widget.html",
        {
            "request": request,
            "token": token,
            "token_mint": token_mint
        }
    )


@app.get("/api/scores")
async def api_scores(
    request: Request,
    limit: int = 50,
    page: int = None,
    search: str = None,
    risk_level: str = None,
    min_score: int = None,
    max_score: int = None,
    creator: str = None,
    from_date: str = None,
    to_date: str = None,
    sort_by: str = "scored_at",
    order: str = "desc"
):
    """
    API endpoint for scores.
    Returns JSON array of last N scores, or paginated/filtered results.
    """
    # Check API key for programmatic access
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error

    # For browser (non-API-key) access, require user auth
    user = None
    if not key_row:
        user = get_current_user(request)
        if not user:
            return JSONResponse({"error": "Authentication required"}, status_code=401)

    # Validate risk_level
    allowed_risk_levels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    if risk_level is not None and risk_level not in allowed_risk_levels:
        return JSONResponse(
            content={"success": False, "error": f"Invalid risk_level. Allowed: {allowed_risk_levels}"},
            status_code=400
        )

    # Validate sort_by
    allowed_sort_by = ["scored_at", "risk_score", "symbol", "name"]
    if sort_by not in allowed_sort_by:
        return JSONResponse(
            content={"success": False, "error": f"Invalid sort_by. Allowed: {allowed_sort_by}"},
            status_code=400
        )

    # Validate order
    allowed_order = ["asc", "desc"]
    if order not in allowed_order:
        return JSONResponse(
            content={"success": False, "error": f"Invalid order. Allowed: {allowed_order}"},
            status_code=400
        )

    # Determine if we should use paginated search
    use_search = page is not None or any(v is not None for v in [search, risk_level, min_score, max_score, creator, from_date, to_date])

    if use_search:
        # Determine per_page cap based on tier
        per_page = limit
        tier = key_row.get("tier", "free") if key_row else "free"
        max_per_page = 500 if tier in ("pro", "builder", "enterprise") else 100
        per_page = min(per_page, max_per_page)

        result = database.search_scored_tokens(
            search=search,
            risk_level=risk_level,
            min_score=min_score,
            max_score=max_score,
            creator=creator,
            from_date=from_date,
            to_date=to_date,
            sort_by=sort_by,
            order=order,
            page=page if page is not None else 1,
            per_page=per_page
        )
        response = JSONResponse(content=result)
    else:
        # If regular (non-admin) user via browser, show only their scans
        if user and not user.get('is_admin'):
            scores = database.get_scores_for_user(user['id'], limit=limit)
        else:
            scores = database.get_recent_scores(limit=limit)
        response = JSONResponse(content=scores)

    if key_row:
        database.increment_api_key_usage(key_row["id"], "/api/scores")
        response = _add_rate_limit_headers(response, key_row)

    return response


@app.get("/api/score/{token_mint}")
async def api_score(request: Request, token_mint: str):
    """
    API endpoint for a single token score.
    """
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error

    token = database.get_token_score(token_mint)
    
    if not token:
        return JSONResponse(
            content={"error": "Token not found"},
            status_code=404
        )
    
    response = JSONResponse(content=token)

    if key_row:
        database.increment_api_key_usage(key_row["id"], f"/api/score/{token_mint}")
        response = _add_rate_limit_headers(response, key_row)

    return response


@app.get("/api/score/{token_mint}/history")
async def api_score_history(request: Request, token_mint: str):
    """Get score history for a token. Shows how risk score evolved over time."""
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error

    history = database.get_score_history(token_mint)

    if not history:
        return JSONResponse(status_code=404, content={"error": "No score history found for this token"})

    response_data = {
        "token_mint": token_mint,
        "scores": history,
        "total_scores": len(history),
        "score_change": history[0]["risk_score"] - history[-1]["risk_score"] if len(history) > 1 else 0
    }

    response = JSONResponse(content=response_data)

    if key_row:
        database.increment_api_key_usage(key_row["id"], f"/api/score/{token_mint}/history")
        response = _add_rate_limit_headers(response, key_row)

    return response


@app.get("/api/rescore/status")
async def api_rescore_status(request: Request):
    """Get info about the re-scoring system."""
    eligible = database.get_tokens_for_rescore(max_age_days=7, min_score=40, limit=100)
    return JSONResponse(content={
        "eligible_for_rescore": len(eligible),
        "rescore_interval_hours": 24,
        "min_score_threshold": 40,
        "max_age_days": 7
    })


@app.get("/api/stats")
async def api_stats():
    """
    API endpoint for statistics.
    """
    stats = database.get_stats()
    return JSONResponse(content=stats)


@app.get("/api/watchlist")
async def api_watchlist(request: Request):
    """
    API endpoint for watchlist.
    Returns all watchlist entries as JSON.
    """
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse(
            content={"success": False, "error": "Admin access required"},
            status_code=401
        )

    watchlist = database.get_watchlist()
    return JSONResponse(content=watchlist)


@app.post("/api/watchlist")
async def api_add_to_watchlist(request: Request):
    """
    Add a wallet to the watchlist.
    Accepts JSON: {"wallet_address": "...", "label": "...", "notes": "..."}
    """
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse(
            content={"success": False, "error": "Admin access required"},
            status_code=401
        )

    try:
        data = await request.json()
        
        if not isinstance(data, dict):
            return JSONResponse(
                content={"success": False, "error": "Invalid request body"},
                status_code=400
            )
        
        wallet_address = data.get("wallet_address", "").strip()
        label = data.get("label", "").strip()
        notes = data.get("notes", "").strip()
        
        if not wallet_address:
            return JSONResponse(
                content={"success": False, "error": "Missing wallet_address"},
                status_code=400
            )
        
        # Validate Solana wallet format (base58, 32-44 chars)
        if not _is_valid_solana_address(wallet_address):
            return JSONResponse(
                content={"success": False, "error": "Invalid wallet address format"},
                status_code=400
            )
        
        success = database.add_to_watchlist(wallet_address, label, notes)
        
        if success:
            return JSONResponse(
                content={"success": True, "message": "Wallet added to watchlist"}
            )
        else:
            return JSONResponse(
                content={"success": False, "error": "Wallet already on watchlist"},
                status_code=409
            )
    
    except Exception as e:
        logger.error(f"[WATCHLIST] Error adding to watchlist: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.delete("/api/watchlist/{wallet_address}")
async def api_remove_from_watchlist(request: Request, wallet_address: str):
    """
    Remove a wallet from the watchlist.
    """
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse(
            content={"success": False, "error": "Admin access required"},
            status_code=401
        )

    if not _is_valid_solana_address(wallet_address):
        return JSONResponse(
            content={"success": False, "error": "Invalid wallet address format"},
            status_code=400
        )

    try:
        success = database.remove_from_watchlist(wallet_address)
        
        if success:
            return JSONResponse(
                content={"success": True, "message": "Wallet removed from watchlist"}
            )
        else:
            return JSONResponse(
                content={"success": False, "error": "Wallet not found on watchlist"},
                status_code=404
            )
    
    except Exception as e:
        logger.error(f"[WATCHLIST] Error removing from watchlist: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.get("/api/user/watchlist")
async def api_user_watchlist(request: Request):
    """Get the authenticated user's personal watchlist. Requires Pro+ API key."""
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row:
        return JSONResponse(status_code=401, content={"error": "API key required"})
    if key_row["tier"] == "free":
        return JSONResponse(status_code=403, content={"error": "Pro tier or higher required for personal watchlist"})

    watchlist = database.get_user_watchlist(key_row["id"])
    response = JSONResponse(content={"watchlist": watchlist, "count": len(watchlist)})
    database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
    return _add_rate_limit_headers(response, key_row)


@app.post("/api/user/watchlist")
async def api_user_watchlist_add(request: Request):
    """Add a wallet to personal watchlist. Requires Pro+ API key."""
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row:
        return JSONResponse(status_code=401, content={"error": "API key required"})
    if key_row["tier"] == "free":
        return JSONResponse(status_code=403, content={"error": "Pro tier or higher required"})

    body = await request.json()
    wallet_address = body.get("wallet_address", "").strip()
    label = body.get("label", "").strip()
    notes = body.get("notes", "").strip()

    if not wallet_address:
        return JSONResponse(status_code=400, content={"error": "wallet_address required"})

    if not _is_valid_solana_address(wallet_address):
        return JSONResponse(status_code=400, content={"error": "Invalid wallet address format"})

    # Cap at 50 wallets per user
    current = database.get_user_watchlist(key_row["id"])
    if len(current) >= 50:
        return JSONResponse(status_code=400, content={"error": "Watchlist limit reached (50 wallets max)"})

    success = database.add_to_user_watchlist(key_row["id"], wallet_address, label, notes)
    if not success:
        return JSONResponse(status_code=409, content={"error": "Wallet already on watchlist"})

    database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
    return JSONResponse(content={"success": True, "wallet_address": wallet_address})


@app.delete("/api/user/watchlist/{wallet_address}")
async def api_user_watchlist_remove(request: Request, wallet_address: str):
    """Remove a wallet from personal watchlist. Requires Pro+ API key."""
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row:
        return JSONResponse(status_code=401, content={"error": "API key required"})
    if key_row["tier"] == "free":
        return JSONResponse(status_code=403, content={"error": "Pro tier or higher required"})

    if not _is_valid_solana_address(wallet_address):
        return JSONResponse(status_code=400, content={"error": "Invalid wallet address format"})

    success = database.remove_from_user_watchlist(key_row["id"], wallet_address)
    if not success:
        return JSONResponse(status_code=404, content={"error": "Wallet not found on watchlist"})

    database.increment_api_key_usage(key_row["id"], "/api/user/watchlist")
    return JSONResponse(content={"success": True})


@app.get("/api/creator/{wallet_address}")
async def api_creator_reputation(request: Request, wallet_address: str):
    """
    Get aggregated reputation data for a creator wallet.
    Returns stats about all tokens launched by this creator.
    """
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error

    try:
        reputation = database.get_creator_reputation(wallet_address)
        
        if reputation is None:
            return JSONResponse(
                content={
                    "success": False,
                    "error": "No tokens found for this creator",
                    "wallet_address": wallet_address
                },
                status_code=404
            )
        
        response = JSONResponse(content={"success": True, "data": reputation})

        if key_row:
            database.increment_api_key_usage(key_row["id"], f"/api/creator/{wallet_address}")
            response = _add_rate_limit_headers(response, key_row)

        return response
    
    except Exception as e:
        logger.error(f"[CREATOR] Error fetching creator reputation: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/api/scan")
async def scan_token(request: Request):
    """
    Manually trigger a scan for a specific token mint address.
    Accepts JSON: {"mint": "TOKEN_MINT_ADDRESS"}
    Returns the score result.
    Rate limited: 5 scans per minute per IP (or API key tier limit).
    """
    # Check API key first
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error

    # For browser (non-API-key) access, require user auth
    user = None
    if not key_row:
        user = get_current_user(request)
        if not user:
            return JSONResponse({"error": "Authentication required"}, status_code=401)

    # If no API key, fall back to IP-based rate limiting
    if not key_row:
        client_ip = _get_client_ip(request)
        allowed, remaining, retry_after = _check_rate_limit(client_ip)
        
        if not allowed:
            return JSONResponse(
                content={
                    "success": False,
                    "error": (
                        f"Rate limit exceeded. Max {_MAX_SCANS_PER_MINUTE} "
                        f"scans/min. Retry in {retry_after}s."
                    )
                },
                status_code=429,
                headers={"Retry-After": str(retry_after)}
            )

    # Check per-user daily scan limit (browser users only)
    scan_check = None
    if user:
        scan_check = database.check_and_increment_scan(user['id'], user.get('is_admin', False))
        if not scan_check['allowed']:
            return JSONResponse(
                content={
                    "success": False,
                    "error": f"Daily scan limit reached ({scan_check['scans_today']}/{scan_check['limit']}). Resets at midnight UTC."
                },
                status_code=429
            )

    try:
        data = await request.json()
        
        if not isinstance(data, dict):
            return JSONResponse(
                content={"success": False, "error": "Invalid request body"},
                status_code=400
            )
        
        token_mint = data.get("mint")
        
        if not token_mint:
            return JSONResponse(
                content={"success": False, "error": "Missing 'mint' field"},
                status_code=400
            )
        
        # Validate Solana mint format (base58, 32-44 chars)
        if not _is_valid_solana_address(token_mint):
            return JSONResponse(
                content={"success": False, "error": "Invalid mint address format"},
                status_code=400
            )
        
        request_id = getattr(request.state, "request_id", "unknown")
        logger.info(
            f"[DASHBOARD] req={request_id} Manual scan requested for: "
            f"{token_mint[:8]}..."
        )

        # Run the scan using the async version for parallel API calls
        result = await monitor.scan_single_token_async(
            token_mint, skip_if_scored=False
        )
        
        if result is None:
            return JSONResponse(
                content={"success": False, "error": "Scan failed or token not found"},
                status_code=500
            )
        
        # Associate scan with user if logged in via browser
        if user and result and result.get("token_mint"):
            try:
                conn = database.get_connection()
                conn.execute(
                    "UPDATE scored_tokens SET user_id = ? WHERE token_mint = ?",
                    (user['id'], result["token_mint"])
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

        response_data = {"success": True, "result": result}

        # Include remaining scans info for browser users
        if scan_check and scan_check['limit'] > 0:
            response_data["scans_remaining"] = scan_check['limit'] - scan_check['scans_today']
        elif scan_check and scan_check['limit'] == -1:
            response_data["scans_remaining"] = -1

        response = JSONResponse(content=response_data)

        if key_row:
            database.increment_api_key_usage(key_row["id"], "/api/scan")
            response = _add_rate_limit_headers(response, key_row)

        return response
        
    except Exception as e:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(f"[DASHBOARD] req={request_id} Error in scan_token: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/api/scan/batch")
async def api_scan_batch(request: Request):
    """Batch scan up to 50 token mints. Requires Builder tier or higher API key.
    
    Body: {"mints": ["mint1", "mint2", ...]}
    
    Returns cached scores immediately for already-scored tokens.
    Triggers fresh scans for unknown tokens (async, non-blocking).
    
    Rate cost: 1 batch request = 10 regular API call units.
    """
    # 1. Check API key — require Builder tier or higher
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row:
        return JSONResponse(status_code=401, content={"error": "API key required. Batch scanning requires Builder tier."})
    if key_row["tier"] not in ("builder", "enterprise"):
        return JSONResponse(status_code=403, content={"error": "Batch scan requires Builder tier or higher"})
    
    request_id = getattr(request.state, "request_id", "unknown")

    # 2. Parse body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})
    
    mints = body.get("mints", [])
    
    if not isinstance(mints, list):
        return JSONResponse(status_code=400, content={"error": "mints must be an array"})
    if len(mints) == 0:
        return JSONResponse(status_code=400, content={"error": "mints array cannot be empty"})
    if len(mints) > 50:
        return JSONResponse(status_code=400, content={"error": "Maximum 50 mints per batch"})
    
    # Validate each mint is a non-empty, base58-formatted Solana address
    mints = [
        m.strip() for m in mints
        if isinstance(m, str) and _is_valid_solana_address(m.strip())
    ]
    if not mints:
        return JSONResponse(status_code=400, content={"error": "No valid mint addresses provided"})
    
    # 3. Charge 10 API call units for the batch
    database.increment_api_key_usage(key_row["id"], "/api/scan/batch", count=10)
    
    # 4. Process: return cached scores, queue fresh scans for unknowns
    results = []
    to_scan = []
    
    for mint in mints:
        # Check if already scored
        existing = database.get_score_by_mint(mint)
        if existing:
            results.append({
                "token_mint": mint,
                "status": "cached",
                "data": existing
            })
        else:
            to_scan.append(mint)
            results.append({
                "token_mint": mint,
                "status": "queued",
                "data": None
            })
    
    # 5. Trigger background scans for unknown tokens (non-blocking)
    if to_scan:
        import asyncio
        async def _background_scan(mint_list):
            for mint in mint_list:
                try:
                    await monitor.scan_single_token_async(mint, skip_if_scored=False)
                except Exception:
                    pass

        task = asyncio.create_task(_background_scan(to_scan))
        _background_scan_tasks.add(task)
        task.add_done_callback(_background_scan_tasks.discard)
        logger.info(
            f"[DASHBOARD] req={request_id} queued {len(to_scan)} "
            "batch scans in background"
        )
    
    # 6. Return response
    response = JSONResponse(content={
        "results": results,
        "total": len(mints),
        "cached": len(mints) - len(to_scan),
        "queued": len(to_scan),
        "message": f"{len(to_scan)} tokens queued for scanning. Check back in 30-60 seconds for results." if to_scan else "All tokens found in cache."
    })
    
    response = _add_rate_limit_headers(response, key_row)
    return response


@app.get("/health")
async def health():
    """
    Health check endpoint.
    Used for uptime monitoring.
    """
    stats = database.get_stats()
    return JSONResponse(content={
        "status": "ok",
        "tokens_scored": stats.get("total_scanned", 0)
    })


@app.get("/api/health/deep")
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
    checks["helius"] = {"status": "configured" if os.environ.get("HELIUS_API_KEY") else "unconfigured"}
    checks["birdeye"] = {"status": "configured" if os.environ.get("BIRDEYE_API_KEY") else "unconfigured"}
    checks["bubblemaps"] = {
        "status": "configured" if os.environ.get("BUBBLEMAPS_API_KEY") else "unconfigured",
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
            checks["helius"]["live_probe"] = "ok" if probe_result is not None else "failed"
        except Exception as e:
            checks["helius"]["live_probe"] = "failed"
            checks["helius"]["probe_error"] = str(e)

        try:
            probe_result = birdeye_client.get_token_overview(
                "So11111111111111111111111111111111111111112"
            )
            checks["birdeye"]["live_probe"] = "ok" if probe_result is not None else "failed"
        except Exception as e:
            checks["birdeye"]["live_probe"] = "failed"
            checks["birdeye"]["probe_error"] = str(e)

    overall_status = "ok"
    if any(v.get("status") == "unhealthy" for v in checks.values()):
        overall_status = "degraded"
    elif any(v.get("status") == "unconfigured" for v in checks.values()):
        overall_status = "degraded"
    elif probe and any(v.get("live_probe") == "failed" for v in checks.values()):
        overall_status = "degraded"

    return JSONResponse(
        content={
            "status": overall_status,
            "probe_enabled": probe,
            "checks": checks,
        }
    )


@app.get("/api/health/bubblemaps")
async def bubblemaps_quota():
    """
    BubbleMaps API quota status endpoint.
    Returns daily quota usage and reset information.
    """
    from clients import bubblemaps_client
    return JSONResponse(content=bubblemaps_client.get_quota_status())


@app.get("/api/platforms")
async def api_platforms():
    """Get status of registered token feed platforms."""
    from clients import platform_router
    return JSONResponse(content=platform_router.get_platform_status())


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """
    Settings page for configuring API keys.
    Shows masked key values only - never full values.
    Requires admin (Google OAuth) or falls back to old token auth if OAuth not configured.
    """
    user = get_current_user(request)
    # If Google OAuth is configured, require admin user
    if os.environ.get("GOOGLE_CLIENT_ID"):
        if not user or not user.get('is_admin'):
            return RedirectResponse(url="/", status_code=302)
    else:
        # Fallback: old Bearer token auth for dev mode
        if not _verify_auth(request) and not (user and user.get('is_admin')):
            return RedirectResponse(url="/", status_code=302)

    masked_keys = get_masked_keys()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "masked_keys": masked_keys,
            "user": user
        }
    )


@app.post("/api/settings")
async def api_settings(request: Request):
    """
    API endpoint to save settings.
    Accepts JSON body with key-value pairs.
    Skips empty values and masked placeholders.
    Requires auth token if SCAMHOUND_ADMIN_TOKEN is set.
    """
    if not _verify_auth(request):
        return JSONResponse(
            content={
                "success": False,
                "error": "Unauthorized. Invalid or missing token."
            },
            status_code=401
        )

    try:
        data = await request.json()

        if not isinstance(data, dict):
            return JSONResponse(
                content={"success": False, "error": "Invalid request body"},
                status_code=400
            )

        # Save configuration
        success = save_config(data)

        if success:
            return JSONResponse(
                content={"success": True, "message": "Settings saved"}
            )
        else:
            return JSONResponse(
                content={"success": False, "error": "Failed to save settings"},
                status_code=500
            )

    except Exception as e:
        logger.error(f"[SETTINGS] Error saving settings: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


def _run_autoscan_cycle():
    """Wrapper to run monitor cycle in a background thread."""
    try:
        logger.info("[AUTOSCAN] Running scheduled scan cycle...")
        monitor.run_cycle()
    except Exception as e:
        logger.error(f"[AUTOSCAN] Error in scheduled cycle: {e}")


@app.get("/api/autoscan/status")
async def autoscan_status():
    """
    Get auto-scan status.
    Returns current enabled state and interval.
    """
    global _autoscan_enabled, _autoscan_interval
    return JSONResponse(content={
        "enabled": _autoscan_enabled,
        "interval": _autoscan_interval
    })


@app.post("/api/autoscan/toggle")
async def autoscan_toggle(request: Request):
    """
    Toggle auto-scan on/off.
    When enabled: starts APScheduler to run monitor.run_cycle() every 60s
    When disabled: shuts down the scheduler
    Returns new status.
    Requires admin authentication.
    """
    # Verify admin authentication
    if not _verify_auth(request):
        return JSONResponse(
            content={
                "success": False,
                "error": "Unauthorized. Admin access required."
            },
            status_code=401
        )

    global _autoscan_scheduler, _autoscan_enabled, _autoscan_interval

    if not _AUTO_SCAN_ALLOWED:
        return JSONResponse(
            content={
                "success": False,
                "error": "Auto-scanning is disabled (AUTO_SCAN_ENABLED != true). Set the environment variable to enable.",
                "enabled": False
            },
            status_code=403
        )

    with _autoscan_lock:
        if _autoscan_enabled:
            # Disable auto-scan
            if _autoscan_scheduler:
                try:
                    _autoscan_scheduler.shutdown(wait=False)
                    logger.info("[AUTOSCAN] Scheduler shut down")
                except Exception as e:
                    logger.error(
                        f"[AUTOSCAN] Error shutting down scheduler: {e}"
                    )
                finally:
                    _autoscan_scheduler = None
            _autoscan_enabled = False
            logger.info("[AUTOSCAN] Auto-scan disabled")
        else:
            # Enable auto-scan
            if _autoscan_scheduler is None:
                _autoscan_scheduler = BackgroundScheduler()
                _autoscan_scheduler.add_job(
                    _run_autoscan_cycle,
                    trigger=IntervalTrigger(seconds=_autoscan_interval),
                    id="scamhound_autoscan",
                    name="ScamHound Auto-Scan",
                    replace_existing=True
                )
                _autoscan_scheduler.start()
                _autoscan_enabled = True
                logger.info(
                    f"[AUTOSCAN] Scheduler started "
                    f"(interval: {_autoscan_interval}s)"
                )

                # Run initial cycle immediately in background thread
                initial_thread = threading.Thread(
                    target=_run_autoscan_cycle, daemon=True
                )
                initial_thread.start()
                logger.info(
                    "[AUTOSCAN] Initial scan cycle started in background"
                )
            else:
                logger.warning(
                    "[AUTOSCAN] Scheduler already exists, skipping start"
                )
                _autoscan_enabled = True

    return JSONResponse(content={
        "enabled": _autoscan_enabled,
        "interval": _autoscan_interval
    })


@app.get("/api/export/csv")
async def export_csv(request: Request):
    """
    Export all scored tokens as CSV.
    Returns a downloadable CSV file.
    Requires Pro tier or higher API key.
    """
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row or key_row.get("tier") not in ("pro", "builder", "enterprise"):
        return JSONResponse(
            content={"success": False, "error": "Export requires Pro tier or higher. Get an API key at /api/keys/generate"},
            status_code=403
        )

    try:
        # Get capped token set for export
        export_limit = 10000
        scores = database.get_recent_scores(limit=export_limit)
        total_scanned = database.get_stats().get("total_scanned", len(scores))
        is_truncated = total_scanned > export_limit
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write truncation warning row if the export is capped
        if is_truncated:
            writer.writerow([
                "NOTE",
                f"Export truncated to {export_limit} rows out of {total_scanned} total scans."
            ])
            writer.writerow([])

        # Write header
        writer.writerow([
            "Token Name", "Symbol", "Mint Address", "Score", "Risk Level",
            "Verdict Summary", "Creator", "Concentration", "Wallet Age (days)",
            "Scanned At"
        ])
        
        # Write data rows
        for token in scores:
            verdict = (token.get("ai_verdict") or "")[:100]
            if len(token.get("ai_verdict") or "") > 100:
                verdict += "..."
            
            writer.writerow([
                token.get("name") or "Unknown",
                token.get("symbol") or "???",
                token.get("token_mint") or "",
                token.get("risk_score") or 0,
                token.get("risk_level") or "UNKNOWN",
                verdict,
                token.get("creator_username") or "Unknown",
                f"{token.get('top_10_concentration', 0):.1f}%" if token.get('top_10_concentration') else "—",
                token.get("wallet_age_days") if token.get("wallet_age_days") is not None else "—",
                token.get("scored_at") or ""
            ])
        
        # Prepare response
        output.seek(0)
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"scamhound_report_{date_str}.csv"
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
        
    except Exception as e:
        logger.error(f"[EXPORT] Error generating CSV: {e}")
        return JSONResponse(
            content={"error": "Failed to generate CSV export"},
            status_code=500
        )


@app.get("/api/export/pdf")
async def export_pdf(request: Request):
    """
    Export all scored tokens as PDF.
    Returns a downloadable PDF report.
    Requires Pro tier or higher API key.
    """
    key_row, key_error = _check_api_key(request)
    if key_error:
        return key_error
    if not key_row or key_row.get("tier") not in ("pro", "builder", "enterprise"):
        return JSONResponse(
            content={"success": False, "error": "Export requires Pro tier or higher. Get an API key at /api/keys/generate"},
            status_code=403
        )

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        
        # Get capped token set for export
        export_limit = 10000
        scores = database.get_recent_scores(limit=export_limit)
        total_scanned = database.get_stats().get("total_scanned", len(scores))
        is_truncated = total_scanned > export_limit
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch
        )
        
        # Container for elements
        elements = []
        
        # Styles
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#0d1117'),
            spaceAfter=12
        )
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#6e7681'),
            spaceAfter=20
        )
        
        # Title
        elements.append(Paragraph("ScamHound Scan Report", title_style))
        
        # Date
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elements.append(Paragraph(f"Generated: {date_str}", subtitle_style))
        elements.append(Spacer(1, 0.2*inch))
        
        # Summary
        total = len(scores)
        high_risk = sum(1 for s in scores if s.get("risk_level") == "HIGH")
        critical = sum(1 for s in scores if s.get("risk_level") == "CRITICAL")
        
        summary_text = (
            f"Total Tokens Scanned: {total} | High Risk: {high_risk} | "
            f"Critical: {critical}"
        )
        elements.append(Paragraph(summary_text, styles['Normal']))
        if is_truncated:
            elements.append(
                Paragraph(
                    (
                        f"NOTE: Export truncated to {export_limit} rows out of "
                        f"{total_scanned} total scans."
                    ),
                    styles['Normal'],
                )
            )
        elements.append(Spacer(1, 0.3*inch))
        
        if not scores:
            elements.append(Paragraph("No tokens have been scanned yet.", styles['Normal']))
        else:
            # Table data
            table_data = [["Token", "Mint", "Score", "Risk", "Verdict"]]
            
            for token in scores:
                symbol = escape(str(token.get("symbol") or "???"))
                name = escape(str(token.get("name") or "Unknown"))
                token_display = f"{symbol} - {name}"
                
                mint = token.get("token_mint") or ""
                mint_short = f"{mint[:8]}...{mint[-8:]}" if len(mint) > 20 else mint
                
                score = str(token.get("risk_score") or 0)
                risk = token.get("risk_level") or "UNKNOWN"
                
                verdict = (token.get("ai_verdict") or "No verdict")[:60]
                if len(token.get("ai_verdict") or "") > 60:
                    verdict += "..."
                
                table_data.append([token_display, mint_short, score, risk, verdict])
            
            # Create table
            table = Table(table_data, colWidths=[1.5*inch, 1.8*inch, 0.6*inch, 0.8*inch, 2.3*inch])
            
            # Risk level colors
            def get_risk_color(risk_level):
                color_map = {
                    "CRITICAL": colors.HexColor('#f85149'),
                    "HIGH": colors.HexColor('#db6d28'),
                    "MODERATE": colors.HexColor('#d29922'),
                    "LOW": colors.HexColor('#3fb950'),
                }
                return color_map.get(risk_level, colors.HexColor('#6e7681'))
            
            # Table style
            style = TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#21262d')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f6f8fa')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#0d1117')),
                ('ALIGN', (2, 1), (3, -1), 'CENTER'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#d0d7de')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ])
            
            # Add alternating row colors
            for i in range(1, len(table_data)):
                if i % 2 == 0:
                    style.add('BACKGROUND', (0, i), (-1, i), colors.white)
                
                # Color code risk level
                risk_level = scores[i-1].get("risk_level") or "UNKNOWN"
                risk_color = get_risk_color(risk_level)
                style.add('TEXTCOLOR', (3, i), (3, i), risk_color)
                if risk_level in ["CRITICAL", "HIGH"]:
                    style.add('FONTNAME', (3, i), (3, i), 'Helvetica-Bold')
            
            table.setStyle(style)
            elements.append(table)
        
        # Build PDF
        doc.build(elements)
        
        # Prepare response
        buffer.seek(0)
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"scamhound_report_{date_str}.pdf"
        
        return StreamingResponse(
            buffer,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
        
    except Exception as e:
        logger.error(f"[EXPORT] Error generating PDF: {e}")
        return JSONResponse(
            content={"error": "Failed to generate PDF export"},
            status_code=500
        )


@app.post("/api/keys/generate")
async def generate_api_key(request: Request):
    """Generate a new free-tier API key for the authenticated user."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            content={"success": False, "error": "Authentication required"},
            status_code=401
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content={"success": False, "error": "Invalid JSON body"},
            status_code=400
        )

    if not isinstance(body, dict):
        return JSONResponse(
            content={"success": False, "error": "Invalid request body"},
            status_code=400
        )

    user_email = str(user.get("email", "")).strip().lower()
    requested_email = str(body.get("email", "")).strip().lower()
    name = body.get("name", "").strip()

    email = requested_email or user_email
    if not email or "@" not in email:
        return JSONResponse(
            content={"success": False, "error": "Valid email required"},
            status_code=400
        )

    # Non-admin users can only generate keys for their own account.
    if requested_email and requested_email != user_email and not user.get("is_admin"):
        return JSONResponse(
            content={
                "success": False,
                "error": "Cannot generate keys for another user"
            },
            status_code=403
        )

    # Limit: max 3 free keys per email
    existing = database.get_api_keys_by_email(email)
    active_keys = [k for k in existing if k.get("is_active")]
    if len(active_keys) >= 3:
        return JSONResponse(
            content={
                "success": False,
                "error": (
                    "Maximum 3 active keys per email. Revoke an existing key first."
                )
            },
            status_code=400
        )

    result = database.create_api_key(email=email, tier="free", name=name)
    daily_limit = TIER_LIMITS.get("free", 100)

    return JSONResponse(content={
        "success": True,
        "key": result["key"],
        "key_prefix": result["key_prefix"],
        "tier": "free",
        "daily_limit": daily_limit,
        "message": "Save this key \u2014 it cannot be retrieved again."
    })


@app.get("/api/keys/status")
async def api_key_status(request: Request):
    """Get status and usage for the provided API key."""
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return JSONResponse(content={"success": False, "error": "X-API-Key header required"}, status_code=401)

    key_row = database.validate_api_key(api_key)
    if not key_row:
        return JSONResponse(content={"success": False, "error": "Invalid or expired API key"}, status_code=401)

    tier = key_row.get("tier", "free")
    daily_limit = TIER_LIMITS.get(tier, 100)

    return JSONResponse(content={
        "success": True,
        "key_prefix": key_row["key_prefix"],
        "email": key_row["email"],
        "tier": tier,
        "name": key_row.get("name", ""),
        "calls_today": key_row.get("calls_today", 0),
        "calls_total": key_row.get("calls_total", 0),
        "daily_limit": daily_limit if daily_limit != -1 else "unlimited",
        "last_used_at": key_row.get("last_used_at"),
        "created_at": key_row.get("created_at"),
        "expires_at": key_row.get("expires_at"),
        "is_active": key_row.get("is_active")
    })


@app.get("/api/alerts/pending")
async def list_pending_alerts(request: Request, limit: int = 100):
    """List high-risk alerts that require admin tweet approval."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse(
            content={"success": False, "error": "Admin access required"},
            status_code=401
        )

    capped_limit = min(max(limit, 1), 500)
    pending = database.get_pending_tweet_approvals(limit=capped_limit)
    return JSONResponse(
        content={"success": True, "pending": pending, "count": len(pending)}
    )


@app.post("/api/alerts/{token_mint}/approve")
async def approve_alert_for_tweet(request: Request, token_mint: str):
    """Approve a token alert for Twitter posting."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return JSONResponse(
            content={"success": False, "error": "Admin access required"},
            status_code=401
        )

    token_mint = (token_mint or "").strip()
    if not token_mint:
        return JSONResponse(
            content={"success": False, "error": "token_mint required"},
            status_code=400
        )

    approved = database.approve_tweet(token_mint, approved_by=user.get("email", ""))
    if not approved:
        return JSONResponse(
            content={"success": False, "error": "Token not found"},
            status_code=404
        )

    return JSONResponse(
        content={
            "success": True,
            "message": f"Approved tweet for {token_mint}",
        }
    )


@app.delete("/api/keys/revoke")
async def revoke_key(request: Request):
    """Revoke an API key (admin only)."""
    if not _verify_auth(request):
        return JSONResponse(content={"success": False, "error": "Admin access required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "error": "Invalid JSON body"}, status_code=400)

    key_prefix = body.get("key_prefix", "").strip()
    if not key_prefix:
        return JSONResponse(content={"success": False, "error": "key_prefix required"}, status_code=400)

    key_data = database.get_api_key_by_prefix(key_prefix)
    if not key_data:
        return JSONResponse(content={"success": False, "error": "Key not found"}, status_code=404)

    database.revoke_api_key(key_data["id"])
    return JSONResponse(content={"success": True, "message": f"Key {key_prefix} revoked"})


@app.get("/api/keys/admin/list")
async def list_api_keys(request: Request):
    """List all API keys (admin only)."""
    if not _verify_auth(request):
        return JSONResponse(content={"success": False, "error": "Admin access required"}, status_code=401)

    keys = database.get_all_api_keys()
    return JSONResponse(content={"success": True, "keys": keys, "total": len(keys)})


@app.delete("/api/scores/clear")
async def clear_scores(request: Request):
    """Clear scan results. Admin clears all; regular user clears own."""
    user = get_current_user(request)
    if not user:
        # Fallback to old Bearer token auth (admin only)
        if not _verify_auth(request):
            return JSONResponse(
                content={"success": False, "error": "Unauthorized. Authentication required."},
                status_code=401
            )
        # Old-style admin auth — clear all
        result = database.clear_all_scores()
        logger.info(f"[SCAMHOUND] Cleared all scans (admin token): {result}")
        return JSONResponse(content={"status": "cleared", **result})

    if user['is_admin']:
        result = database.clear_all_scores()
        logger.info(f"[SCAMHOUND] Cleared all scans by admin {user['email']}: {result}")
    else:
        result = database.clear_user_scans(user['id'])
        logger.info(f"[SCAMHOUND] Cleared user scans for {user['email']}: {result}")
    return JSONResponse(content={"status": "cleared", **result})


@app.on_event("startup")
async def startup_event():
    """Initialize database and config on startup."""
    global _main_event_loop
    import asyncio

    _main_event_loop = asyncio.get_running_loop()
    config_source = load_config()
    logger.info(f"[SCAMHOUND] Config source: {config_source}")
    database.init_db()
    database.reset_daily_counters()
    init_oauth()

    # Schedule daily counter reset at midnight UTC
    from apscheduler.triggers.cron import CronTrigger
    _daily_reset_scheduler = BackgroundScheduler()
    _daily_reset_scheduler.add_job(
        database.reset_daily_counters,
        trigger=CronTrigger(hour=0, minute=0),
        id="api_key_daily_reset",
        name="API Key Daily Counter Reset",
        replace_existing=True
    )
    _daily_reset_scheduler.start()
    logger.info("[SCAMHOUND] Daily API key counter reset scheduled at UTC midnight")

    # Add re-score job - runs every 24 hours
    from engine.monitor import run_rescore_cycle
    _rescore_scheduler = BackgroundScheduler()
    _rescore_scheduler.add_job(
        run_rescore_cycle,
        trigger=IntervalTrigger(hours=24),
        id='rescore_cycle',
        name='ScamHound Re-Score Cycle',
        replace_existing=True
    )
    _rescore_scheduler.start()
    logger.info("[SCAMHOUND] Re-score scheduler started (interval: 24h)")
    
    # Register WebSocket broadcast callback with monitor
    # This allows monitor to broadcast new scores without circular imports
    try:
        from engine import monitor
        def broadcast_callback(score_data: dict):
            """Callback to broadcast score via WebSocket from any thread."""
            if _main_event_loop is None:
                logger.debug("[WEBSOCKET] Main event loop unavailable, skipping")
                return

            asyncio.run_coroutine_threadsafe(
                broadcast_new_score(score_data), _main_event_loop
            )
        
        monitor.set_new_score_callback(broadcast_callback)
        logger.info("[SCAMHOUND] WebSocket callback registered with monitor")
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not register monitor callback: {e}")
    
    if not _AUTO_SCAN_ALLOWED:
        logger.info("[MONITOR] Auto-scanning disabled (AUTO_SCAN_ENABLED != true)")

    logger.info("[SCAMHOUND] Dashboard started")


@app.websocket("/ws/scores")
async def websocket_scores(websocket: WebSocket):
    """
    WebSocket endpoint for real-time score updates.
    Broadcasts new token scores to all connected clients.
    """
    await websocket.accept()
    
    with _websocket_lock:
        _websocket_connections.add(websocket)
    
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info(f"[WEBSOCKET] Client connected: {client_host}")
    
    try:
        while True:
            # Keep connection alive, wait for any message (ping/pong)
            data = await websocket.receive_text()
            # Echo back to confirm connection is alive
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info(f"[WEBSOCKET] Client disconnected: {client_host}")
    except Exception as e:
        logger.warning(f"[WEBSOCKET] Connection error: {e}")
    finally:
        with _websocket_lock:
            _websocket_connections.discard(websocket)


async def broadcast_new_score(score_data: dict):
    """
    Broadcast a new score to all connected WebSocket clients.
    
    Args:
        score_data: Dictionary containing token score information
    """
    with _websocket_lock:
        sockets_snapshot = list(_websocket_connections)

    if not sockets_snapshot:
        return
    
    # Prepare the message
    message = {
        "type": "new_score",
        "data": score_data
    }
    
    # Send to all connected clients
    disconnected = set()
    
    for websocket in sockets_snapshot:
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.warning(f"[WEBSOCKET] Failed to send to client: {e}")
            disconnected.add(websocket)
    
    # Clean up disconnected clients
    if disconnected:
        with _websocket_lock:
            _websocket_connections.difference_update(disconnected)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))