"""
ScamHound Helius API Client
Deep on-chain analysis using creator wallet addresses
"""

import os
import requests
from typing import Optional, Dict, List, Any
import logging
from datetime import datetime, timezone
from collections import Counter

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


def get_wallet_transaction_history(wallet_address: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get recent transactions for a wallet.
    
    Returns list of transaction objects with type, timestamp, and token info.
    """
    result = _make_request(f"/addresses/{wallet_address}/transactions", params={"limit": limit})
    
    if result is None or not isinstance(result, list):
        return []
    
    return result


def get_wallet_age_days(wallet_address: str) -> int:
    """
    Get the age of a wallet in days since first transaction.
    
    Returns:
    - Age in days (0 if unknown, -1 if error)
    - HIGH RISK if creator wallet is less than 7 days old
    """
    # Get a larger sample to find the oldest transaction
    result = _make_request(f"/addresses/{wallet_address}/transactions", params={"limit": 100})
    
    if result is None or not isinstance(result, list) or len(result) == 0:
        return -1  # Unknown
    
    # Find the oldest transaction
    oldest_timestamp = None
    for tx in result:
        timestamp = tx.get("timestamp")
        if timestamp:
            tx_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            if oldest_timestamp is None or tx_time < oldest_timestamp:
                oldest_timestamp = tx_time
    
    if oldest_timestamp is None:
        return -1
    
    # Calculate age in days
    now = datetime.now(timezone.utc)
    age_days = (now - oldest_timestamp).days
    
    return max(0, age_days)


def get_previous_token_launches(wallet_address: str) -> Dict[str, Any]:
    """
    Identify prior token launches by the same wallet.
    
    Returns:
    - prior_launch_count: int
    - abandoned_tokens: list of token mints where liquidity was removed
    - days_since_last_launch: int (None if first launch)
    """
    transactions = get_wallet_transaction_history(wallet_address, limit=100)
    
    if not transactions:
        return {
            "prior_launch_count": 0,
            "abandoned_tokens": [],
            "days_since_last_launch": None
        }
    
    # Track token creation events and subsequent activity
    token_creations = []
    token_activity = Counter()
    
    for tx in transactions:
        # Look for token creation patterns (this is simplified)
        # In reality, you'd parse the transaction instructions
        tx_type = tx.get("type", "")
        
        # Check for token-related transactions
        token_transfers = tx.get("tokenTransfers", [])
        native_transfers = tx.get("nativeTransfers", [])
        
        # Simplified: look for large outgoing transfers that might indicate rug
        for transfer in native_transfers:
            from_address = transfer.get("fromUserAccount", "")
            to_address = transfer.get("toUserAccount", "")
            amount = transfer.get("amount", 0)
            
            if from_address == wallet_address and amount > 1000000000:  # > 1 SOL
                token_activity["large_outgoing"] += 1
        
        # Track unique tokens interacted with
        for t in token_transfers:
            mint = t.get("mint", "")
            if mint:
                token_activity[mint] += 1
    
    # Estimate prior launches (simplified heuristic)
    # In production, you'd use DAS API to get asset creations
    prior_launch_count = max(0, len([t for t in token_activity.keys() if t not in ["large_outgoing"]]) - 1)
    
    # Check for abandonment patterns (large outgoing transfers)
    abandoned = []
    if token_activity.get("large_outgoing", 0) > 2:
        abandoned.append("potential_rug_pattern")
    
    return {
        "prior_launch_count": prior_launch_count,
        "abandoned_tokens": abandoned,
        "days_since_last_launch": None  # Would need more detailed analysis
    }


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
            - 'total_holders': estimated total holder count
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
        
        # Estimate total holders (this is an approximation)
        # In reality, we'd need to query all token accounts, which is expensive
        total_holders = len(accounts) if len(accounts) < limit else limit * 2
        
        logger.info(f"[HELIUS] Holder analysis for {token_mint[:8]}...: "
                   f"top1={top1_pct:.1f}%, top5={top5_pct:.1f}%, top10={top10_pct:.1f}%, "
                   f"concentration={concentration_score}"
                   f"{' (pump.fun, bonding curve excluded)' if bonding_curve_excluded else ''}")
        
        return {
            "top_holders": top_holders,
            "total_holders": total_holders,
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
    age = get_wallet_age_days(wallet_address)
    prior = get_previous_token_launches(wallet_address)
    
    return {
        "wallet_address": wallet_address,
        "wallet_age_days": age,
        "prior_launch_count": prior["prior_launch_count"],
        "abandoned_tokens": prior["abandoned_tokens"],
        "days_since_last_launch": prior["days_since_last_launch"],
        "is_new_wallet": age < 7,
        "has_rug_history": len(prior["abandoned_tokens"]) > 0
    }