"""
ScamHound Birdeye API Client
Market-side analysis - liquidity, trading patterns, price action
"""

import os
import time
import threading
import requests
from typing import Optional, Dict, List, Any, Tuple
import logging
from collections import Counter

from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.birdeye.so"

# Rate limiting: track last request time
_last_request_time = 0
_MIN_DELAY_SECONDS = 0.5  # Minimum delay between requests
_rate_limit_lock = threading.Lock()

# TTL response cache: {(endpoint, token_mint): (response_dict, timestamp)}
_response_cache: Dict[Tuple[str, str], Tuple[Any, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_cache_key(endpoint: str, params: Optional[Dict] = None) -> Tuple[str, str]:
    """Build a cache key from endpoint and the token address param."""
    address = (params or {}).get("address", "")
    return (endpoint, address)


def _check_cache(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Return cached response if still within TTL, else None."""
    key = _get_cache_key(endpoint, params)
    with _cache_lock:
        entry = _response_cache.get(key)
        if entry is not None:
            response_data, ts = entry
            if time.time() - ts < _CACHE_TTL_SECONDS:
                logger.debug(f"[BIRDEYE] Cache hit for {key}")
                return response_data
            else:
                del _response_cache[key]
    return None


def _store_cache(endpoint: str, params: Optional[Dict], response_data: Dict) -> None:
    """Store a response in the cache."""
    key = _get_cache_key(endpoint, params)
    with _cache_lock:
        _response_cache[key] = (response_data, time.time())


def _enforce_rate_limit() -> None:
    """Serialize request pacing to respect minimum delay globally."""
    global _last_request_time
    with _rate_limit_lock:
        elapsed = time.time() - _last_request_time
        if elapsed < _MIN_DELAY_SECONDS:
            time.sleep(_MIN_DELAY_SECONDS - elapsed)
        _last_request_time = time.time()


def _make_request(
    endpoint: str,
    params: Optional[Dict] = None,
    use_cache: bool = True,
) -> Optional[Dict]:
    """
    Make an authenticated request to the Birdeye API.
    
    Implements:
    - TTL response cache (5 minutes)
    - Rate limiting (0.5s delay between requests)
    - Retry logic with exponential backoff for 429 errors
    """
    # Check cache first unless caller explicitly requests fresh data.
    if use_cache:
        cached = _check_cache(endpoint, params)
        if cached is not None:
            return cached
    
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
    
    # Rate limiting: ensure minimum delay between requests across threads
    _enforce_rate_limit()

    try:
        response = request_with_retry(
            requests.get, url, headers=headers, params=params, timeout=30
        )
        response.raise_for_status()
        result = response.json()
        # Store in cache
        if result is not None:
            _store_cache(endpoint, params, result)
        return result
    except requests.exceptions.RequestException as e:
        logger.error(f"[BIRDEYE] API error on {endpoint}: {e}")
        return None


def get_token_overview(
    token_mint: str,
    use_cache: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Get token overview data.
    
    Returns: price, marketcap, liquidity, volume24h, priceChange24h
    """
    result = _make_request(
        "/defi/token_overview",
        params={"address": token_mint},
        use_cache=use_cache,
    )
    
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
        # Keep a normalized marketcap field but preserve additional variants.
        "marketcap": (
            data.get("marketCap", 0)
            or data.get("market_cap", 0)
            or data.get("mc", 0)
            or data.get("marketcap", 0)
            or data.get("fdv", 0)
        ),
        # Prefer explicit USD liquidity fields when available.
        "liquidity": (
            data.get("liquidityUsd", 0)
            or data.get("liquidity_usd", 0)
            or data.get("liquidity", 0)
        ),
        "volume_24h": data.get("volume24h", 0) or data.get("volume", 0),
        "price_change_24h": data.get("priceChange24h", 0) or data.get("price_change_24h", 0),
        "name": data.get("name"),
        "symbol": data.get("symbol"),
        # Preserve useful enrichment fields so monitor can use them directly.
        "holderCount": (
            data.get("holderCount")
            or data.get("holder_count")
            or data.get("holders")
            or data.get("holdersCount")
            or data.get("uniqueHolders")
        ),
        "creator_wallet": (
            data.get("creatorAddress")
            or data.get("creatorWallet")
            or data.get("creator")
            or data.get("creator_address")
            or data.get("creator_wallet")
            or data.get("deployerAddress")
            or data.get("owner")
            or data.get("deployer")
        ),
        "created_at": (
            data.get("createdAt")
            or data.get("created_at")
            or data.get("launchTime")
            or data.get("launch_time")
            or data.get("createdTime")
            or data.get("created_time")
        ),
    }


def get_liquidity_data(token_mint: str, overview_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Get liquidity pool data.
    
    Note: The /defi/liquidity endpoint returns 404 and is deprecated.
    We now use token_overview which includes liquidity data.
    
    Args:
        token_mint: The token mint address.
        overview_data: Optional pre-fetched overview data from get_token_overview().
                       If provided, skips the redundant API call.
    
    Returns:
    - liquidity_usd: float
    - liquidity_to_mcap_ratio: float (low ratio = danger)
    - pool_count: int (estimated)
    """
    if overview_data is not None:
        # Use pre-fetched data directly — no API call needed
        data = overview_data
    else:
        # Fallback: fetch from API (backward-compatible)
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
    
    # Extract liquidity and market cap from either:
    # 1) raw Birdeye payload keys (marketCap/mc/fdv), or
    # 2) normalized overview dict keys returned by get_token_overview (marketcap).
    raw_liquidity = (
        data.get("liquidityUsd", 0)
        or data.get("liquidity_usd", 0)
        or data.get("liquidity", 0)
    )
    raw_marketcap = (
        data.get("marketCap", 0)
        or data.get("marketcap", 0)
        or data.get("mc", 0)
        or data.get("fdv", 0)
    )

    # Birdeye can return numeric strings in some fields; coerce safely.
    try:
        liquidity_usd = float(raw_liquidity or 0)
    except (TypeError, ValueError):
        liquidity_usd = 0.0

    try:
        marketcap = float(raw_marketcap or 0)
    except (TypeError, ValueError):
        marketcap = 0.0
    
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
    - two_sided_trader_activity_ratio: float 0.0-1.0
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
    
    # Heuristic only: ratio of wallets with both buy and sell activity.
    # This is not definitive wash-trading detection.
    two_sided_traders = 0
    for trader in unique_traders:
        buys = trader_buy_count[trader]
        sells = trader_sell_count[trader]
        if buys > 0 and sells > 0:
            two_sided_traders += 1
    
    two_sided_ratio = (
        two_sided_traders / len(unique_traders) if unique_traders else 0.0
    )
    
    avg_trade_size = total_volume / len(trades) if trades else 0
    
    return {
        "two_sided_trader_activity_ratio": round(two_sided_ratio, 2),
        "two_sided_trader_ratio": round(two_sided_ratio, 2),
        # Backward-compatible alias retained for existing consumers.
        "wash_trading_score": round(two_sided_ratio, 2),
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


def get_full_market_data(token_mint: str, fresh: bool = False) -> Dict[str, Any]:
    """
    Get comprehensive market data for a token.
    
    Optimized: calls token_overview once and reuses data for liquidity extraction.
    """
    overview = get_token_overview(token_mint, use_cache=not fresh)
    # Pass overview data to avoid redundant API call to same endpoint
    liquidity = get_liquidity_data(token_mint, overview_data=overview)
    trades = get_trade_history(token_mint)
    
    return {
        "token_mint": token_mint,
        "overview": overview or {},
        "liquidity": liquidity or {},
        "trades": trades or {}
    }