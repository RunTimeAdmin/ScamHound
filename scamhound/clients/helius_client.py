"""
ScamHound Helius API Client
Deep on-chain analysis using creator wallet addresses
"""

import os
import requests
from typing import Optional, Dict, List, Any
import logging
import concurrent.futures
from datetime import datetime, timezone
import statistics

from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.helius.xyz/v0"
RPC_URL = "https://mainnet.helius-rpc.com"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Legitimate compliance tokens that intentionally keep freeze authority.
FREEZE_AUTHORITY_MINT_WHITELIST = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoA9Pp5h5hN9fQf4yKs",  # USDT
}

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

LP_BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}

LP_LOCK_PROVIDER_ADDRESSES = {
    # Common Solana lock destinations/providers.
    "HfoTxFR1Tm6kGxU9r9kUS3X5L5qQkNoP9f8uQfJ4Yf2h": "streamflow",
    "Timelock11111111111111111111111111111111111": "teamfinance_like",
    "UniCrypt11111111111111111111111111111111111": "unicrypt_like",
}

_CREATOR_HISTORY_CACHE: Dict[str, Dict[str, Any]] = {}
_CREATOR_HISTORY_CACHE_TTL_SECONDS = int(
    os.environ.get("HELIUS_CREATOR_CACHE_TTL_SECONDS", "600")
)


def _classify_holder_concentration(top1_pct: float, top10_pct: float) -> str:
    """
    Classify holder concentration using both top-1 and top-10 distribution.

    We keep top-1 as a strong signal for whale risk but also guard against
    misleadingly "low" labels when aggregate top-10 concentration is elevated.
    """
    if top1_pct > 50 or top10_pct > 80:
        return "critical"
    if top1_pct > 30 or top10_pct > 60:
        return "high"
    if top1_pct > 15 or top10_pct >= 35:
        return "moderate"
    return "low"


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


def _make_rpc_request(method: str, params: Any) -> Optional[Dict[str, Any]]:
    """Make a JSON-RPC request against the Helius mainnet RPC endpoint."""
    api_key = os.environ.get("HELIUS_API_KEY", "")
    if not api_key:
        logger.error("[HELIUS] API key not configured")
        return None

    url = f"{RPC_URL}/?api-key={api_key}"
    payload = {
        "jsonrpc": "2.0",
        "id": f"helius-{method}",
        "method": method,
        "params": params,
    }
    try:
        response = request_with_retry(
            requests.post, url, json=payload, timeout=30
        )
        response.raise_for_status()
        body = response.json()
    except requests.exceptions.RequestException as exc:
        logger.error(f"[HELIUS] RPC request failed ({method}): {exc}")
        return None

    if "error" in body:
        logger.error(f"[HELIUS] RPC error ({method}): {body['error']}")
        return None
    return body.get("result") if isinstance(body, dict) else None


def _extract_token_extensions(parsed_info: Dict[str, Any]) -> List[str]:
    """Extract Token-2022 extension names from parsed mint account payload."""
    extensions: List[str] = []
    candidates = [
        parsed_info.get("extensions"),
        parsed_info.get("extensionTypes"),
        parsed_info.get("tlvData"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            for entry in candidate:
                if isinstance(entry, str):
                    name = entry.strip()
                elif isinstance(entry, dict):
                    name = (
                        entry.get("extension")
                        or entry.get("type")
                        or entry.get("name")
                        or ""
                    )
                    name = str(name).strip()
                else:
                    name = ""
                if name and name not in extensions:
                    extensions.append(name)
    return extensions


def _extract_transfer_fee_config(parsed_info: Dict[str, Any]) -> Dict[str, Any]:
    """Extract Token-2022 transfer fee config if present."""
    config = {"transfer_fee_bps": None, "transfer_fee_max": None}
    extension_blocks = parsed_info.get("extensions")
    if not isinstance(extension_blocks, list):
        return config

    for entry in extension_blocks:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("extension") or entry.get("type") or "").lower()
        if "transferfee" not in name:
            continue

        state = entry.get("state") if isinstance(entry.get("state"), dict) else entry
        bps = (
            state.get("transferFeeBasisPoints")
            or state.get("transfer_fee_basis_points")
            or state.get("basisPoints")
            or state.get("bps")
        )
        max_fee = (
            state.get("maximumFee")
            or state.get("maximum_fee")
            or state.get("maxFee")
            or state.get("max_fee")
        )
        try:
            config["transfer_fee_bps"] = int(float(bps))
        except (TypeError, ValueError):
            config["transfer_fee_bps"] = None
        try:
            config["transfer_fee_max"] = float(max_fee)
        except (TypeError, ValueError):
            config["transfer_fee_max"] = None
        return config

    return config


def _extract_permanent_delegate(parsed_info: Dict[str, Any]) -> Optional[str]:
    """Extract Token-2022 permanent delegate address when configured."""
    extension_blocks = parsed_info.get("extensions")
    if not isinstance(extension_blocks, list):
        return None

    for entry in extension_blocks:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("extension") or entry.get("type") or "").lower()
        if "permanentdelegate" not in name:
            continue
        state = entry.get("state") if isinstance(entry.get("state"), dict) else entry
        delegate = (
            state.get("delegate")
            or state.get("permanentDelegate")
            or state.get("permanent_delegate")
        )
        return str(delegate).strip() if delegate else None
    return None


