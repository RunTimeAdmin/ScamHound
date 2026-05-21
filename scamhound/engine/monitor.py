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
from clients import dexscreener_client
from clients import domain_age_client
from clients import geckoterminal_client
from clients import jupiter_client
from clients import pumpfun_client
from clients import platform_router
from engine import database
from engine import scorer
from alerts import twitter_bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
RISK_THRESHOLD = int(os.getenv("RISK_ALERT_THRESHOLD", "65"))
MIN_TOKEN_AGE_MINUTES = int(os.getenv("MIN_TOKEN_AGE_MINUTES", "0"))  # Skip tokens younger than this
AUTO_SCAN_ENABLED = os.environ.get("AUTO_SCAN_ENABLED", "false").lower() == "true"

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
            # Numeric strings from some APIs are unix timestamps.
            if created_at.isdigit():
                raw_ts = int(created_at)
                # Normalize epoch precision (seconds vs ms/us/ns).
                if raw_ts > 10**18:  # nanoseconds
                    raw_ts = raw_ts / 1_000_000_000
                elif raw_ts > 10**15:  # microseconds
                    raw_ts = raw_ts / 1_000_000
                elif raw_ts > 10**12:  # milliseconds
                    raw_ts = raw_ts / 1_000
                created_dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            else:
                # Handle various ISO formats
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        elif isinstance(created_at, (int, float)):
            # Unix timestamp in seconds
            raw_ts = created_at
            if raw_ts > 10**18:  # nanoseconds
                raw_ts = raw_ts / 1_000_000_000
            elif raw_ts > 10**15:  # microseconds
                raw_ts = raw_ts / 1_000_000
            elif raw_ts > 10**12:  # milliseconds
                raw_ts = raw_ts / 1_000
            created_dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
        elif isinstance(created_at, datetime):
            # Handle various ISO formats
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


