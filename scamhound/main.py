"""
ScamHound Main Entry Point

Coordinates the monitoring scheduler and FastAPI dashboard server.
Run with: python main.py
"""

import os
import sys
import logging
import signal

from dotenv import load_dotenv
import uvicorn

# Configure logging at module level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info("[SCAMHOUND] Shutdown signal received, stopping...")
    sys.exit(0)


def main():
    """Main entry point for ScamHound."""
    # 1. Load .env file
    load_dotenv()
    logger.info("[SCAMHOUND] Environment loaded from .env")
    
    # 2. Load config (wrapped in try/except for compatibility)
    try:
        from config import load_config
        load_config()
        logger.info("[SCAMHOUND] Config loaded from config.json")
    except ImportError:
        logger.warning(
            "[SCAMHOUND] config.py not found, skipping config.json load"
        )
    except Exception as e:
        logger.error(f"[SCAMHOUND] Error loading config: {e}")
    
    # 3. Initialize database
    from engine import database
    database.init_db()
    
    # 4. Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 5. Start FastAPI/Uvicorn server on main thread
    from dashboard.app import app
    
    port = int(os.getenv("PORT", "8000"))
    host = "0.0.0.0"
    
    logger.info(f"[SCAMHOUND] Starting dashboard server on {host}:{port}")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