def _get_update_authority(token_mint: str) -> Optional[str]:
    """Fetch update authority from asset metadata when available."""
    asset = _make_rpc_request("getAsset", {"id": token_mint})
    if not isinstance(asset, dict):
        return None

    metadata = asset.get("content", {}).get("metadata", {})
    if isinstance(metadata, dict):
        candidate = (
            metadata.get("updateAuthority")
            or metadata.get("update_authority")
        )
        if candidate:
            return str(candidate)

    authorities = asset.get("authorities")
    if isinstance(authorities, list):
        for entry in authorities:
            if not isinstance(entry, dict):
                continue
            scope = str(entry.get("scope") or "").lower()
            if scope in {"full", "metadata", "update"}:
                address = entry.get("address")
                if address:
                    return str(address)
    return None


def get_token_security_signals(token_mint: str) -> Optional[Dict[str, Any]]:
    """
    Fetch on-chain token control signals from mint account metadata.

    Returns mint/freeze authority state and Token-2022 extension footprint.
    """
    result = _make_rpc_request(
        "getAccountInfo",
        [token_mint, {"encoding": "jsonParsed"}],
    )
    value = (result or {}).get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict):
        return None

    owner_program = str(value.get("owner") or "")
    data = value.get("data")
    if not isinstance(data, dict):
        return None
    parsed = data.get("parsed")
    if not isinstance(parsed, dict):
        return None
    parsed_info = parsed.get("info")
    if not isinstance(parsed_info, dict):
        return None

    mint_authority = parsed_info.get("mintAuthority")
    freeze_authority = parsed_info.get("freezeAuthority")
    token_extensions = _extract_token_extensions(parsed_info)
    transfer_fee = _extract_transfer_fee_config(parsed_info)
    permanent_delegate = _extract_permanent_delegate(parsed_info)
    update_authority = _get_update_authority(token_mint)
    is_token_2022 = (
        owner_program == TOKEN_2022_PROGRAM_ID
        or str(parsed.get("type") or "").lower() == "mint2022"
        or bool(token_extensions)
    )
    freeze_whitelisted = token_mint in FREEZE_AUTHORITY_MINT_WHITELIST

    return {
        "mint_authority": mint_authority,
        "mint_authority_renounced": mint_authority in (None, ""),
        "freeze_authority": freeze_authority,
        "freeze_authority_renounced": freeze_authority in (None, ""),
        "freeze_authority_whitelisted": freeze_whitelisted,
        "freeze_authority_high_risk": bool(
            freeze_authority not in (None, "") and not freeze_whitelisted
        ),
        "update_authority": update_authority,
        "token_program_owner": owner_program,
        "is_token_2022": is_token_2022,
        "token_2022_extensions": token_extensions,
        "transfer_fee_bps": transfer_fee["transfer_fee_bps"],
        "transfer_fee_max": transfer_fee["transfer_fee_max"],
        "permanent_delegate": permanent_delegate,
        "uses_standard_token_program": owner_program == TOKEN_PROGRAM_ID,
    }


