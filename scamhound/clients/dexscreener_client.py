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
        }
    )
    return result
