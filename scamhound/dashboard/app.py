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
from datetime import datetime
from typing import Dict, List, Optional
# Type imports removed - not needed

from fastapi import FastAPI, Request, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from engine import database
from engine import monitor
from config import get_masked_keys, save_config, load_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Rate limiting storage: {ip: [timestamp1, timestamp2, ...]}
_rate_limit_store: Dict[str, List[float]] = {}
_MAX_SCANS_PER_MINUTE = 5
_RATE_LIMIT_WINDOW = 60  # seconds

# Auto-scan scheduler state
_autoscan_scheduler: Optional[BackgroundScheduler] = None
_autoscan_enabled: bool = False
_autoscan_interval: int = 60  # seconds
_autoscan_lock = threading.Lock()


def _check_rate_limit(ip: str) -> tuple[bool, int, int]:
    """
    Check if IP has exceeded rate limit.
    Returns: (allowed, remaining, retry_after)
    """
    now = time.time()
    
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


def _verify_auth(token_query: Optional[str], auth_header: Optional[str]) -> bool:
    """
    Verify authentication token.
    Returns True if authorized, False otherwise.
    If SCAMHOUND_ADMIN_TOKEN is not set, allows access (dev mode).
    """
    expected_token = os.environ.get("SCAMHOUND_ADMIN_TOKEN", "")
    
    # Dev mode: no token configured, allow access
    if not expected_token:
        return True
    
    # Check query param
    if token_query and token_query == expected_token:
        return True
    
    # Check Bearer header
    if auth_header and auth_header.startswith("Bearer "):
        provided_token = auth_header[7:]  # Remove "Bearer " prefix
        if provided_token == expected_token:
            return True
    
    return False


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
    Rate limited: 5 scans per minute per IP.
    """
    # Rate limiting check
    client_ip = request.client.host if request.client else "unknown"
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

        # Run the scan using the async version for parallel API calls
        result = await monitor.scan_single_token_async(
            token_mint, skip_if_scored=False
        )
        
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
async def settings_page(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    Settings page for configuring API keys.
    Shows masked key values only - never full values.
    Requires auth token if SCAMHOUND_ADMIN_TOKEN is set.
    """
    if not _verify_auth(token, authorization):
        return JSONResponse(
            content={
                "success": False,
                "error": "Unauthorized. Invalid or missing token."
            },
            status_code=401
        )

    masked_keys = get_masked_keys()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "masked_keys": masked_keys
        }
    )


@app.post("/api/settings")
async def api_settings(
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """
    API endpoint to save settings.
    Accepts JSON body with key-value pairs.
    Skips empty values and masked placeholders.
    Requires auth token if SCAMHOUND_ADMIN_TOKEN is set.
    """
    if not _verify_auth(token, authorization):
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
async def autoscan_toggle():
    """
    Toggle auto-scan on/off.
    When enabled: starts APScheduler to run monitor.run_cycle() every 60s
    When disabled: shuts down the scheduler
    Returns new status.
    """
    global _autoscan_scheduler, _autoscan_enabled, _autoscan_interval

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
async def export_csv():
    """
    Export all scored tokens as CSV.
    Returns a downloadable CSV file.
    """
    try:
        # Get all scored tokens (no limit)
        scores = database.get_recent_scores(limit=10000)
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        
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
async def export_pdf():
    """
    Export all scored tokens as PDF.
    Returns a downloadable PDF report.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        
        # Get all scored tokens
        scores = database.get_recent_scores(limit=10000)
        
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
        
        summary_text = f"Total Tokens Scanned: {total} | High Risk: {high_risk} | Critical: {critical}"
        elements.append(Paragraph(summary_text, styles['Normal']))
        elements.append(Spacer(1, 0.3*inch))
        
        if not scores:
            elements.append(Paragraph("No tokens have been scanned yet.", styles['Normal']))
        else:
            # Table data
            table_data = [["Token", "Mint", "Score", "Risk", "Verdict"]]
            
            for token in scores:
                symbol = token.get("symbol") or "???"
                name = token.get("name") or "Unknown"
                token_display = f"{symbol}<br/><font size='8' color='#6e7681'>{name}</font>"
                
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


@app.on_event("startup")
async def startup_event():
    """Initialize database and config on startup."""
    load_config()
    database.init_db()
    logger.info("[SCAMHOUND] Dashboard started")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))