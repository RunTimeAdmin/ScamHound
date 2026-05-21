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
import json
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape
from datetime import datetime, timezone
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
    init_oauth,
    get_current_user,
    get_jwt_secret,
)
from dashboard.routers.auth import create_auth_router
from dashboard.routers.health import create_health_router
from dashboard.routers.alerts import create_alerts_router
from dashboard.routers.keys import create_keys_router
from dashboard.routers.scores import create_scores_router
from dashboard.routers.score_detail import create_score_detail_router
from dashboard.routers.operational import create_operational_router
from dashboard.routers.watchlist import create_watchlist_router
from dashboard.routers.creator import create_creator_router
from dashboard.routers.scan import create_scan_router
from dashboard.routers.admin_ops import create_admin_ops_router

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
    "free": 10,
    "pro": 10000,
    "builder": 100000,
    "enterprise": -1,  # unlimited
}

# Auto-scan scheduler state
_autoscan_scheduler: Optional[BackgroundScheduler] = None
_daily_reset_scheduler: Optional[BackgroundScheduler] = None
_rescore_scheduler: Optional[BackgroundScheduler] = None
_autoscan_enabled: bool = False
_autoscan_interval: int = 60  # seconds
_autoscan_lock = threading.Lock()
_AUTO_SCAN_ALLOWED = os.environ.get("AUTO_SCAN_ENABLED", "false").lower() == "true"

# WebSocket active connections
_websocket_connections: set = set()
_websocket_lock = threading.Lock()
_main_event_loop = None
_background_scan_tasks: set = set()


def _structured_log(event: str, **fields) -> None:
    """Emit structured JSON logs for easier filtering and correlation."""
    payload = {"event": event, "component": "dashboard.app", **fields}
    logger.info(json.dumps(payload, separators=(",", ":"), default=str))


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
    Verify admin token auth via Authorization: Bearer header only.
    Returns True when token matches, False otherwise.
    """
    expected_token = os.environ.get("SCAMHOUND_ADMIN_TOKEN", "")

    # Fail closed if admin token is not configured.
    if not expected_token:
        logger.error(
            "[AUTH] SCAMHOUND_ADMIN_TOKEN missing; admin-token auth disabled"
        )
        return False

    # Check Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        provided_token = auth_header[7:]  # Remove "Bearer " prefix
        if provided_token == expected_token:
            return True
        logger.warning(
            "[AUTH] Invalid admin token from ip=%s",
            _get_client_ip(request),
        )
    else:
        logger.warning(
            "[AUTH] Missing Bearer token on admin endpoint ip=%s",
            _get_client_ip(request),
        )

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


def _is_admin_authenticated(request: Request) -> bool:
    """Allow either admin OAuth session or legacy admin token auth."""
    user = get_current_user(request)
    if user and user.get("is_admin"):
        return True
    return _verify_auth(request)


async def _startup_runtime() -> None:
    """Initialize runtime dependencies and background schedulers."""
    global _main_event_loop, _daily_reset_scheduler, _rescore_scheduler
    import asyncio

    _main_event_loop = asyncio.get_running_loop()
    config_source = load_config()
    logger.info(f"[SCAMHOUND] Config source: {config_source}")
    # Hard-fail startup if JWT secret is missing/weak.
    get_jwt_secret()
    database.init_db()
    database.reset_daily_counters()
    oauth_enabled = init_oauth()
    logger.info(f"[SCAMHOUND] OAuth enabled: {oauth_enabled}")

    # Schedule daily counter reset at midnight UTC
    from apscheduler.triggers.cron import CronTrigger

    _daily_reset_scheduler = BackgroundScheduler()
    _daily_reset_scheduler.add_job(
        database.reset_daily_counters,
        trigger=CronTrigger(hour=0, minute=0),
        id="api_key_daily_reset",
        name="API Key Daily Counter Reset",
        replace_existing=True,
    )
    _daily_reset_scheduler.start()
    logger.info("[SCAMHOUND] Daily API key counter reset scheduled at UTC midnight")

    # Add re-score job - runs every 24 hours
    from engine.monitor import run_rescore_cycle

    _rescore_scheduler = BackgroundScheduler()
    _rescore_scheduler.add_job(
        run_rescore_cycle,
        trigger=IntervalTrigger(hours=24),
        id="rescore_cycle",
        name="ScamHound Re-Score Cycle",
        replace_existing=True,
    )
    _rescore_scheduler.start()
    logger.info("[SCAMHOUND] Re-score scheduler started (interval: 24h)")

    # Register WebSocket broadcast callback with monitor
    # This allows monitor to broadcast new scores without circular imports
    try:
        from engine import monitor

        def broadcast_callback(score_data: dict):
            """Callback to broadcast score via WebSocket from any thread."""
            if _main_event_loop is None or _main_event_loop.is_closed():
                logger.debug("[WEBSOCKET] Main event loop unavailable, skipping")
                return

            future = asyncio.run_coroutine_threadsafe(
                broadcast_new_score(score_data), _main_event_loop
            )
            def _on_done(completed_future):
                try:
                    completed_future.result()
                except Exception as exc:
                    logger.warning(f"[WEBSOCKET] Broadcast scheduling failed: {exc}")

            future.add_done_callback(_on_done)

        monitor.set_new_score_callback(broadcast_callback)
        logger.info("[SCAMHOUND] WebSocket callback registered with monitor")
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not register monitor callback: {e}")

    if not _AUTO_SCAN_ALLOWED:
        logger.info("[MONITOR] Auto-scanning disabled (AUTO_SCAN_ENABLED != true)")

    logger.info("[SCAMHOUND] Dashboard started")


async def _shutdown_runtime() -> None:
    """Shutdown background schedulers cleanly."""
    global _autoscan_scheduler, _daily_reset_scheduler, _rescore_scheduler, _main_event_loop

    for scheduler_name in (
        "_autoscan_scheduler",
        "_daily_reset_scheduler",
        "_rescore_scheduler",
    ):
        scheduler = globals().get(scheduler_name)
        if scheduler is None:
            continue
        try:
            scheduler.shutdown(wait=False)
            logger.info(f"[SCAMHOUND] Stopped {scheduler_name}")
        except Exception as e:
            logger.warning(f"[SCAMHOUND] Failed stopping {scheduler_name}: {e}")
        globals()[scheduler_name] = None
    _main_event_loop = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan hook for startup/shutdown lifecycle."""
    await _startup_runtime()
    try:
        yield
    finally:
        await _shutdown_runtime()


