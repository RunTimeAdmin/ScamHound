"""
ScamHound Dashboard Backend
FastAPI web server serving the live dashboard and widget
"""

import os
import logging
# Type imports removed - not needed

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import database
from engine import monitor
from config import get_masked_keys, save_config, load_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="ScamHound",
    description="Real-time rug pull detection for Bags.fm",
    version="1.0.0"
)

# Get the directory paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "dashboard", "templates")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Main dashboard page.
    Shows last 50 scored tokens with auto-refresh.
    """
    scores = database.get_recent_scores(limit=50)
    stats = database.get_stats()
    
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "scores": scores,
            "stats": stats
        }
    )


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
                "error": "Token not found"
            },
            status_code=404
        )
    
    return templates.TemplateResponse(
        "token_detail.html",
        {
            "request": request,
            "token": token
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
async def api_scores(limit: int = 50):
    """
    API endpoint for scores.
    Returns JSON array of last N scores.
    """
    scores = database.get_recent_scores(limit=limit)
    return JSONResponse(content=scores)


@app.get("/api/score/{token_mint}")
async def api_score(token_mint: str):
    """
    API endpoint for a single token score.
    """
    token = database.get_token_score(token_mint)
    
    if not token:
        return JSONResponse(
            content={"error": "Token not found"},
            status_code=404
        )
    
    return JSONResponse(content=token)


@app.get("/api/stats")
async def api_stats():
    """
    API endpoint for statistics.
    """
    stats = database.get_stats()
    return JSONResponse(content=stats)


@app.post("/api/scan")
async def scan_token(request: Request):
    """
    Manually trigger a scan for a specific token mint address.
    Accepts JSON: {"mint": "TOKEN_MINT_ADDRESS"}
    Returns the score result.
    """
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
        
        # Validate mint address format (basic check)
        if len(token_mint) < 32 or len(token_mint) > 44:
            return JSONResponse(
                content={"success": False, "error": "Invalid mint address format"},
                status_code=400
            )
        
        logger.info(f"[DASHBOARD] Manual scan requested for: {token_mint[:8]}...")
        
        # Run the scan
        result = monitor.scan_single_token(token_mint, skip_if_scored=False)
        
        if result is None:
            return JSONResponse(
                content={"success": False, "error": "Scan failed or token not found"},
                status_code=500
            )
        
        return JSONResponse(content={"success": True, "result": result})
        
    except Exception as e:
        logger.error(f"[DASHBOARD] Error in scan_token: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


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


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """
    Settings page for configuring API keys.
    Shows masked key values only - never full values.
    """
    masked_keys = get_masked_keys()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "masked_keys": masked_keys
        }
    )


@app.post("/api/settings")
async def api_settings(request: Request):
    """
    API endpoint to save settings.
    Accepts JSON body with key-value pairs.
    Skips empty values and masked placeholders.
    """
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


@app.on_event("startup")
async def startup_event():
    """Initialize database and config on startup."""
    load_config()
    database.init_db()
    logger.info("[SCAMHOUND] Dashboard started")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))