def _extract_lp_mint_from_overview(overview: Dict[str, Any]) -> Optional[str]:
    """Best-effort LP mint extraction across provider payload variants."""
    if not isinstance(overview, dict):
        return None
    candidates = [
        overview.get("lpMint"),
        overview.get("lp_mint"),
        overview.get("liquidityTokenMint"),
        overview.get("liquidity_token_mint"),
        overview.get("pairLpMint"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _detect_top_holder_dumping(
    top_holders: list,
    recent_trades: list,
) -> Dict[str, Any]:
    """Detect notable sell flow from top holder wallets."""
    top_wallets = set()
    for holder in top_holders or []:
        if not isinstance(holder, dict):
            continue
        wallet = str(holder.get("address") or "").strip()
        if wallet:
            top_wallets.add(wallet)

    sell_count = 0
    sell_volume = 0.0
    buy_volume = 0.0
    for trade in recent_trades or []:
        if not isinstance(trade, dict):
            continue
        wallet = str(trade.get("wallet") or "").strip()
        side = str(trade.get("side") or "").lower()
        if wallet in top_wallets and side == "sell":
            sell_count += 1
            sell_volume += float(trade.get("amount_usd") or 0.0)
        elif wallet in top_wallets and side == "buy":
            buy_volume += float(trade.get("amount_usd") or 0.0)

    net_sell = max(0.0, sell_volume - buy_volume)
    suspected = sell_count >= 2 or sell_volume >= 10_000 or net_sell >= 8_000
    return {
        "top_holder_dumping_suspected": suspected,
        "top_holder_sell_count": sell_count,
        "top_holder_sell_volume_usd": round(sell_volume, 2),
        "top_holder_net_sell_usd": round(net_sell, 2),
        "top_holder_net_sell_suspected": net_sell >= 8_000,
    }


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


async def _async_get_pumpfun_profile(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting pump.fun token metadata."""
    try:
        return await asyncio.to_thread(
            pumpfun_client.get_token_profile, token_mint
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get pump.fun profile for "
            f"{token_mint[:8]}...: {e}"
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
                "total_holder_count": holder_data.get("total_holders"),
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


async def _async_get_token_security_signals(
    token_mint: str,
) -> Optional[Dict[str, Any]]:
    """Async wrapper for mint authorities and Token-2022 signals."""
    try:
        return await asyncio.to_thread(
            helius_client.get_token_security_signals,
            token_mint,
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get token security signals for "
            f"{token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_bubblemaps_data(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for getting BubbleMaps cluster analysis."""
    try:
        bubblemaps_data = await asyncio.to_thread(
            bubblemaps_client.get_cluster_analysis, token_mint, chain="solana"
        )
        if bubblemaps_data:
            decentralization_score = bubblemaps_data.get(
                "decentralization_score", 0
            )
            cluster_count = bubblemaps_data.get("cluster_count", 0)
            largest_cluster_share = bubblemaps_data.get(
                "largest_cluster_share", 0
            )

            # Derive risk signal from the data
            if largest_cluster_share > 70 or cluster_count == 1:
                risk_signal = "HIGHLY_CENTRALIZED"
            elif largest_cluster_share > 40 or cluster_count < 3:
                risk_signal = "MODERATE_CENTRALIZATION"
            elif cluster_count >= 5 and largest_cluster_share < 25:
                risk_signal = "DECENTRALIZED"
            else:
                risk_signal = "MODERATE"

            logger.info(
                f"[SCAMHOUND] BubbleMaps analysis for {token_mint[:8]}...: "
                f"decentralization={decentralization_score}, "
                f"clusters={cluster_count}"
            )
            return {
                "decentralization_score": decentralization_score,
                "cluster_count": cluster_count,
                "largest_cluster_share": largest_cluster_share,
                "risk_signal": risk_signal
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
        market_data = await asyncio.to_thread(
            birdeye_client.get_full_market_data, token_mint, True
        )
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


async def _async_get_geckoterminal_fallback(
    token_mint: str,
) -> Optional[Dict[str, Any]]:
    """Async wrapper for public GeckoTerminal fallback market signals."""
    try:
        return await asyncio.to_thread(
            geckoterminal_client.get_token_market_fallback,
            token_mint,
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get GeckoTerminal fallback for "
            f"{token_mint[:8]}...: {e}"
        )
        return None


async def _async_simulate_honeypot(token_mint: str) -> Optional[Dict[str, Any]]:
    """Async wrapper for Jupiter round-trip honeypot check."""
    try:
        return await asyncio.to_thread(
            jupiter_client.simulate_round_trip,
            token_mint,
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not simulate round-trip swap for "
            f"{token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_dexscreener_signals(
    token_mint: str,
) -> Optional[Dict[str, Any]]:
    """Async wrapper for DexScreener trust/warning signals."""
    try:
        return await asyncio.to_thread(
            dexscreener_client.get_token_trust_signals,
            token_mint,
        )
    except Exception as e:
        logger.warning(
            f"[SCAMHOUND] Could not get DexScreener signals for "
            f"{token_mint[:8]}...: {e}"
        )
        return None


async def _async_get_domain_age(urls: list) -> Optional[Dict[str, Any]]:
    """Async wrapper for website domain-age lookup."""
    try:
        return await asyncio.to_thread(
            domain_age_client.lookup_domain_age_from_urls,
            urls,
        )
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not lookup domain age: {e}")
        return None


async def _async_analyze_lp_controls(
    lp_mint: str,
    creator_wallet: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Async wrapper for LP burn/lock/creator control analysis."""
    try:
        return await asyncio.to_thread(
            helius_client.analyze_lp_token_controls,
            lp_mint,
            creator_wallet,
        )
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not analyze LP controls: {e}")
        return None


async def _async_detect_bundle_launch(
    creator_wallet: Optional[str],
    recent_buy_wallets: list,
) -> Optional[Dict[str, Any]]:
    """Async wrapper for launch bundle/snipe detection."""
    try:
        return await asyncio.to_thread(
            helius_client.analyze_bundle_launch,
            creator_wallet,
            recent_buy_wallets,
        )
    except Exception as e:
        logger.warning(f"[SCAMHOUND] Could not detect launch bundle: {e}")
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
            # Keep unknown when source APIs don't provide launch time.
            "created_at": None,
        }
        
        # Source profile by platform hint:
        # - pump mints: avoid Bags endpoints (they return 400 for many pump tokens)
        # - non-pump: keep existing Bags enrichment
        is_pump_mint = token_mint.lower().endswith("pump")
        if is_pump_mint:
            pump_profile = await _async_get_pumpfun_profile(token_mint)
            if pump_profile:
                token_data.update(pump_profile)
                creator_wallet = pump_profile.get("creator_wallet")
                if creator_wallet:
                    token_data["creator"] = {
                        "wallet": creator_wallet,
                        "username": "pumpfun",
                        "royalty_pct": 0.0,
                    }
        else:
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
        security_task = _async_get_token_security_signals(token_mint)
        bubblemaps_task = _async_get_bubblemaps_data(token_mint)
        market_task = _async_get_market_data(token_mint)
        honeypot_task = _async_simulate_honeypot(token_mint)
        dexscreener_task = _async_get_dexscreener_signals(token_mint)
        
        (
            holder_data,
            security_data,
            bubblemaps_data,
            market_data,
            honeypot_data,
            dexscreener_data,
        ) = (
            await asyncio.gather(
                holder_task,
                security_task,
                bubblemaps_task,
                market_task,
                honeypot_task,
                dexscreener_task,
                return_exceptions=True,
            )
        )
        
        # Process holder data
        if holder_data and not isinstance(holder_data, Exception):
            token_data["holders"] = holder_data
            burn_ratio = helius_client.analyze_supply_burn_ratio(
                holder_data.get("top_holders", [])
            )
            token_data.update(burn_ratio)

        # Process on-chain mint security controls
        if security_data and not isinstance(security_data, Exception):
            token_data["security"] = security_data
        
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
            token_data["two_sided_trader_activity_ratio"] = trades.get(
                "two_sided_trader_activity_ratio",
                trades.get("two_sided_trader_ratio", 0),
            )
            # Backward-compat aliases for older prompt/template consumers.
            token_data["two_sided_trader_ratio"] = token_data[
                "two_sided_trader_activity_ratio"
            ]
            token_data["wash_trading_score"] = token_data[
                "two_sided_trader_activity_ratio"
            ]
            token_data["large_sell_pressure"] = trades.get("large_sell_pressure", False)
            token_data["honeypot_suspected"] = trades.get(
                "honeypot_suspected",
                False,
            )
            token_data["buy_count"] = trades.get("buy_count", 0)
            token_data["sell_count"] = trades.get("sell_count", 0)
            token_data["recent_buy_wallets"] = trades.get(
                "recent_buy_wallets",
                [],
            )
            token_data["recent_trades"] = trades.get("recent_trades", [])
            token_data["wash_trade_cycle_count"] = trades.get(
                "wash_trade_cycle_count",
                0,
            )
            token_data["wash_trade_suspected"] = trades.get(
                "wash_trade_suspected",
                False,
            )
            token_data["unique_buyers_last_hour"] = trades.get(
                "unique_buyers_last_hour",
                0,
            )
            token_data["unique_buyers_prev_hour"] = trades.get(
                "unique_buyers_prev_hour",
                0,
            )
            token_data["holder_velocity_spike"] = trades.get(
                "holder_velocity_spike",
                False,
            )
            token_data["unique_buyers_last_15m"] = trades.get(
                "unique_buyers_last_15m",
                0,
            )
            token_data["unique_buyers_prev_15m"] = trades.get(
                "unique_buyers_prev_15m",
                0,
            )
            token_data["unique_buyers_last_6h"] = trades.get(
                "unique_buyers_last_6h",
                0,
            )
            token_data["unique_buyers_prev_6h"] = trades.get(
                "unique_buyers_prev_6h",
                0,
            )
            token_data["holder_velocity_band"] = trades.get(
                "holder_velocity_band",
                "stable",
            )
            if token_data.get("holders", {}).get("top_holders"):
                dumping = _detect_top_holder_dumping(
                    top_holders=token_data.get("holders", {}).get(
                        "top_holders", []
                    ),
                    recent_trades=token_data.get("recent_trades", []),
                )
                token_data.update(dumping)
            
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

                # Fallback creator wallet for tokens where Bags profile is unavailable.
                if not token_data.get("creator", {}).get("wallet"):
                    creator_wallet = overview.get("creator_wallet")
                    if creator_wallet:
                        token_data["creator"] = {
                            "wallet": creator_wallet,
                            "username": "unknown",
                            "royalty_pct": 0.0,
                        }

                # Backfill created_at from market overview when Bags did not provide it.
                if not token_data.get("created_at") and overview.get("created_at"):
                    token_data["created_at"] = overview.get("created_at")

                # Prefer real holder count from market overview when available.
                # Helius getTokenLargestAccounts only gives top holders, so earlier
                # values can be rough estimates for newer scans.
                holder_count = (
                    overview.get("holderCount")
                    or overview.get("holder_count")
                    or overview.get("holders")
                    or overview.get("holder")
                    or overview.get("uniqueHolders")
                    or overview.get("holdersCount")
                )
                if holder_count is not None:
                    try:
                        normalized_holder_count = int(float(holder_count))
                    except (TypeError, ValueError):
                        normalized_holder_count = None

                    if normalized_holder_count is not None and normalized_holder_count >= 0:
                        existing_holders = token_data.get("holders") or {}
                        existing_holders["total_holder_count"] = normalized_holder_count
                        token_data["holders"] = existing_holders

                # Optional LP lock/burn metadata when Birdeye provides it.
                lp_locked = (
                    overview.get("lp_locked")
                    or overview.get("lpLocked")
                    or overview.get("liquidityLocked")
                    or overview.get("liquidity_locked")
                )
                lp_burned = (
                    overview.get("lp_burned")
                    or overview.get("lpBurned")
                    or overview.get("liquidityBurned")
                    or overview.get("liquidity_burned")
                )
                if lp_locked is not None or lp_burned is not None:
                    token_data["lp_controls"] = {
                        "lp_locked": (
                            bool(lp_locked) if lp_locked is not None else None
                        ),
                        "lp_burned": (
                            bool(lp_burned) if lp_burned is not None else None
                        ),
                    }

                # Stronger LP lock/burn verification when LP mint is available.
                lp_mint = _extract_lp_mint_from_overview(overview)
                creator_wallet = token_data.get("creator", {}).get("wallet")
                if lp_mint:
                    lp_analysis = await _async_analyze_lp_controls(
                        lp_mint=lp_mint,
                        creator_wallet=creator_wallet,
                    )
                    if lp_analysis:
                        lp_controls = token_data.get("lp_controls") or {}
                        lp_controls.update(
                            {
                                "lp_locked": lp_analysis.get("lp_locked"),
                                "lp_burned": lp_analysis.get("lp_burned"),
                                "lp_unlocked_creator_controlled": lp_analysis.get(
                                    "lp_unlocked_creator_controlled"
                                ),
                                "lp_burned_share_pct": lp_analysis.get(
                                    "lp_burned_share_pct"
                                ),
                                "lp_locked_share_pct": lp_analysis.get(
                                    "lp_locked_share_pct"
                                ),
                                "lp_creator_share_pct": lp_analysis.get(
                                    "lp_creator_share_pct"
                                ),
                            }
                        )
                        token_data["lp_controls"] = lp_controls

        gecko_data = None
        needs_gecko_fallback = (
            float(token_data.get("liquidity_usd", 0) or 0) <= 0
            or not token_data.get("created_at")
        )
        if needs_gecko_fallback:
            gecko_data = await _async_get_geckoterminal_fallback(token_mint)

        # Public fallback for missing/zero Birdeye market fields.
        if gecko_data and not isinstance(gecko_data, Exception):
            token_data["geckoterminal_checked"] = gecko_data.get("checked", False)
            if float(token_data.get("liquidity_usd", 0) or 0) <= 0:
                token_data["liquidity_usd"] = float(
                    gecko_data.get("liquidity_usd", 0) or 0
                )
                token_data["liquidity_source"] = "geckoterminal_fallback"
            if float(token_data.get("liquidity_to_mcap_ratio", 0) or 0) <= 0:
                token_data["liquidity_to_mcap_ratio"] = float(
                    gecko_data.get("liquidity_to_mcap_ratio", 0) or 0
                )
            if not token_data.get("created_at") and gecko_data.get("pool_created_at"):
                token_data["created_at"] = gecko_data.get("pool_created_at")
            gecko_total_txns = int(gecko_data.get("txns_h24_buys", 0) or 0) + int(
                gecko_data.get("txns_h24_sells", 0) or 0
            )
            if (
                int(token_data.get("buy_count", 0) or 0) == 0
                and int(token_data.get("sell_count", 0) or 0) == 0
                and gecko_total_txns > 0
            ):
                token_data["buy_count"] = int(gecko_data.get("txns_h24_buys", 0) or 0)
                token_data["sell_count"] = int(
                    gecko_data.get("txns_h24_sells", 0) or 0
                )
                token_data["trade_activity_source"] = "geckoterminal_fallback"
                if int(token_data.get("unique_trader_count", 0) or 0) <= 0:
                    token_data["unique_trader_count"] = gecko_total_txns

        # Process Jupiter round-trip honeypot check
        if honeypot_data and not isinstance(honeypot_data, Exception):
            token_data["honeypot_simulation_status"] = honeypot_data.get("status")
            token_data["honeypot_round_trip_loss_pct"] = honeypot_data.get(
                "round_trip_loss_pct"
            )
            token_data["jupiter_buy_route_count"] = int(
                honeypot_data.get("buy_route_count", 0) or 0
            )
            token_data["jupiter_sell_route_count"] = int(
                honeypot_data.get("sell_route_count", 0) or 0
            )
            token_data["jupiter_buy_price_impact_pct"] = float(
                honeypot_data.get("buy_price_impact_pct", 0) or 0
            )
            token_data["jupiter_sell_price_impact_pct"] = float(
                honeypot_data.get("sell_price_impact_pct", 0) or 0
            )
            token_data["jupiter_total_price_impact_pct"] = float(
                honeypot_data.get("total_price_impact_pct", 0) or 0
            )
            token_data["jupiter_route_complexity"] = honeypot_data.get(
                "route_complexity"
            )
            token_data["honeypot_simulation_reason"] = honeypot_data.get("reason")
            token_data["honeypot_simulation_checked"] = honeypot_data.get(
                "checked"
            )
            token_data["honeypot_suspected"] = bool(
                token_data.get("honeypot_suspected", False)
                or honeypot_data.get("honeypot_suspected", False)
            )

            # If Birdeye overview included market cap indirectly (as marketcap key)
            # but liquidity ratio wasn't precomputed, derive it here.
            if token_data.get("liquidity_to_mcap_ratio", 0) == 0:
                try:
                    liquidity_usd = float(token_data.get("liquidity_usd", 0) or 0)
                    market_cap = float(
                        overview.get("marketcap", 0) if overview else 0
                    )
                    if liquidity_usd > 0 and market_cap > 0:
                        token_data["liquidity_to_mcap_ratio"] = round(
                            liquidity_usd / market_cap, 4
                        )
                except (TypeError, ValueError):
                    pass

        # Process DexScreener supporting trust/warning signals
        if dexscreener_data and not isinstance(dexscreener_data, Exception):
            token_data["dexscreener"] = dexscreener_data
            token_data["dexscreener_checked"] = dexscreener_data.get(
                "checked", False
            )
            token_data["dexscreener_has_pair"] = dexscreener_data.get(
                "has_pair", False
            )
            token_data["dexscreener_pair_count"] = dexscreener_data.get(
                "pair_count", 0
            )
            token_data["dexscreener_labels"] = dexscreener_data.get("labels", [])
            token_data["dexscreener_has_trust_badge"] = dexscreener_data.get(
                "has_trust_badge", False
            )
            token_data["dexscreener_has_warning_label"] = (
                dexscreener_data.get("has_warning_label", False)
            )
            token_data["dexscreener_warning_labels"] = dexscreener_data.get(
                "warning_labels",
                [],
            )
            token_data["dexscreener_website_urls"] = dexscreener_data.get(
                "website_urls",
                [],
            )
            token_data["dexscreener_txns_h24_buys"] = int(
                dexscreener_data.get("txns_h24_buys", 0) or 0
            )
            token_data["dexscreener_txns_h24_sells"] = int(
                dexscreener_data.get("txns_h24_sells", 0) or 0
            )
            token_data["dexscreener_txns_h24_total"] = (
                token_data["dexscreener_txns_h24_buys"]
                + token_data["dexscreener_txns_h24_sells"]
            )
            token_data["dexscreener_pair_created_at"] = dexscreener_data.get(
                "pair_created_at"
            )
            token_data["dexscreener_liquidity_usd"] = float(
                dexscreener_data.get("liquidity_usd", 0) or 0
            )
            token_data["dexscreener_liquidity_to_mcap_ratio"] = float(
                dexscreener_data.get("liquidity_to_mcap_ratio", 0) or 0
            )

            # Fallback: when Birdeye recent trade counters are empty, use
            # DexScreener txn aggregates to avoid false "no activity" unknowns.
            if (
                int(token_data.get("buy_count", 0) or 0) == 0
                and int(token_data.get("sell_count", 0) or 0) == 0
                and token_data["dexscreener_txns_h24_total"] > 0
            ):
                token_data["buy_count"] = token_data["dexscreener_txns_h24_buys"]
                token_data["sell_count"] = token_data["dexscreener_txns_h24_sells"]
                token_data["trade_activity_source"] = "dexscreener_fallback"
                if int(token_data.get("unique_trader_count", 0) or 0) <= 0:
                    token_data["unique_trader_count"] = token_data[
                        "dexscreener_txns_h24_total"
                    ]

            if float(token_data.get("liquidity_usd", 0) or 0) <= 0:
                if token_data["dexscreener_liquidity_usd"] > 0:
                    token_data["liquidity_usd"] = token_data[
                        "dexscreener_liquidity_usd"
                    ]
                    token_data["liquidity_source"] = "dexscreener_fallback"
            if float(token_data.get("liquidity_to_mcap_ratio", 0) or 0) <= 0:
                ratio = token_data["dexscreener_liquidity_to_mcap_ratio"]
                if ratio > 0:
                    token_data["liquidity_to_mcap_ratio"] = ratio
            if (
                not token_data.get("created_at")
                and token_data.get("dexscreener_pair_created_at")
            ):
                token_data["created_at"] = token_data.get(
                    "dexscreener_pair_created_at"
                )

            domain_data = await _async_get_domain_age(
                token_data.get("dexscreener_website_urls", [])
            )
            if domain_data:
                token_data["domain_name"] = domain_data.get("domain")
                token_data["domain_age_checked"] = domain_data.get("checked")
                token_data["domain_age_days"] = domain_data.get("age_days")
                token_data["domain_recently_registered"] = domain_data.get(
                    "recently_registered"
                )

        # Recompute age/status after market enrichment in case created_at was backfilled.
        if token_data.get("token_age_minutes") is None and token_data.get("created_at"):
            token_data["token_age_minutes"] = _calculate_token_age_minutes(
                token_data.get("created_at")
            )
            token_data["token_status"] = _get_token_status(token_data)
        
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
            bundle_task = _async_detect_bundle_launch(
                creator_wallet=creator_wallet,
                recent_buy_wallets=token_data.get("recent_buy_wallets", []),
            )
            
            # Prepare clustering task if we have holder wallets
            holder_wallets = [
                h.get("address") for h in token_data.get("holders", {}).get("top_holders", [])
                if h.get("address")
            ]
            
            if holder_wallets:
                clustering_task = _async_check_clustering(holder_wallets)
                (
                    creator_result,
                    clustering_result,
                    bundle_result,
                ) = await asyncio.gather(
                    creator_task, clustering_task,
                    bundle_task,
                    return_exceptions=True
                )
            else:
                creator_result = await creator_task
                clustering_result = None
                bundle_result = await bundle_task
            
            # Process creator analysis
            if creator_result and not isinstance(creator_result, Exception):
                token_data.update(creator_result)
            
            # Process clustering result
            if clustering_result and not isinstance(clustering_result, Exception):
                token_data["clustering_score"] = clustering_result.get("clustering_score", 0)
                token_data["clustered_wallets"] = clustering_result.get("clustered_wallets", 0)

            if bundle_result and not isinstance(bundle_result, Exception):
                token_data.update(bundle_result)
        
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
    """Scan a single token through the full pipeline.
    
    For synchronous callers (e.g., APScheduler), creates a local loop and runs
    the async scanner directly. In async contexts, callers must use
    scan_single_token_async().
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (APScheduler thread context) — run locally
        return asyncio.run(scan_single_token_async(token_mint, skip_if_scored))
    raise RuntimeError(
        "scan_single_token() cannot be called from an active event loop; "
        "use scan_single_token_async() instead."
    )


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
        # Get recent launches from all active platforms (limited to 25 per platform)
        recent_tokens = platform_router.get_recent_launches(limit=25)[:50]
        
        if not recent_tokens:
            logger.info("[SCAMHOUND] No recent tokens found from any platform")
            return
        
        logger.info(f"[SCAMHOUND] Got {len(recent_tokens)} tokens from platform router")
        
        # APScheduler executes this in a worker thread; use asyncio.run
        # so each cycle has an isolated lifecycle-managed event loop.
        asyncio.run(_run_cycle_async(recent_tokens))
        
        # Trigger Twitter alerts for high-risk tokens
        try:
            twitter_bot.send_pending_alerts()
        except Exception as e:
            logger.error(f"[SCAMHOUND] Twitter alert error: {e}")
            
    except Exception as e:
        logger.error(f"[SCAMHOUND] Monitor cycle error: {e}")


def start_scheduler() -> None:
    """Start the monitoring scheduler."""
    if not AUTO_SCAN_ENABLED:
        logger.info("[MONITOR] Auto-scanning disabled (AUTO_SCAN_ENABLED != true)")
        return

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


def run_rescore_cycle():
    """Re-score tokens that are risky and within their first 7 days.
    Called by APScheduler every 24 hours.
    """
    tokens_to_rescore = database.get_tokens_for_rescore(max_age_days=7, min_score=40, limit=25)

    if not tokens_to_rescore:
        logger.info("[RESCORE] No tokens eligible for re-scoring")
        return

    logger.info(f"[RESCORE] Re-scoring {len(tokens_to_rescore)} tokens")

    for token_info in tokens_to_rescore:
        try:
            # Re-use the existing scan function
            result = scan_single_token(token_info["token_mint"], skip_if_scored=False)
            if result:
                old_score = token_info["risk_score"]
                new_score = result.get("risk_score", 0)
                logger.info(f"[RESCORE] {token_info['symbol'] or token_info['token_mint'][:8]}: {old_score} \u2192 {new_score}")
        except Exception as e:
            logger.error(f"[RESCORE] Error re-scoring {token_info['token_mint'][:8]}: {e}")
            continue