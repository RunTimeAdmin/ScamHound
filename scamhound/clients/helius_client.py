"""
ScamHound Helius API Client
Deep on-chain analysis using creator wallet addresses
"""

import os
import requests
from typing import Optional, Dict, List, Any
import logging
from datetime import datetime, timezone

from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.helius.xyz/v0"
RPC_URL = "https://mainnet.helius-rpc.com"

# Known protocol/infrastructure addresses that should NOT be counted as "holders"
# These are bonding curves, DEX pools, AMM vaults, and system programs
EXCLUDED_HOLDER_ADDRESSES = {
    # Pump.fun
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun program
    "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbCfM35SxDDdCB",  # Pump.fun fee account
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # Pump.fun authority

    # Raydium
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium AMM authority
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # Raydium CP AMM
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium V4 AMM
    "routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS",   # Raydium route

    # System programs
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated token program
    "11111111111111111111111111111111",                 # System program

    # Orca/Whirlpool
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # Orca Whirlpool

    # Meteora
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
}

_CREATOR_HISTORY_CACHE: Dict[str, Dict[str, Any]] = {}
_CREATOR_HISTORY_CACHE_TTL_SECONDS = int(
    os.environ.get("HELIUS_CREATOR_CACHE_TTL_SECONDS", "600")
)


def _fetch_creator_transactions(
    wallet_address: str,
    max_pages: int,
) -> List[Dict[str, Any]]:
    """Fetch paginated creator transactions once for downstream analyses."""
    collected: List[Dict[str, Any]] = []
    before: Optional[str] = None

    for _ in range(max_pages):
        transactions = get_wallet_transaction_history(
            wallet_address, limit=100, before=before
        )
        if not transactions:
            break

        collected.extend(transactions)
        if len(transactions) < 100:
            break

        next_before = transactions[-1].get("signature")
        if not next_before or next_before == before:
            break
        before = next_before

    return collected


def _derive_wallet_age_days(transactions: List[Dict[str, Any]]) -> int:
    """Derive wallet age from fetched transactions."""
    oldest_timestamp: Optional[datetime] = None
    for tx in transactions:
        timestamp = tx.get("timestamp")
        if timestamp:
            tx_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if oldest_timestamp is None or tx_time < oldest_timestamp:
                oldest_timestamp = tx_time

    if oldest_timestamp is None:
        return -1

    now = datetime.now(timezone.utc)
    return max(0, (now - oldest_timestamp).days)


