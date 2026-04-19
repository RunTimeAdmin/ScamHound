"""
ScamHound Monitor Module
Main polling loop that coordinates all analysis
"""

import os
import logging
import asyncio
from collections import OrderedDict
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from clients import bags_client
from clients import helius_client
from clients import birdeye_client
from clients import bubblemaps_client
from engine import database
from engine import scorer
from alerts import twitter_bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
RISK_THRESHOLD = int(os.getenv("RISK_ALERT_THRESHOLD", "65"))
MIN_TOKEN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", "0"))  # Skip tokens younger than this

# Capped LRU-style processed tokens tracker (max 500 entries)
_processed_tokens = OrderedDict()
_PROCESSED_MAX = 500


def _mark_processed(token_mint):
    """Mark a token as processed, evicting oldest if at capacity."""
    if token_mint in _processed_tokens:
        _processed_tokens.move_to_end(token_mint)
    else:
        _processed_tokens[token_mint] = True
        if len(_processed_tokens) > _PROCESSED_MAX:
            _processed_tokens.popitem(last=False)


def _is_processed(token_mint):
    """Check if a token has been processed."""
    return token_mint in _processed_tokens


# Callback for broadcasting new scores via WebSocket
_new_score_callback: Optional[Callable[[dict], None]] = None


def set_new_score_callback(callback: Callable[[dict], None]):
    """
    Set a callback function to be called when a new score is saved.
    Used by dashboard to broadcast via WebSocket.
    
    Args:
        callback: Function that accepts a score_data dict
    """
    global _new_score_callback
    _new_score_callback = callback
    logger.info("[MONITOR] New score callback registered")


def _notify_new_score(score_data: dict):
    """
    Notify the registered callback of a new score.
    Thread-safe wrapper for the callback.
    """
    if _new_score_callback:
        try:
            logger.debug(
                f"[MONITOR] Broadcasting new score for "
                f"{score_data.get('token_name', 'unknown')}"
            )
            _new_score_callback(score_data)
        except Exception as e:
            logger.warning(
                f"[MONITOR] Failed to notify new score callback: {e}"
            )


# Global scheduler instance
_scheduler = None


def _calculate_token_age_minutes(created_at: Any) -> Optional[int]:
    """
    Calculate token age in minutes from creation timestamp.
    
    Args:
        created_at: ISO format timestamp string or datetime
        
    Returns:
        Age in minutes, or None if cannot calculate
    """
    if not created_at:
        return None
    
    try:
        # Parse the timestamp
        if isinstance(created_at, str):
            # Handle various ISO formats
            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        elif isinstance(created_at, datetime):
            created_dt = created_at
        else:
            return None
        
        # Ensure timezone-aware
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        
        # Calculate age
        now = datetime.now(timezone.utc)
        age_seconds = (now - created_dt).total_seconds()
        age_minutes = int(age_seconds / 60)
        
        return max(0, age_minutes)  # Ensure non-negative
    except Exception:
        return None


def _get_token_status(token_data: Dict[str, Any]) -> str:
    """
    Determine token status based on available data.
    
    Returns one of: 'bonding', 'graduated', 'active', 'unknown'
    """
    # Check if status is explicitly provided
    status = token_data.get("status", "")
    if status:
        return status.lower()
    
    # Check claim stats from Bags
    claim_stats = token_data.get("claim_stats", {})
    if claim_stats:
        # If there are claim stats, token has likely graduated
        if claim_stats.get("totalClaimed") or claim_stats.get("claimedCount"):
            return "graduated"
    
    # Check liquidity as a proxy for status
    liquidity = token_data.get("liquidity_usd", 0)
    if liquidity and liquidity > 0:
        return "active"
    
    return "unknown"


