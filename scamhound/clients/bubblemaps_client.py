"""
ScamHound BubbleMaps API Client
Token holder clustering and decentralization analysis using the new beta API.
"""

import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, List

import os
from .retry import request_with_retry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://api-legacy.bubblemaps.io"

# Supported chains
SUPPORTED_CHAINS = [
    "eth", "base", "solana", "tron", "bsc",
    "apechain", "ton", "polygon", "avalanche",
    "sonic", "monad", "aptos"
]

# Daily quota tracking
_daily_request_count = 0
_daily_quota_exhausted = False
_quota_reset_date = None


def _check_quota():
    """Check if daily quota is exhausted. Resets at UTC midnight."""
    global _daily_quota_exhausted, _quota_reset_date, _daily_request_count
    today = datetime.now(timezone.utc).date()
    if _quota_reset_date != today:
        _daily_quota_exhausted = False
        _daily_request_count = 0
        _quota_reset_date = today
    return not _daily_quota_exhausted


def _handle_429():
    """Mark quota as exhausted for the day."""
    global _daily_quota_exhausted
    _daily_quota_exhausted = True
    logger.warning("BubbleMaps daily quota exhausted. Pausing until UTC midnight.")


def get_quota_status():
    """Return quota status for health endpoint."""
    _check_quota()
    return {
        "requests_today": _daily_request_count,
        "quota_exhausted": _daily_quota_exhausted,
        "reset_date": str(_quota_reset_date) if _quota_reset_date else None
    }


def _make_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Make an authenticated request to the BubbleMaps API."""
    global _daily_request_count

    api_key = os.environ.get("BUBBLEMAPS_API_KEY", "")
    if not api_key:
        logger.info("BubbleMaps API key not configured")
        return None

    if not _check_quota():
        logger.warning("BubbleMaps daily quota exhausted, skipping request")
        return None

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{BASE_URL}{endpoint}"

    try:
        # Use retry but with max_retries=1 since 429 means daily quota, not temp limit
        response = request_with_retry(
            requests.get, url,
            headers=headers, params=params, timeout=30,
            max_retries=1
        )

        if response and response.status_code == 429:
            _handle_429()
            return None

        if response and response.status_code == 200:
            _daily_request_count += 1
            return response.json()

        if response:
            logger.warning(f"BubbleMaps API returned {response.status_code}")
        return None

    except Exception as e:
        logger.error(f"BubbleMaps API error: {e}")
        return None


def get_supported_chains() -> List[str]:
    """Return list of supported blockchain chains."""
    return SUPPORTED_CHAINS.copy()


def get_cluster_analysis(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Get cluster analysis for a token using the new BubbleMaps API.
    Returns structured cluster data with native decentralization score.

    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)

    Returns:
        Cluster analysis data including:
        - decentralization_score (0-100, higher = more decentralized)
        - cluster_count (number of distinct clusters)
        - largest_cluster_share (percentage held by largest cluster)
        - clusters (list of cluster details)
    """
    if chain not in SUPPORTED_CHAINS:
        logger.error(f"[BUBBLEMAPS] Unsupported chain: {chain}")
        return None

    params = {
        "return_clusters": "true",
        "return_decentralization_score": "true",
        "return_nodes": "false",
        "return_relationships": "false"
    }

    data = _make_request(f"/maps/{chain}/{token_address}", params=params)
    if not data:
        return None

    try:
        decentralization_score = data.get("decentralization_score")
        clusters = data.get("clusters", [])

        # Convert share from 0-1 to percentage
        largest_cluster_share = clusters[0]["share"] * 100 if clusters else 0

        return {
            "decentralization_score": round(decentralization_score * 100, 1) if decentralization_score else None,
            "cluster_count": len(clusters),
            "largest_cluster_share": round(largest_cluster_share, 1),
            "clusters": [
                {
                    "share_pct": round(c["share"] * 100, 1),
                    "holder_count": c.get("holder_count", 0),
                    "holders": c.get("holders", [])
                }
                for c in clusters[:10]  # Top 10 clusters
            ]
        }
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"Error parsing BubbleMaps cluster data: {e}")
        return None


def get_detailed_holders(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Get detailed holder info including address_details (CEX/DEX flags, labels, wallet age).
    Uses more quota — only call for token detail pages, not bulk scanning.

    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)

    Returns:
        Detailed holder analysis including:
        - decentralization_score
        - clusters
        - top_holders with address details (CEX/DEX flags, labels, etc.)
        - labeled_holder_count
        - cex_holder_count
    """
    if chain not in SUPPORTED_CHAINS:
        logger.error(f"[BUBBLEMAPS] Unsupported chain: {chain}")
        return None

    params = {
        "return_clusters": "true",
        "return_decentralization_score": "true",
        "return_nodes": "true",
        "return_relationships": "false"
    }

    data = _make_request(f"/maps/{chain}/{token_address}", params=params)
    if not data:
        return None

    try:
        nodes = data.get("nodes", {})
        top_holders = nodes.get("top_holders", [])

        holders_detail = []
        labeled_count = 0
        cex_count = 0

        for holder in top_holders[:20]:
            details = holder.get("address_details", {})
            holder_data = holder.get("holder_data", {})

            is_labeled = bool(details.get("label"))
            is_cex = details.get("is_cex", False)
            is_dex = details.get("is_dex", False)

            if is_labeled:
                labeled_count += 1
            if is_cex:
                cex_count += 1

            holders_detail.append({
                "address": holder.get("address"),
                "share_pct": round(holder_data.get("share", 0) * 100, 2),
                "rank": holder_data.get("rank"),
                "is_cex": is_cex,
                "is_dex": is_dex,
                "is_contract": details.get("is_contract", False),
                "label": details.get("label"),
                "first_activity": details.get("first_activity_date"),
                "inward_relations": details.get("inward_relations", 0),
                "outward_relations": details.get("outward_relations", 0)
            })

        return {
            "decentralization_score": round(data.get("decentralization_score", 0) * 100, 1),
            "clusters": data.get("clusters", []),
            "top_holders": holders_detail,
            "labeled_holder_count": labeled_count,
            "cex_holder_count": cex_count,
            "total_holders_analyzed": len(holders_detail)
        }
    except Exception as e:
        logger.error(f"Error parsing BubbleMaps holder details: {e}")
        return None