def analyze_lp_token_controls(
    lp_mint: str,
    creator_wallet: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze LP token holder distribution for burn/lock/creator control.

    This is an approximation using known burn and locker addresses.
    """
    holders = get_token_holders(lp_mint, limit=30)
    if not holders:
        return {
            "checked": False,
            "lp_locked": None,
            "lp_burned": None,
            "lp_unlocked_creator_controlled": None,
            "lp_burned_share_pct": None,
            "lp_locked_share_pct": None,
            "lp_creator_share_pct": None,
        }

    top_holders = holders.get("top_holders", [])
    burned_share = 0.0
    locked_share = 0.0
    creator_share = 0.0

    for holder in top_holders:
        address = str(holder.get("address") or "")
        pct = float(holder.get("percentage") or 0.0)
        if address in LP_BURN_ADDRESSES:
            burned_share += pct
        if address in LP_LOCK_PROVIDER_ADDRESSES:
            locked_share += pct
        if creator_wallet and address == creator_wallet:
            creator_share += pct

    lp_burned = burned_share >= 90.0
    lp_locked = locked_share >= 50.0 or lp_burned
    lp_unlocked_creator_controlled = (
        creator_share >= 50.0 and not lp_locked and burned_share < 10.0
    )

    return {
        "checked": True,
        "lp_locked": lp_locked,
        "lp_burned": lp_burned,
        "lp_unlocked_creator_controlled": lp_unlocked_creator_controlled,
        "lp_burned_share_pct": round(burned_share, 2),
        "lp_locked_share_pct": round(locked_share, 2),
        "lp_creator_share_pct": round(creator_share, 2),
    }


def analyze_bundle_launch(
    creator_wallet: Optional[str],
    recent_buy_wallets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Detect likely coordinated bundle/snipe launch behavior.

    Signals:
    - clustered buys in same/near slots or tight time window
    - similar buy sizes
    - buyer wallets funded by creator shortly before launch
    """
    buys = [b for b in (recent_buy_wallets or []) if isinstance(b, dict)]
    if not creator_wallet or len(buys) < 5:
        return {
            "checked": False,
            "bundle_launch_suspected": False,
            "bundle_same_slot_or_window": False,
            "bundle_amount_clustered": False,
            "bundle_funded_by_creator_count": 0,
            "bundle_buy_wallet_count": len(buys),
            "bundle_triggered_signals": 0,
        }

    first_buys = buys[:10]
    slots = [b.get("slot") for b in first_buys if isinstance(b.get("slot"), int)]
    timestamps = [
        b.get("timestamp")
        for b in first_buys
        if isinstance(b.get("timestamp"), int)
    ]
    amounts = [
        float(b.get("amount_usd") or 0.0)
        for b in first_buys
        if float(b.get("amount_usd") or 0.0) > 0
    ]

    slot_cluster = len(slots) >= 5 and (max(slots) - min(slots) <= 5)
    time_cluster = (
        len(timestamps) >= 5 and (max(timestamps) - min(timestamps) <= 120)
    )
    same_slot_or_window = slot_cluster or time_cluster

    amount_clustered = False
    if len(amounts) >= 5:
        mean_amount = statistics.fmean(amounts)
        if mean_amount > 0:
            stdev = statistics.pstdev(amounts)
            amount_clustered = (stdev / mean_amount) <= 0.35

    earliest_buy_ts = min(timestamps) if timestamps else None
    funded_by_creator_count = 0
    seen_wallets = set()
    for buy in first_buys:
        wallet = str(buy.get("wallet") or "").strip()
        if not wallet or wallet in seen_wallets:
            continue
        seen_wallets.add(wallet)

        txs = get_wallet_transaction_history(wallet, limit=30)
        for tx in txs:
            tx_ts = tx.get("timestamp")
            native = tx.get("nativeTransfers", [])
            if not isinstance(native, list):
                continue
            matched = False
            for transfer in native:
                if not isinstance(transfer, dict):
                    continue
                if transfer.get("toUserAccount") != wallet:
                    continue
                if transfer.get("fromUserAccount") != creator_wallet:
                    continue
                if (
                    isinstance(tx_ts, int)
                    and earliest_buy_ts is not None
                    and 0 <= earliest_buy_ts - tx_ts <= 3600
                ):
                    matched = True
                    break
            if matched:
                funded_by_creator_count += 1
                break

    funded_signal = funded_by_creator_count >= 3
    triggered = sum([same_slot_or_window, amount_clustered, funded_signal])
    suspected = triggered >= 2

    return {
        "checked": True,
        "bundle_launch_suspected": suspected,
        "bundle_same_slot_or_window": same_slot_or_window,
        "bundle_amount_clustered": amount_clustered,
        "bundle_funded_by_creator_count": funded_by_creator_count,
        "bundle_buy_wallet_count": len(first_buys),
        "bundle_triggered_signals": triggered,
    }


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
    wallet_address: str, max_pages: int = 20
) -> Dict[str, Any]:
    """Get creator age + launch history from one transaction fetch (cached)."""
    now = datetime.now(timezone.utc).timestamp()
    cached = _CREATOR_HISTORY_CACHE.get(wallet_address)
    if cached and now - cached["cached_at"] < _CREATOR_HISTORY_CACHE_TTL_SECONDS:
        return dict(cached["data"])

    transactions = _fetch_creator_transactions(wallet_address, max_pages=max_pages)
    age_days = _derive_wallet_age_days(transactions)
    launches = get_previous_token_launches(wallet_address)
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