async def _async_get_bags_profile(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting Bags profile."""
    try:
        return await asyncio.to_thread(
            bags_client.get_full_token_profile, token_mint
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get Bags profile for {token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_holder_data(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting Helius holder data."""
    try:
        holder_data = await asyncio.to_thread(
            helius_client.get_token_holders, token_mint
        )
        if holder_data:
            return {
                "top_holders": holder_data.get("top_holders", []),
                "top_10_concentration_pct": holder_data.get("top10_pct", 0),
                "total_holder_count": holder_data.get("total_holders", 0),
                "concentration_score": holder_data.get(
                    "concentration_score", "unknown"
                ),
                "top1_pct": holder_data.get("top1_pct", 0),
                "top5_pct": holder_data.get("top5_pct", 0)
            }
        return None
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get holder data for {token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_bubblemaps_data(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting BubbleMaps cluster analysis."""
    try:
        bubblemaps_data = await asyncio.to_thread(
            bubblemaps_client.get_cluster_analysis, token_mint, chain="solana"
        )
        if bubblemaps_data:
            logger.info(
                f"[SCAMHOUND] BubbleMaps analysis for {token_mint[:8]}...: "
                f"decentralization={bubblemaps_data.get('decentralization_score')}, "
                f"clusters={bubblemaps_data.get('cluster_count')}"
            )
            return {
                "decentralization_score": bubblemaps_data.get(
                    "decentralization_score", 0
                ),
                "cluster_count": bubblemaps_data.get("cluster_count", 0),
                "largest_cluster_share": bubblemaps_data.get(
                    "largest_cluster_share", 0
                ),
                "risk_signal": bubblemaps_data.get("risk_signal", "UNKNOWN")
            }
        logger.warning(
            f"[SCAMHOUND] No BubbleMaps data for {token_mint[:8]}..."
        )
        return None
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get BubbleMaps data for {token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_market_data(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting Birdeye market data."""
    try:
        market_data = await asyncio.to_thread(birdeye_client.get_full_market_data, token_mint)
        if market_data:
            return {
                "overview": market_data.get("overview", {}),
                "liquidity": market_data.get("liquidity", {}),
                "trades": market_data.get("trades", {})
            }
        return None
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not get market data for {token_mint[:8]}...: {e}")
        return None


async def _async_analyze_creator(creator_wallet: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for analyzing creator wallet via Helius."""
    try:
        creator_analysis = await asyncio.to_thread(
            helius_client.analyze_creator_wallet, creator_wallet
        )
        return {
            "wallet_age_days": creator_analysis.get("wallet_age_days", -1),
            "prior_launch_count": creator_analysis.get("prior_launch_count", 0),
            "abandoned_tokens": creator_analysis.get("abandoned_tokens", []),
            "days_since_last_launch": creator_analysis.get("days_since_last_launch")
        }
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not analyze creator wallet: {e}")
        return None


async def _async_check_clustering(holder_wallets: list) -> Optional[Dict[str, Any]]:
    """Async wrapper for checking wallet clustering via Helius."""
    try:
        clustering = await asyncio.to_thread(
            helius_client.check_wallet_clustering, holder_wallets
        )
        return {
            "clustering_score": clustering.get("clustering_score", 0),
            "clustered_wallets": clustering.get("clustered_wallets", 0)
        }
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not check clustering: {e}")
        return None


async def scan_single_token_async(token_mint: str, skip_if_scored: bool = True) -> Optional[Dict[str, Any]]:
    """
    Scan and analyze a single token by mint address (async version with parallel API calls).
    
    This function performs the full analysis pipeline on a single token:
    - Get Bags profile (if available)
    - Analyze creator wallet via Helius
    - Check holder clustering via Helius
    - Get market data via Birdeye
    - Get BubbleMaps cluster analysis
    - Score with Claude
    - Save to database
    
    Args:
        token_mint: The token mint address to scan
        skip_if_scored: If True, skip if token already in database
        
    Returns:
        The score result dict, or None if skipped or error
    """
    logger.info(f"[SCAMHOUND] Scanning single token: {token_mint}")
    
    # Check if already scored
    if skip_if_scored and database.token_already_scored(token_mint):
        logger.info(f"[SCAMHOUND] Token {token_mint[:8]}... already scored, skipping")
        return database.get_token_score(token_mint)
    
    # Check if scored recently (within last hour) to prevent duplicates
    if database.was_recently_scored(token_mint, hours=1):
        logger.info(f"[SCAMHOUND] Token {token_mint[:8]}... scored within last hour, skipping")
        return database.get_token_score(token_mint)
    
    try:
        # Build token profile
        token_data = {
            "token_mint": token_mint,
            "name": "Unknown",
            "symbol": "UNKNOWN",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Get Bags profile (may fail if token not from Bags)
        bags_profile = await _async_get_bags_profile(token_mint)
        if bags_profile:
            token_data.update(bags_profile)
            # Try to get better name/symbol from Bags
            if bags_profile.get("name"):
                token_data["name"] = bags_profile["name"]
            if bags_profile.get("symbol"):
                token_data["symbol"] = bags_profile["symbol"]
            # Try to get created_at from Bags if not already set
            if bags_profile.get("created_at"):
                token_data["created_at"] = bags_profile["created_at"]
        
        # Calculate token age
        token_data["token_age_minutes"] = _calculate_token_age_minutes(
            token_data.get("created_at")
        )
        token_data["token_status"] = _get_token_status(token_data)
        
        # Check minimum age filter
        age_minutes = token_data.get("token_age_minutes")
        if age_minutes is not None and MIN_TOKEN_AGE_MINUTES > 0:
            if age_minutes < MIN_TOKEN_AGE_MINUTES:
                logger.info(
                    f"[SCAMHOUND] Token {token_mint[:8]}... skipped: "
                    f"age {age_minutes}m < minimum {MIN_TOKEN_AGE_MINUTES}m"
                )
                return None
        
        # Run Helius, BubbleMaps, and Birdeye API calls in parallel
        holder_task = _async_get_holder_data(token_mint)
        bubblemaps_task = _async_get_bubblemaps_data(token_mint)
        market_task = _async_get_market_data(token_mint)
        
        holder_data, bubblemaps_data, market_data = await asyncio.gather(
            holder_task, bubblemaps_task, market_task,
            return_exceptions=True
        )
        
        # Process holder data
        if holder_data and not isinstance(holder_data, Exception):
            token_data["holders"] = holder_data
        
        # Process BubbleMaps data
        if bubblemaps_data and not isinstance(bubblemaps_data, Exception):
            token_data["bubblemaps"] = bubblemaps_data
        
        # Process market data
        if market_data and not isinstance(market_data, Exception):
            overview = market_data.get("overview", {})
            liquidity = market_data.get("liquidity", {})
            trades = market_data.get("trades", {})
            
            token_data["liquidity_usd"] = liquidity.get("liquidity_usd", 0)
            token_data["liquidity_to_mcap_ratio"] = liquidity.get("liquidity_to_mcap_ratio", 0)
            token_data["unique_trader_count"] = trades.get("unique_trader_count", 0)
            token_data["wash_trading_score"] = trades.get("wash_trading_score", 0)
            token_data["large_sell_pressure"] = trades.get("large_sell_pressure", False)
            
            # Try to get token name/symbol from Birdeye overview
            if overview:
                if not token_data.get("name") or token_data["name"] == "Unknown":
                    birdeye_name = overview.get("name")
                    if birdeye_name:
                        token_data["name"] = birdeye_name
                if not token_data.get("symbol") or token_data["symbol"] == "UNKNOWN":
                    birdeye_symbol = overview.get("symbol")
                    if birdeye_symbol:
                        token_data["symbol"] = birdeye_symbol
        
        # Get creator wallet
        creator_wallet = token_data.get("creator", {}).get("wallet")
        
        # Check if creator wallet is on watchlist
        if creator_wallet and database.is_watched_wallet(creator_wallet):
            database.update_watchlist_seen(creator_wallet)
            logger.info(
                f"[SCAMHOUND] Watched wallet detected: {creator_wallet[:8]}..."
            )
        
        if creator_wallet:
            # Run creator analysis and clustering check in parallel
            creator_task = _async_analyze_creator(creator_wallet)
            
            # Prepare clustering task if we have holder wallets
            holder_wallets = [
                h.get("address") for h in token_data.get("holders", {}).get("top_holders", [])
                if h.get("address")
            ]
            
            if holder_wallets:
                clustering_task = _async_check_clustering(holder_wallets)
                creator_result, clustering_result = await asyncio.gather(
                    creator_task, clustering_task,
                    return_exceptions=True
                )
            else:
                creator_result = await creator_task
                clustering_result = None
            
            # Process creator analysis
            if creator_result and not isinstance(creator_result, Exception):
                token_data.update(creator_result)
            
            # Process clustering result
            if clustering_result and not isinstance(clustering_result, Exception):
                token_data["clustering_score"] = clustering_result.get("clustering_score", 0)
                token_data["clustered_wallets"] = clustering_result.get("clustered_wallets", 0)
        
        # Calculate risk score
        score_result = scorer.calculate_risk_score(token_data)
        
        # Save to database
        database.save_score(score_result)
        
        # Mark as processed
        _mark_processed(token_mint)
        
        # Notify WebSocket clients
        _notify_new_score(score_result)
        
        # Log result
        logger.info(
            f"[SCAMHOUND] {score_result.get('symbol', '???')} | "
            f"Score: {score_result.get('risk_score', 0)} | "
            f"{score_result.get('risk_level', 'UNKNOWN')}"
        )
        
        return score_result
        
    except Exception as e:
        logger.error(f"[SCAMHOUND] Error scanning token {token_mint}: {e}")
        return None


def scan_single_token(token_mint: str, skip_if_scored: bool = True) -> Optional[Dict[str, Any]]:
    """
    Scan and analyze a single token by mint address (sync wrapper for async implementation).
    
    This is a convenience wrapper that runs the async scan_single_token_async()
    using asyncio.run(). For use in sync contexts like the scheduler.
    
    Args:
        token_mint: The token mint address to scan
        skip_if_scored: If True, skip if token already in database
        
    Returns:
        The score result dict, or None if skipped or error
    """
    try:
        return asyncio.run(scan_single_token_async(token_mint, skip_if_scored))
    except RuntimeError:
        # If already in an async context (e.g., FastAPI), use the async version directly
        # This shouldn't happen in normal usage but provides a fallback
        logger.warning("[SCAMHOUND] scan_single_token called from async context, using sync fallback")
        # Fallback to sequential execution for edge cases
        return _scan_single_token_sync_fallback(token_mint, skip_if_scored)


def _scan_single_token_sync_fallback(token_mint: str, skip_if_scored: bool = True) -> Optional[Dict[str, Any]]:
    """
    Fallback synchronous implementation for edge cases.
    This is the original sequential implementation kept for compatibility.
    """
    logger.info(f"[SCAMHOUND] Scanning single token (sync fallback): {token_mint}")
    
    # Check if already scored
    if skip_if_scored and database.token_already_scored(token_mint):
        logger.info(f"[SCAMHOUND] Token {token_mint[:8]}... already scored, skipping")
        return database.get_token_score(token_mint)
    
    # Check if scored recently (within last hour) to prevent duplicates
    if database.was_recently_scored(token_mint, hours=1):
        logger.info(f"[SCAMHOUND] Token {token_mint[:8]}... scored within last hour, skipping")
        return database.get_token_score(token_mint)
    
    try:
        # Build token profile
        token_data = {
            "token_mint": token_mint,
            "name": "Unknown",
            "symbol": "UNKNOWN",
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Get Bags profile (may fail if token not from Bags)
        try:
            bags_profile = bags_client.get_full_token_profile(token_mint)
            if bags_profile:
                token_data.update(bags_profile)
                # Try to get better name/symbol from Bags
                if bags_profile.get("name"):
                    token_data["name"] = bags_profile["name"]
                if bags_profile.get("symbol"):
                    token_data["symbol"] = bags_profile["symbol"]
                # Try to get created_at from Bags if not already set
                if bags_profile.get("created_at"):
                    token_data["created_at"] = bags_profile["created_at"]
        except Exception as e:
            logger.warning(f"[SCAMHOUND] Could not get Bags profile for {token_mint[:8]}...: {e}")
        
        # Calculate token age
        token_data["token_age_minutes"] = _calculate_token_age_minutes(
            token_data.get("created_at")
        )
        token_data["token_status"] = _get_token_status(token_data)
        
        # Check minimum age filter
        age_minutes = token_data.get("token_age_minutes")
        if age_minutes is not None and MIN_TOKEN_AGE_MINUTES > 0:
            if age_minutes < MIN_TOKEN_AGE_MINUTES:
                logger.info(
                    f"[SCAMHOUND] Token {token_mint[:8]}... skipped: "
                    f"age {age_minutes}m < minimum {MIN_TOKEN_AGE_MINUTES}m"
                )
                return None
        
        # Get holder data from Helius
        try:
            holder_data = helius_client.get_token_holders(token_mint)
            if holder_data:
                token_data["holders"] = {
                    "top_holders": holder_data.get("top_holders", []),
                    "top_10_concentration_pct": holder_data.get("top10_pct", 0),
                    "total_holder_count": holder_data.get("total_holders", 0),
                    "concentration_score": holder_data.get("concentration_score", "unknown"),
                    "top1_pct": holder_data.get("top1_pct", 0),
                    "top5_pct": holder_data.get("top5_pct", 0)
                }
        except Exception as e:
            logger.warning(f"[SCAMHOUND] Could not get holder data for {token_mint[:8]}...: {e}")
        
        # Get BubbleMaps cluster analysis
        try:
            bubblemaps_data = bubblemaps_client.get_cluster_analysis(token_mint, chain="solana")
            if bubblemaps_data:
                token_data["bubblemaps"] = {
                    "decentralization_score": bubblemaps_data.get("decentralization_score", 0),
                    "cluster_count": bubblemaps_data.get("cluster_count", 0),
                    "largest_cluster_share": bubblemaps_data.get("largest_cluster_share", 0),
                    "risk_signal": bubblemaps_data.get("risk_signal", "UNKNOWN")
                }
                logger.info(f"[SCAMHOUND] BubbleMaps analysis for {token_mint[:8]}...: "
                           f"decentralization={bubblemaps_data.get('decentralization_score')}, "
                           f"clusters={bubblemaps_data.get('cluster_count')}")
            else:
                logger.warning(f"[SCAMHOUND] No BubbleMaps data for {token_mint[:8]}...")
        except Exception as e:
            logger.warning(f"[SCAMHOUND] Could not get BubbleMaps data for {token_mint[:8]}...: {e}")
        
        # Get creator wallet
        creator_wallet = token_data.get("creator", {}).get("wallet")
        
        if creator_wallet:
            try:
                # Analyze creator wallet
                creator_analysis = helius_client.analyze_creator_wallet(creator_wallet)
                token_data["wallet_age_days"] = creator_analysis.get("wallet_age_days", -1)
                token_data["prior_launch_count"] = creator_analysis.get("prior_launch_count", 0)
                token_data["abandoned_tokens"] = creator_analysis.get("abandoned_tokens", [])
                token_data["days_since_last_launch"] = creator_analysis.get("days_since_last_launch")
            except Exception as e:
                logger.warning(f"[SCAMHOUND] Could not analyze creator wallet for {token_mint[:8]}...: {e}")
            
            # Check holder clustering using Helius holder data
            try:
                holder_wallets = [
                    h.get("address") for h in token_data.get("holders", {}).get("top_holders", [])
                    if h.get("address")
                ]
                
                if holder_wallets:
                    clustering = helius_client.check_wallet_clustering(holder_wallets)
                    token_data["clustering_score"] = clustering.get("clustering_score", 0)
                    token_data["clustered_wallets"] = clustering.get("clustered_wallets", 0)
            except Exception as e:
                logger.warning(f"[SCAMHOUND] Could not check clustering for {token_mint[:8]}...: {e}")
        
        # Get market data
        try:
            market_data = birdeye_client.get_full_market_data(token_mint)
            if market_data:
                overview = market_data.get("overview", {})
                liquidity = market_data.get("liquidity", {})
                trades = market_data.get("trades", {})
                
                token_data["liquidity_usd"] = liquidity.get("liquidity_usd", 0)
                token_data["liquidity_to_mcap_ratio"] = liquidity.get("liquidity_to_mcap_ratio", 0)
                token_data["unique_trader_count"] = trades.get("unique_trader_count", 0)
                token_data["wash_trading_score"] = trades.get("wash_trading_score", 0)
                token_data["large_sell_pressure"] = trades.get("large_sell_pressure", False)
                
                # Try to get token name/symbol from Birdeye overview
                if overview:
                    if not token_data.get("name") or token_data["name"] == "Unknown":
                        birdeye_name = overview.get("name")
                        if birdeye_name:
                            token_data["name"] = birdeye_name
                    if not token_data.get("symbol") or token_data["symbol"] == "UNKNOWN":
                        birdeye_symbol = overview.get("symbol")
                        if birdeye_symbol:
                            token_data["symbol"] = birdeye_symbol
        except Exception as e:
            logger.warning(f"[SCAMHOUND] Could not get market data for {token_mint[:8]}...: {e}")
        
        # Calculate risk score
        score_result = scorer.calculate_risk_score(token_data)
        
        # Save to database
        database.save_score(score_result)
        
        # Mark as processed
        _mark_processed(token_mint)
        
        # Log result
        logger.info(
            f"[SCAMHOUND] {score_result.get('symbol', '???')} | "
            f"Score: {score_result.get('risk_score', 0)} | "
            f"{score_result.get('risk_level', 'UNKNOWN')}"
        )
        
        return score_result
        
    except Exception as e:
        logger.error(f"[SCAMHOUND] Error scanning token {token_mint}: {e}")
        return None


async def _run_cycle_async(tokens: list) -> None:
    """
    Async implementation of the scan loop for fetched tokens.
    
    Processes tokens sequentially via scan_single_token_async(),
    which internally parallelizes the API calls for each individual token.
    """
    new_tokens_processed = 0
    skipped_already_scored = 0
    skipped_no_mint = 0
    
    for token in tokens:
        # Try multiple possible field names for token mint
        token_mint = (token.get("tokenMint") or 
                     token.get("mint") or 
                     token.get("token_mint") or
                     token.get("address"))
        
        if not token_mint:
            skipped_no_mint += 1
            logger.debug(f"[SCAMHOUND] Skipping token with no mint: {token}")
            continue
        
        # Skip if already processed
        if _is_processed(token_mint):
            skipped_already_scored += 1
            continue
        
        if database.token_already_scored(token_mint):
            _mark_processed(token_mint)
            skipped_already_scored += 1
            continue
        
        # Check if scored recently (within last hour) to prevent duplicates
        if database.was_recently_scored(token_mint, hours=1):
            logger.info(
                f"[SCAMHOUND] Token {token_mint[:8]}... "
                "scored within last hour, skipping"
            )
            _mark_processed(token_mint)
            skipped_already_scored += 1
            continue
        
        # Delegate to the async scan path (parallel API calls per token)
        result = await scan_single_token_async(
            token_mint=token_mint, skip_if_scored=False
        )
        
        if result is not None:
            new_tokens_processed += 1
        
        # Small delay between tokens to avoid rate limits
        await asyncio.sleep(1)
    
    logger.info(
        f"[SCAMHOUND] Cycle complete. Processed: {new_tokens_processed}, "
        f"Skipped (already scored): {skipped_already_scored}, "
        f"Skipped (no mint): {skipped_no_mint}"
    )


def run_cycle() -> None:
    """
    Execute one full monitoring cycle.
    
    1. Get recent launches from Bags.fm
    2. For each new token, delegate to scan_single_token_async()
       which parallelizes API calls per token.
    3. Trigger Twitter alerts for high-risk tokens
    """
    logger.info("[SCAMHOUND] Starting monitor cycle...")
    
    try:
        # Get recent launches (limited to 25 tokens per cycle)
        recent_tokens = bags_client.get_recent_launches(limit=25)[:25]
        
        if not recent_tokens:
            logger.info("[SCAMHOUND] No recent tokens found from Bags API")
            return
        
        logger.info(f"[SCAMHOUND] Got {len(recent_tokens)} tokens from Bags feed")
        
        # Run the async scan loop in a new event loop
        # (APScheduler calls run_cycle from a thread)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_cycle_async(recent_tokens))
        finally:
            loop.close()
        
        # Trigger Twitter alerts for high-risk tokens
        try:
            twitter_bot.send_pending_alerts()
        except Exception as e:
            logger.error(f"[SCAMHOUND] Twitter alert error: {e}")
            
    except Exception as e:
        logger.error(f"[SCAMHOUND] Monitor cycle error: {e}")


def start_scheduler() -> None:
    """Start the monitoring scheduler."""
    global _scheduler
    _scheduler = BackgroundScheduler()
    scheduler = _scheduler
    
    scheduler.add_job(
        run_cycle,
        trigger=IntervalTrigger(seconds=POLL_INTERVAL),
        id="scamhound_monitor",
        name="ScamHound Token Monitor",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info(f"[SCAMHOUND] Monitor scheduler started (interval: {POLL_INTERVAL}s)")
    
    # Run first cycle immediately
    logger.info("[SCAMHOUND] Running initial monitor cycle...")
    run_cycle()


def stop_scheduler() -> None:
    """Stop the monitoring scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("[SCAMHOUND] Monitor scheduler stopped")
    else:
        logger.warning("[SCAMHOUND] No scheduler running to stop")