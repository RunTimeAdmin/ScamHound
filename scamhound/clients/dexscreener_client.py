"""
ScamHound DexScreener API Client
Supporting trust/warning signals from public pair metadata.
"""

import logging
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests

from .retry import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"

TRUST_LABELS = {
    "verified",
    "kyc",
    "doxxed",
    "trusted",
}

WARNING_LABELS = {
    "honeypot",
    "scam",
    "suspicious",
    "high-risk",
    "blacklist",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _normalize_labels(raw_labels: Any) -> List[str]:
    """Return normalized label strings from DexScreener payload."""
    if not isinstance(raw_labels, list):
        return []
    labels: List[str] = []
    for item in raw_labels:
        text = str(item or "").strip().lower()
        if text:
            labels.append(text)
    return labels


def get_token_trust_signals(token_mint: str) -> Dict[str, Any]:
    """
    Fetch trust/warning metadata for a token from DexScreener.

    Returns a stable shape even when API data is unavailable.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "has_pair": False,
        "pair_count": 0,
        "labels": [],
        "has_trust_badge": False,
        "has_warning_label": False,
        "warning_labels": [],
        "website_count": 0,
        "social_count": 0,
        "website_urls": [],
        "txns_m5_buys": 0,
        "txns_m5_sells": 0,
        "txns_m15_buys": 0,
        "txns_m15_sells": 0,
        "txns_h1_buys": 0,
        "txns_h1_sells": 0,
        "txns_h24_buys": 0,
        "txns_h24_sells": 0,
        "pair_created_at": None,
        "liquidity_usd": 0.0,
        "market_cap_usd": 0.0,
        "liquidity_to_mcap_ratio": 0.0,
    }

    url = f"{BASE_URL}/latest/dex/tokens/{token_mint}"
    try:
        response = request_with_retry(
            requests.get,
            url,
            timeout=20,
        )
        if response is None:
            return result
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning(
            "[DEXSCREENER] Request failed for %s: %s",
            token_mint[:8],
            exc,
        )
        return result
    except ValueError:
        logger.warning(
            "[DEXSCREENER] Invalid JSON for %s",
            token_mint[:8],
        )
        return result

    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        return result

    aggregated_labels = set()
    warning_labels = set()
    website_count = 0
    social_count = 0
    website_urls = set()
    trust_badge_detected = False
    warning_detected = False
    txns_m5_buys = 0
    txns_m5_sells = 0
    txns_m15_buys = 0
    txns_m15_sells = 0
    txns_h1_buys = 0
    txns_h1_sells = 0
    txns_h24_buys = 0
    txns_h24_sells = 0
    pair_created_at = None
    max_liquidity_usd = 0.0
    max_market_cap_usd = 0.0
    max_ratio = 0.0

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        labels = _normalize_labels(pair.get("labels", []))
        aggregated_labels.update(labels)
        trust_matches = set(labels).intersection(TRUST_LABELS)
        warning_matches = set(labels).intersection(WARNING_LABELS)
        if trust_matches:
            trust_badge_detected = True
        if warning_matches:
            warning_detected = True
            warning_labels.update(warning_matches)

        info = pair.get("info", {})
        if isinstance(info, dict):
            websites = info.get("websites", [])
            socials = info.get("socials", [])
            if isinstance(websites, list):
                website_count += len(websites)
                for website in websites:
                    if not isinstance(website, dict):
                        continue
                    candidate = str(website.get("url") or "").strip()
                    if not candidate:
                        continue
                    parsed = urlparse(candidate)
                    if parsed.scheme and parsed.netloc:
                        website_urls.add(candidate)
            if isinstance(socials, list):
                social_count += len(socials)

        txns = pair.get("txns", {})
        if isinstance(txns, dict):
            m5 = txns.get("m5", {})
            if isinstance(m5, dict):
                txns_m5_buys += int(m5.get("buys") or 0)
                txns_m5_sells += int(m5.get("sells") or 0)
            m15 = txns.get("m15", {})
            if isinstance(m15, dict):
                txns_m15_buys += int(m15.get("buys") or 0)
                txns_m15_sells += int(m15.get("sells") or 0)
            h1 = txns.get("h1", {})
            if isinstance(h1, dict):
                txns_h1_buys += int(h1.get("buys") or 0)
                txns_h1_sells += int(h1.get("sells") or 0)
            h24 = txns.get("h24", {})
            if isinstance(h24, dict):
                txns_h24_buys += int(h24.get("buys") or 0)
                txns_h24_sells += int(h24.get("sells") or 0)

        created_at = pair.get("pairCreatedAt")
        if isinstance(created_at, (int, float)):
            normalized = int(created_at)
            if pair_created_at is None or normalized < int(pair_created_at):
                pair_created_at = normalized

        pair_liquidity_usd = 0.0
        liquidity = pair.get("liquidity", {})
        if isinstance(liquidity, dict):
            pair_liquidity_usd = _safe_float(liquidity.get("usd"))
            max_liquidity_usd = max(max_liquidity_usd, pair_liquidity_usd)
        market_cap = _safe_float(pair.get("marketCap"))
        fdv = _safe_float(pair.get("fdv"))
        cap = market_cap if market_cap > 0 else fdv
        max_market_cap_usd = max(max_market_cap_usd, cap)
        if cap > 0 and pair_liquidity_usd > 0:
            max_ratio = max(max_ratio, pair_liquidity_usd / cap)

    result.update(
        {
            "checked": True,
            "has_pair": len(pairs) > 0,
            "pair_count": len(pairs),
            "labels": sorted(aggregated_labels),
            "has_trust_badge": trust_badge_detected,
            "has_warning_label": warning_detected,
            "warning_labels": sorted(warning_labels),
            "website_count": website_count,
            "social_count": social_count,
            "website_urls": sorted(website_urls),
            "txns_m5_buys": txns_m5_buys,
            "txns_m5_sells": txns_m5_sells,
            "txns_m15_buys": txns_m15_buys,
            "txns_m15_sells": txns_m15_sells,
            "txns_h1_buys": txns_h1_buys,
            "txns_h1_sells": txns_h1_sells,
            "txns_h24_buys": txns_h24_buys,
            "txns_h24_sells": txns_h24_sells,
            "pair_created_at": pair_created_at,
            "liquidity_usd": max_liquidity_usd,
            "market_cap_usd": max_market_cap_usd,
            "liquidity_to_mcap_ratio": round(max_ratio, 4),
        }
    )
    return result
