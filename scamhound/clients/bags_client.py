"""
ScamHound Bags.fm API Client
All interactions with the Bags.fm REST API
"""

import os
import requests
from typing import Optional, Dict, List, Any
import logging

from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://public-api-v2.bags.fm/api/v1"


def _make_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Make an authenticated request to the Bags API."""
    url = f"{BASE_URL}{endpoint}"
    api_key = os.environ.get("BAGS_API_KEY", "")
    if not api_key:
        logger.error("[BAGS] API key not configured")
        return None
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    
    try:
        response = request_with_retry(
            requests.get, url, headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"[BAGS] API error on {endpoint}: {e}")
        return None


def get_recent_launches(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Get recently launched tokens from Bags.fm.
    
    Returns list of tokens with: tokenMint, name, symbol, createdAt, description
    """
    # Note: Using the actual Bags API endpoint structure
    # Feed endpoint doesn't support limit parameter
    result = _make_request("/token-launch/feed")
    
    if result is None:
        return []
    
    # Bags API returns {"success": true, "response": [...]} format
    if isinstance(result, dict) and "response" in result:
        return result["response"]
    
    return []


def get_token_creators(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get creator information for a token.
    
    Returns:
    - wallet: Solana wallet address
    - username: Bags/Twitter username
    - royalty_pct: royalty percentage (royaltyBps / 100)
    
    Response format: {"success": true, "response": [{"username": "...", "royaltyBps": ..., "isCreator": true, "wallet": "..."}]}
    """
    result = _make_request("/token-launch/creator/v3", params={"tokenMint": token_mint})
    
    if result is None:
        return None
    
    # Bags API returns {"success": true, "response": [...]} format
    creators = None
    if isinstance(result, dict) and "response" in result:
        creators = result["response"]
    
    if not creators or not isinstance(creators, list) or len(creators) == 0:
        return None
    
    # Get the primary creator (usually first in list with isCreator=True)
    primary = None
    for c in creators:
        if c.get("isCreator", False):
            primary = c
            break
    
    if primary is None:
        primary = creators[0]
    
    return {
        "wallet": primary.get("wallet", ""),
        "username": primary.get("providerUsername") or primary.get("username", ""),
        "royalty_pct": primary.get("royaltyBps", 0) / 100
    }


def get_top_holders(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get top holders information for a token.
    
    NOTE: The Bags API does NOT provide holder/distribution data.
    This function returns None. Holder data must be sourced from an alternative
    source such as Helius or Birdeye API.
    
    Returns None - use helius_client or birdeye_client for holder data.
    """
    logger.info(f"[BAGS] Holder data not available from Bags API for {token_mint}. Use Helius/Birdeye instead.")
    return None


def get_lifetime_fees(token_mint: str) -> Optional[float]:
    """
    Get total lifetime fees collected for a token in SOL.
    
    Note: The API returns the fee as a string in lamports, not as a dict.
    We need to convert it to SOL (1 SOL = 1e9 lamports).
    """
    result = _make_request("/token-launch/lifetime-fees", params={"tokenMint": token_mint})
    
    if result is None:
        return 0.0
    
    if isinstance(result, dict):
        if "response" in result:
            response = result["response"]
            # Response can be a string (lamports) or a dict
            if isinstance(response, str):
                try:
                    lamports = int(response)
                    return lamports / 1e9  # Convert lamports to SOL
                except (ValueError, TypeError):
                    return 0.0
            elif isinstance(response, dict):
                return response.get("totalFeesSol", 0.0)
        elif "totalFeesSol" in result:
            return result.get("totalFeesSol", 0.0)
    
    return 0.0


def get_token_claim_stats(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get claim statistics for a token.
    
    Response format: {"success": true, "response": {...}} or {"success": true, "response": []}
    """
    result = _make_request("/token-launch/claim-stats", params={"tokenMint": token_mint})
    
    if result is None:
        return {}
    
    # Bags API returns {"success": true, "response": {...}} format
    # Response can be a dict or a list (empty if no claims)
    if isinstance(result, dict) and "response" in result:
        response = result["response"]
        if isinstance(response, dict):
            return response
        elif isinstance(response, list):
            # Empty list means no claims, return empty dict
            return {}
    
    return {}


def get_full_token_profile(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get comprehensive token profile by aggregating all Bags API data.
    
    This is the primary payload passed to the scorer.
    """
    # Get all data
    creators = get_token_creators(token_mint)
    holders = get_top_holders(token_mint)
    fees = get_lifetime_fees(token_mint)
    claims = get_token_claim_stats(token_mint)
    
    # We need basic token info - try to get it from recent launches or another endpoint
    # For now, we'll use placeholder values that will be filled in by monitor
    return {
        "token_mint": token_mint,
        "name": None,  # Will be filled by monitor
        "symbol": None,  # Will be filled by monitor
        "created_at": None,  # Will be filled by monitor
        "creator": creators or {
            "wallet": "",
            "username": "",
            "royalty_pct": 0.0
        },
        "holders": holders or {
            "top_holders": [],
            "top_10_concentration_pct": 0.0,
            "total_holder_count": 0
        },
        "lifetime_fees_sol": fees or 0.0,
        "claim_stats": claims or {},
        "source": "bags_api"
    }