def get_wallet_age_days(wallet_address: str, max_pages: int = 20) -> int:
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
    page = 1
    limit = 1000
    total_assets = 0
    newest_timestamp: Optional[int] = None

    while True:
        result = _make_rpc_request(
            "getAssetsByCreator",
            {
                "creatorAddress": wallet_address,
                "page": page,
                "limit": limit,
            },
        )
        if not result:
            break

        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list) or not items:
            break

        total_assets += len(items)
        for asset in items:
            if not isinstance(asset, dict):
                continue
            created_at = asset.get("created_at")
            if isinstance(created_at, int):
                if newest_timestamp is None or created_at > newest_timestamp:
                    newest_timestamp = created_at

        if len(items) < limit:
            break
        page += 1

    days_since_last_launch: Optional[int] = None
    if newest_timestamp is not None:
        launch_dt = datetime.fromtimestamp(newest_timestamp, tz=timezone.utc)
        days_since_last_launch = max(0, (datetime.now(timezone.utc) - launch_dt).days)

    return {
        "prior_launch_count": max(0, total_assets - 1),
        "abandoned_tokens": [],
        "days_since_last_launch": days_since_last_launch,
    }


def _find_wallet_funding_source(wallet: str) -> Optional[str]:
    """Return earliest observed incoming funding source for a wallet."""
    transactions = get_wallet_transaction_history(wallet, limit=10)
    if not transactions:
        return None

    for tx in reversed(transactions):
        native_transfers = tx.get("nativeTransfers", [])
        for transfer in native_transfers:
            if transfer.get("toUserAccount") == wallet:
                source = transfer.get("fromUserAccount", "")
                if source:
                    return source
    return None


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
    funding_sources: Dict[str, List[str]] = {}
    analyzed_wallets = holder_wallets[:10]  # Limit to top 10

    max_workers = min(8, len(analyzed_wallets))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_wallet = {
            executor.submit(_find_wallet_funding_source, wallet): wallet
            for wallet in analyzed_wallets
        }
        for future in concurrent.futures.as_completed(future_to_wallet):
            wallet = future_to_wallet[future]
            try:
                source = future.result()
            except Exception:
                source = None
            if source:
                if source not in funding_sources:
                    funding_sources[source] = []
                funding_sources[source].append(wallet)
    
    # Find clusters (multiple wallets funded from same source)
    max_cluster = 0
    total_clustered = 0
    
    for source, wallets in funding_sources.items():
        if len(wallets) > 1:
            total_clustered += len(wallets)
            max_cluster = max(max_cluster, len(wallets))
    
    # Calculate clustering score
    total_analyzed = len(analyzed_wallets)
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
        
        # Classify concentration from both whale and aggregate distribution.
        concentration_score = _classify_holder_concentration(
            top1_pct=top1_pct,
            top10_pct=top10_pct,
        )
        
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
    summary = get_creator_history_summary(wallet_address)
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