# Create FastAPI app
app = FastAPI(
    title="ScamHound",
    description="On-demand rug pull detection for Solana",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """Attach a per-request ID for log and client correlation."""
    started_at = time.perf_counter()
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    client_ip = _get_client_ip(request)
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        _structured_log(
            "http_request_error",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    _structured_log(
        "http_request_complete",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        client_ip=client_ip,
        duration_ms=duration_ms,
    )
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

app.include_router(create_auth_router(templates))
app.include_router(create_health_router())
app.include_router(create_alerts_router(lambda request: get_current_user(request)))
app.include_router(
    create_keys_router(
        lambda request: get_current_user(request),
        lambda request: _is_admin_authenticated(request),
        TIER_LIMITS,
        lambda request: _get_client_ip(request),
    )
)
app.include_router(
    create_scores_router(
        lambda request: get_current_user(request),
        lambda request: _verify_auth(request),
        lambda request: _check_api_key(request),
        lambda response, key_row: _add_rate_limit_headers(response, key_row),
        logger,
    )
)
app.include_router(
    create_score_detail_router(
        lambda request: _check_api_key(request),
        lambda response, key_row: _add_rate_limit_headers(response, key_row),
    )
)
app.include_router(create_operational_router())


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
    if not _is_admin_authenticated(request):
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


@app.get("/api/_legacy/leaderboard")
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


@app.get("/api/_legacy/rescore/status")
async def api_rescore_status(request: Request):
    """Get info about the re-scoring system."""
    eligible = database.get_tokens_for_rescore(max_age_days=7, min_score=40, limit=100)
    return JSONResponse(content={
        "eligible_for_rescore": len(eligible),
        "rescore_interval_hours": 24,
        "min_score_threshold": 40,
        "max_age_days": 7
    })


@app.get("/api/_legacy/stats")
async def api_stats():
    """
    API endpoint for statistics.
    """
    stats = database.get_stats()
    return JSONResponse(content=stats)


@app.get("/api/_legacy/creator/{wallet_address}")
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


@app.get("/api/_legacy/platforms")
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


@app.post("/api/_legacy/settings")
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


@app.get("/api/_legacy/autoscan/status")
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


@app.post("/api/_legacy/autoscan/toggle")
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
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    next_run_time=datetime.now(timezone.utc),
                )
                _autoscan_scheduler.start()
                _autoscan_enabled = True
                logger.info(
                    f"[AUTOSCAN] Scheduler started "
                    f"(interval: {_autoscan_interval}s)"
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


@app.get("/api/_legacy/export/csv")
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


@app.get("/api/_legacy/export/pdf")
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
                verdict = escape(str(verdict))
                
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


@app.post("/api/_legacy/keys/generate")
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


@app.get("/api/_legacy/keys/status")
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


@app.delete("/api/_legacy/keys/revoke")
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


@app.get("/api/_legacy/keys/admin/list")
async def list_api_keys(request: Request):
    """List all API keys (admin only)."""
    if not _verify_auth(request):
        return JSONResponse(content={"success": False, "error": "Admin access required"}, status_code=401)

    keys = database.get_all_api_keys()
    return JSONResponse(content={"success": True, "keys": keys, "total": len(keys)})


app.include_router(
    create_creator_router(
        api_leaderboard_fn=api_leaderboard,
        api_creator_reputation_fn=api_creator_reputation,
    )
)
app.include_router(
    create_watchlist_router(
        is_admin_authenticated_fn=lambda request: _is_admin_authenticated(request),
        check_api_key_fn=lambda request: _check_api_key(request),
        add_rate_limit_headers_fn=(
            lambda response, key_row: _add_rate_limit_headers(response, key_row)
        ),
        is_valid_solana_address_fn=lambda value: _is_valid_solana_address(value),
        logger=logger,
    )
)
app.include_router(
    create_scan_router(
        check_api_key_fn=lambda request: _check_api_key(request),
        get_current_user_fn=lambda request: get_current_user(request),
        get_client_ip_fn=lambda request: _get_client_ip(request),
        check_rate_limit_fn=lambda ip: _check_rate_limit(ip),
        is_valid_solana_address_fn=lambda value: _is_valid_solana_address(value),
        add_rate_limit_headers_fn=(
            lambda response, key_row: _add_rate_limit_headers(response, key_row)
        ),
        max_scans_per_minute=_MAX_SCANS_PER_MINUTE,
        logger=logger,
        background_scan_tasks=_background_scan_tasks,
    )
)
app.include_router(
    create_admin_ops_router(
        api_save_settings_fn=api_settings,
        api_autoscan_status_fn=autoscan_status,
        api_toggle_autoscan_fn=autoscan_toggle,
        export_csv_fn=export_csv,
        export_pdf_fn=export_pdf,
    )
)


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
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )