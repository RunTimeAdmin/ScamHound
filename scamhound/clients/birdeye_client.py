"""
ScamHound Birdeye API Client
Market-side analysis - liquidity, trading patterns, price action
"""

import os
import time
import requests
from typing import Optional, Dict, List, Any
import logging
from collections import Counter

from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so"

# Rate limiting: track last request time
_last_request_time = 0
_MIN_DELAY_SECONDS = 0.5  # Minimum delay between requests


def _make_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """
    Make an authenticated request to the Birdeye API.
    
    Implements:
    - Rate limiting (0.5s delay between requests)
    - Retry logic with exponential backoff for 429 errors
    """
    global _last_request_time
    
    url = f"{BASE_URL}{endpoint}"
    api_key = os.environ.get("BIRDEYE_API_KEY", "")
    if not api_key:
        logger.error("[BIRDEYE] API key not configured")
        return None
    
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
        "x-chain": "solana"
    }
    
    # Rate limiting: ensure minimum delay between requests
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_DELAY_SECONDS:
        time.sleep(_MIN_DELAY_SECONDS - elapsed)
    
    try:
        _last_request_time = time.time()
        response = request_with_retry(
            requests.get, url, headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"[BIRDEYE] API error on {endpoint}: {e}")
        return None


def get_token_overview(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get token overview data.
    
    Returns: price, marketcap, liquidity, volume24h, priceChange24h
    """
    result = _make_request("/defi/token_overview", params={"address": token_mint})
    
    if result is None:
        return None
    
    # Handle response format
    data = result
    if isinstance(result, dict):
        if "data" in result:
            data = result["data"]
        elif "response" in result:
            data = result["response"]
    
    if not isinstance(data, dict):
        return None
    
    return {
        "price": data.get("price", 0),
        "marketcap": data.get("mc", 0) or data.get("marketcap", 0),
        "liquidity": data.get("liquidity", 0),
        "volume_24h": data.get("volume24h", 0) or data.get("volume", 0),
        "price_change_24h": data.get("priceChange24h", 0) or data.get("price_change_24h", 0),
        "name": data.get("name"),
        "symbol": data.get("symbol")
    }


def get_liquidity_data(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Get liquidity pool data.
    
    Note: The /defi/liquidity endpoint returns 404 and is deprecated.
    We now use token_overview which includes liquidity data.
    
    Returns:
    - liquidity_usd: float
    - liquidity_to_mcap_ratio: float (low ratio = danger)
    - pool_count: int (estimated)
    """
    # Use token_overview instead of deprecated /defi/liquidity
    result = _make_request("/defi/token_overview", params={"address": token_mint})
    
    if result is None:
        return None
    
    data = result
    if isinstance(result, dict):
        if "data" in result:
            data = result["data"]
        elif "response" in result:
            data = result["response"]
    
    if not isinstance(data, dict):
        return None
    
    # Extract liquidity and marketcap from token_overview
    liquidity_usd = data.get("liquidity", 0)
    marketcap = data.get("marketCap", 0) or data.get("mc", 0) or data.get("fdv", 0)
    
    liquidity_to_mcap = 0.0
    if marketcap > 0 and liquidity_usd > 0:
        liquidity_to_mcap = liquidity_usd / marketcap
    
    # Pool count not available from token_overview, estimate as 1
    pool_count = 1
    
    return {
        "liquidity_usd": liquidity_usd,
        "liquidity_to_mcap_ratio": round(liquidity_to_mcap, 4),
        "pool_count": pool_count
    }


def get_trade_history(token_mint: str, limit: int = 50) -> Optional[Dict[str, Any]]:
    """
    Analyze trading patterns for manipulation signals.
    
    Note: Birdeye API limit must be 1-50 for /defi/txs/token endpoint.
    
    Returns:
    - wash_trading_score: float 0.0-1.0
    - large_sell_pressure: bool
    - avg_trade_size_usd: float
    - unique_trader_count: int
    """
    # Birdeye API requires limit to be 1-50
    limit = min(max(limit, 1), 50)
    
    result = _make_request("/defi/txs/token", params={
        "address": token_mint,
        "limit": limit,
        "offset": 0
    })
    
    if result is None:
        return None
    
    data = result
    if isinstance(result, dict):
        if "data" in result:
            data = result["data"]
        elif "response" in result:
            data = result["response"]
    
    if not isinstance(data, list):
        return None
    
    trades = data
    
    # Analyze trading patterns
    unique_traders = set()
    trader_buy_count = Counter()
    trader_sell_count = Counter()
    total_volume = 0
    large_sells = 0
    
    for trade in trades:
        trader = trade.get("owner") or trade.get("trader") or trade.get("wallet")
        side = trade.get("side", "").lower()
        amount_usd = trade.get("amountUsd", 0) or trade.get("amount_usd", 0)
        
        if trader:
            unique_traders.add(trader)
            if side == "buy":
                trader_buy_count[trader] += 1
            elif side == "sell":
                trader_sell_count[trader] += 1
                if amount_usd > 1000:  # Large sell
                    large_sells += 1
        
        total_volume += amount_usd
    
    # Detect wash trading (same wallets repeatedly buying and selling)
    wash_traders = 0
    for trader in unique_traders:
        buys = trader_buy_count[trader]
        sells = trader_sell_count[trader]
        # If a trader has both buys and sells, could be wash trading
        if buys > 0 and sells > 0:
            wash_traders += 1
    
    wash_trading_score = wash_traders / len(unique_traders) if unique_traders else 0.0
    
    avg_trade_size = total_volume / len(trades) if trades else 0
    
    return {
        "wash_trading_score": round(wash_trading_score, 2),
        "large_sell_pressure": large_sells > 3,
        "avg_trade_size_usd": round(avg_trade_size, 2),
        "unique_trader_count": len(unique_traders)
    }


def get_price_history(token_mint: str, time_from: int, time_to: int) -> Optional[List[Dict]]:
    """
    Get OHLCV price history.
    
    Used to detect pump-and-dump price patterns.
    """
    result = _make_request("/defi/history_price", params={
        "address": token_mint,
        "time_from": time_from,
        "time_to": time_to,
        "type": "15m"
    })
    
    if result is None:
        return None
    
    data = result
    if isinstance(result, dict):
        if "data" in result:
            data = result["data"]
        elif "response" in result:
            data = result["response"]
    
    if not isinstance(data, list):
        return None
    
    return data


def get_full_market_data(token_mint: str) -> Dict[str, Any]:
    """
    Get comprehensive market data for a token.
    """
    overview = get_token_overview(token_mint)
    liquidity = get_liquidity_data(token_mint)
    trades = get_trade_history(token_mint)
    
    return {
        "token_mint": token_mint,
        "overview": overview or {},
        "liquidity": liquidity or {},
        "trades": trades or {}
    }