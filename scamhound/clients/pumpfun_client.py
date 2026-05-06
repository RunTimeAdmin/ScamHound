"""pump.fun token launch client via PumpPortal API."""

import logging
import os
import requests
from typing import List, Dict, Any

from .retry import request_with_retry

logger = logging.getLogger(__name__)

PUMPPORTAL_BASE_URL = "https://pumpportal.fun/api/data"


def get_recent_launches(limit: int = 25) -> List[Dict[str, Any]]:
    """Get recent token launches from pump.fun via PumpPortal.
    
    Returns normalized token data matching the same shape as bags_client output:
    - token_mint: str (Solana mint address)
    - name: str
    - symbol: str  
    - creator_wallet: str
    - created_at: str (ISO timestamp)
    - platform: str ("pumpfun")
    
    Plus any platform-specific fields.
    """
    try:
        response = request_with_retry(
            requests.get,
            f"{PUMPPORTAL_BASE_URL}/tokens/latest",
            timeout=30,
        )
        
        if not response or response.status_code != 200:
            logger.warning("[PUMPFUN] Failed to fetch recent launches")
            return []
        
        data = response.json()
        tokens = []
        
        for item in data[:limit]:
            token = {
                "token_mint": item.get("mint", ""),
                "name": item.get("name", "Unknown"),
                "symbol": item.get("symbol", ""),
                "creator_wallet": item.get("creator", item.get("deployer", "")),
                "created_at": item.get("created_at", item.get("timestamp", "")),
                "platform": "pumpfun",
                # pump.fun specific fields
                "initial_buy_sol": item.get("initial_buy", 0),
                "market_cap": item.get("market_cap", 0),
                "reply_count": item.get("reply_count", 0),
            }
            if token["token_mint"]:
                tokens.append(token)
        
        logger.info(f"[PUMPFUN] Fetched {len(tokens)} recent launches")
        return tokens
        
    except Exception as e:
        logger.error(f"[PUMPFUN] Error fetching launches: {e}")
        return []


def is_configured() -> bool:
    """Check if pump.fun monitoring is enabled."""
    return os.environ.get("PUMPFUN_ENABLED", "false").lower() == "true"