def _derive_previous_token_launches(
    wallet_address: str,
    transactions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Derive creator launch history from fetched transactions."""
    created_mints = set()
    latest_launch_timestamp: Optional[int] = None

    for tx in transactions:
        tx_type = str(tx.get("type", "")).upper()
        if not (
            "CREATE" in tx_type
            or "INITIALIZE_MINT" in tx_type
            or tx_type in {"MINT_TO", "CREATE_TOKEN", "CREATE_MINT"}
        ):
            continue

        fee_payer = (
            tx.get("feePayer")
            or tx.get("feePayerAccount")
            or tx.get("source")
            or ""
        )
        if fee_payer and fee_payer != wallet_address:
            continue

        for transfer in tx.get("tokenTransfers", []):
            mint = transfer.get("mint", "")
            if mint:
                created_mints.add(mint)

        timestamp = tx.get("timestamp")
        if isinstance(timestamp, int):
            if latest_launch_timestamp is None or timestamp > latest_launch_timestamp:
                latest_launch_timestamp = timestamp

    days_since_last_launch: Optional[int] = None
    if latest_launch_timestamp is not None:
        launch_dt = datetime.fromtimestamp(latest_launch_timestamp, tz=timezone.utc)
        days_since_last_launch = max(0, (datetime.now(timezone.utc) - launch_dt).days)

    return {
        "prior_launch_count": max(0, len(created_mints) - 1),
        "abandoned_tokens": [],
        "days_since_last_launch": days_since_last_launch,
    }


def get_creator_history_summary(
    wallet_address: str, max_pages: int = 5
) -> Dict[str, Any]:
    """Get creator age + launch history from one transaction fetch (cached)."""
    now = datetime.now(timezone.utc).timestamp()
    cached = _CREATOR_HISTORY_CACHE.get(wallet_address)
    if cached and now - cached["cached_at"] < _CREATOR_HISTORY_CACHE_TTL_SECONDS:
        return dict(cached["data"])

    transactions = _fetch_creator_transactions(wallet_address, max_pages=max_pages)
    age_days = _derive_wallet_age_days(transactions)
    launches = _derive_previous_token_launches(wallet_address, transactions)
    data = {
        "wallet_age_days": age_days,
        "prior_launch_count": launches["prior_launch_count"],
        "abandoned_tokens": launches["abandoned_tokens"],
        "days_since_last_launch": launches["days_since_last_launch"],
    }
    _CREATOR_HISTORY_CACHE[wallet_address] = {"cached_at": now, "data": dict(data)}
    return data


def _make_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Make a request to the Helius API."""
    url = f"{BASE_URL}{endpoint}"
    api_key = os.environ.get("HELIUS_API_KEY", "")
    if not api_key:
        logger.error("[HELIUS] API key not configured")
        return None
    
    # Add API key to params
    if params is None:
        params = {}
    params["api-key"] = api_key
    
    try:
        response = request_with_retry(
            requests.get, url, params=params, timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"[HELIUS] API error on {endpoint}: {e}")
        return None


def get_wallet_transaction_history(
    wallet_address: str,
    limit: int = 50,
    before: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Get recent transactions for a wallet.
    
    Returns list of transaction objects with type, timestamp, and token info.
    """
    params: Dict[str, Any] = {"limit": max(1, min(limit, 100))}
    if before:
        params["before"] = before

    result = _make_request(f"/addresses/{wallet_address}/transactions", params=params)
    
    if result is None or not isinstance(result, list):
        return []
    
    return result


def get_wallet_age_days(wallet_address: str, max_pages: int = 5) -> int:
    """
    Get the age of a wallet in days since first transaction.
    
    Returns:
    - Age in days (0 if unknown, -1 if error)
    - HIGH RISK if creator wallet is less than 7 days old
    """
    transactions = _fetch_creator_transactions(wallet_address, max_pages=max_pages)
    return _derive_wallet_age_days(transactions)


def get_previous_token_launches(wallet_address: str) -> Dict[str, Any]:
    """
    Identify prior token launches by the same wallet.
    
    Returns:
    - prior_launch_count: int
    - abandoned_tokens: list of token mints where liquidity was removed
    - days_since_last_launch: int (None if first launch)
    """
    transactions = _fetch_creator_transactions(wallet_address, max_pages=3)
    return _derive_previous_token_launches(wallet_address, transactions)


def check_wallet_clustering(holder_wallets: List[str]) -> Dict[str, Any]:
    """
    Check if multiple holder wallets are connected (funded from same source).
    
    Returns:
    - clustered_wallets: int - number of connected wallets
    - clustering_score: float 0.0-1.0 (1.0 = all top holders are connected)
    - HIGH RISK if: clustering_score > 0.4
    """
    if not holder_wallets or len(holder_wallets) < 2:
        return {
            "clustered_wallets": 0,
            "clustering_score": 0.0
        }
    
    # Track funding sources for each wallet
    funding_sources = {}
    
    for wallet in holder_wallets[:10]:  # Limit to top 10
        transactions = get_wallet_transaction_history(wallet, limit=10)
        
        if not transactions:
            continue
        
        # Find the earliest incoming transfer (funding source)
        for tx in reversed(transactions):
            native_transfers = tx.get("nativeTransfers", [])
            for transfer in native_transfers:
                if transfer.get("toUserAccount") == wallet:
                    source = transfer.get("fromUserAccount", "")
                    if source:
                        if source not in funding_sources:
                            funding_sources[source] = []
                        funding_sources[source].append(wallet)
                        break
    
    # Find clusters (multiple wallets funded from same source)
    max_cluster = 0
    total_clustered = 0
    
    for source, wallets in funding_sources.items():
        if len(wallets) > 1:
            total_clustered += len(wallets)
            max_cluster = max(max_cluster, len(wallets))
    
    # Calculate clustering score
    total_analyzed = len(holder_wallets[:10])
    clustering_score = total_clustered / total_analyzed if total_analyzed > 0 else 0.0
    
    return {
        "clustered_wallets": total_clustered,
        "clustering_score": round(clustering_score, 2)
    }


def get_token_holders(token_mint: str, limit: int = 20) -> Optional[Dict[str, Any]]:
    """
    Get holder distribution for a token via Helius RPC API.
    
    Uses getTokenLargestAccounts RPC method to fetch top token holders,
    then calculates concentration metrics.
    
    Args:
        token_mint: The token mint address
        limit: Maximum number of holders to fetch (default 20)
    
    Returns:
        dict with keys:
            - 'top_holders': list of {address, balance, percentage}
            - 'total_holders': exact count when known, otherwise None
            - 'sampled_holder_count': number of top accounts returned by RPC
            - 'concentration_score': str ('critical', 'high', 'moderate', 'low')
            - 'top1_pct': float - top holder percentage
            - 'top5_pct': float - top 5 holders combined percentage
            - 'top10_pct': float - top 10 holders combined percentage
        None if API call fails
    """
    api_key = os.environ.get("HELIUS_API_KEY", "")
    if not api_key:
        logger.error("[HELIUS] API key not configured")
        return None
    
    url = f"{RPC_URL}/?api-key={api_key}"
    
    # First, try getTokenLargestAccounts to get top holders
    payload = {
        "jsonrpc": "2.0",
        "id": "helius-holders",
        "method": "getTokenLargestAccounts",
        "params": [token_mint]
    }
    
    try:
        response = request_with_retry(
            requests.post, url, json=payload, timeout=30
        )
        
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            logger.error(f"[HELIUS] RPC error: {data['error']}")
            return None
        
        accounts = data.get("result", {}).get("value", [])
        
        if not accounts:
            logger.warning(f"[HELIUS] No holder accounts found for {token_mint}")
            return None
        
        # Get token supply to calculate percentages
        supply_payload = {
            "jsonrpc": "2.0",
            "id": "helius-supply",
            "method": "getTokenSupply",
            "params": [token_mint]
        }
        
        supply_response = request_with_retry(
            requests.post, url, json=supply_payload, timeout=30
        )
        supply_response.raise_for_status()
        supply_data = supply_response.json()
        
        if "error" in supply_data:
            logger.error(f"[HELIUS] Supply RPC error: {supply_data['error']}")
            return None
        
        supply_info = supply_data.get("result", {}).get("value", {})
        total_supply = float(supply_info.get("amount", 0))
        decimals = supply_info.get("decimals", 0)
        
        if total_supply == 0:
            logger.warning(f"[HELIUS] Zero total supply for {token_mint}")
            return None
        
        # Process holder accounts
        all_holders = []
        for account in accounts[:limit]:
            address = account.get("address", "")
            # Amount is in raw token units, convert to actual tokens
            raw_balance = float(account.get("amount", 0))
            balance = raw_balance / (10 ** decimals) if decimals > 0 else raw_balance
            percentage = (raw_balance / total_supply) * 100 if total_supply > 0 else 0
            
            all_holders.append({
                "address": address,
                "balance": balance,
                "percentage": round(percentage, 2)
            })
        
        # Filter out known protocol/infrastructure addresses
        filtered_holders = [
            h for h in all_holders
            if h.get("address") not in EXCLUDED_HOLDER_ADDRESSES
        ]
        excluded_count = len(all_holders) - len(filtered_holders)
        if excluded_count > 0:
            logger.debug(f"[HELIUS] Excluded {excluded_count} protocol addresses from holder analysis")
        
        # Detect pump.fun tokens and handle bonding curve
        is_pumpfun = token_mint.endswith("pump")
        bonding_curve_excluded = False
        
        if is_pumpfun and filtered_holders:
            # The largest holder on a pump.fun token in bonding phase is almost always
            # the bonding curve PDA. Exclude if it holds > 70% of supply.
            top_holder = filtered_holders[0]
            if top_holder.get("percentage", 0) > 70:
                logger.info(f"[HELIUS] Pump.fun bonding curve detected ({top_holder['percentage']:.1f}%), excluding from concentration calc")
                filtered_holders = filtered_holders[1:]
                bonding_curve_excluded = True
        
        # Use filtered holders for concentration metrics
        top_holders = filtered_holders
        
        # Calculate concentration metrics
        top1_pct = top_holders[0]["percentage"] if len(top_holders) >= 1 else 0
        top5_pct = sum(h["percentage"] for h in top_holders[:5]) if len(top_holders) >= 5 else sum(h["percentage"] for h in top_holders)
        top10_pct = sum(h["percentage"] for h in top_holders[:10]) if len(top_holders) >= 10 else sum(h["percentage"] for h in top_holders)
        
        # Determine concentration score based on top holder
        if top1_pct > 50:
            concentration_score = "critical"
        elif top1_pct > 30:
            concentration_score = "high"
        elif top1_pct > 15:
            concentration_score = "moderate"
        else:
            concentration_score = "low"
        
        # Only report total holders when we can infer it accurately.
        sampled_holder_count = len(accounts)
        total_holders = sampled_holder_count if sampled_holder_count < limit else None
        
        logger.info(f"[HELIUS] Holder analysis for {token_mint[:8]}...: "
                   f"top1={top1_pct:.1f}%, top5={top5_pct:.1f}%, top10={top10_pct:.1f}%, "
                   f"concentration={concentration_score}"
                   f"{' (pump.fun, bonding curve excluded)' if bonding_curve_excluded else ''}")
        
        return {
            "top_holders": top_holders,
            "total_holders": total_holders,
            "sampled_holder_count": sampled_holder_count,
            "concentration_score": concentration_score,
            "top1_pct": round(top1_pct, 2),
            "top5_pct": round(top5_pct, 2),
            "top10_pct": round(top10_pct, 2),
            "is_pumpfun": is_pumpfun,
            "bonding_curve_excluded": bonding_curve_excluded
        }
        
    except requests.exceptions.Timeout:
        logger.error(f"[HELIUS] Timeout getting holders for {token_mint}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[HELIUS] Request error getting holders: {e}")
        return None
    except Exception as e:
        logger.error(f"[HELIUS] Unexpected error getting holders: {e}")
        return None


def analyze_creator_wallet(wallet_address: str) -> Dict[str, Any]:
    """
    Comprehensive analysis of a creator wallet.
    
    Combines age, prior launches, and behavioral patterns.
    """
    summary = get_creator_history_summary(wallet_address, max_pages=5)
    age = summary["wallet_age_days"]

    return {
        "wallet_address": wallet_address,
        "wallet_age_days": age,
        "prior_launch_count": summary["prior_launch_count"],
        "abandoned_tokens": summary["abandoned_tokens"],
        "days_since_last_launch": summary["days_since_last_launch"],
        "is_new_wallet": age < 7,
        "has_rug_history": len(summary["abandoned_tokens"]) > 0,
    }
