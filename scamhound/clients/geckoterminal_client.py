"""
ScamHound GeckoTerminal API client.
Public fallback market/pool signals for Solana tokens.
"""

import logging
from typing import Any, Dict

import requests

from .retry import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.geckoterminal.com/api/v2"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def get_token_market_fallback(token_mint: str) -> Dict[str, Any]:
    """
    Fetch public fallback market signals from GeckoTerminal.

    Returns a stable dict even when API data is unavailable.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "pool_found": False,
        "liquidity_usd": 0.0,
        "market_cap_usd": 0.0,
        "liquidity_to_mcap_ratio": 0.0,
        "volume_24h_usd": 0.0,
        "pool_created_at": None,
        "txns_h24_buys": 0,
        "txns_h24_sells": 0,
    }

    url = f"{BASE_URL}/networks/solana/tokens/{token_mint}/pools"
    try:
        response = request_with_retry(
            requests.get,
            url,
            timeout=20,
            headers={"Accept": "application/json"},
        )
        if response is None:
            return result
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning(
            "[GECKOTERMINAL] Request failed for %s: %s",
            token_mint[:8],
            exc,
        )
        return result
    except ValueError:
        logger.warning(
            "[GECKOTERMINAL] Invalid JSON for %s",
            token_mint[:8],
        )
        return result

    pools = payload.get("data")
    if not isinstance(pools, list) or not pools:
        return result

    best = pools[0]
    if not isinstance(best, dict):
        return result
    attrs = best.get("attributes", {})
    if not isinstance(attrs, dict):
        return result

    liquidity_usd = _safe_float(attrs.get("reserve_in_usd"))
    market_cap_usd = _safe_float(
        attrs.get("market_cap_usd") or attrs.get("fdv_usd")
    )
    ratio = 0.0
    if liquidity_usd > 0 and market_cap_usd > 0:
        ratio = liquidity_usd / market_cap_usd

    volume = attrs.get("volume_usd", {})
    txns = attrs.get("transactions", {})
    h24_volume = 0.0
    if isinstance(volume, dict):
        h24_volume = _safe_float(volume.get("h24"))
    h24_buys = 0
    h24_sells = 0
    if isinstance(txns, dict):
        h24 = txns.get("h24", {})
        if isinstance(h24, dict):
            h24_buys = int(h24.get("buys") or 0)
            h24_sells = int(h24.get("sells") or 0)

    result.update(
        {
            "checked": True,
            "pool_found": True,
            "liquidity_usd": liquidity_usd,
            "market_cap_usd": market_cap_usd,
            "liquidity_to_mcap_ratio": round(ratio, 4),
            "volume_24h_usd": h24_volume,
            "pool_created_at": attrs.get("pool_created_at"),
            "txns_h24_buys": h24_buys,
            "txns_h24_sells": h24_sells,
        }
    )
    return result
