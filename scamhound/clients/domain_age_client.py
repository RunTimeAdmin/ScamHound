"""
Domain age lookup helper for supporting scam-signal context.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import requests

from .retry import request_with_retry

logger = logging.getLogger(__name__)

RDAP_URL = "https://rdap.org/domain"


def _extract_domain(url: str) -> Optional[str]:
    """Extract normalized domain from a URL."""
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower().strip()
    if not host:
        return None
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if "." not in host:
        return None
    return host


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse common timestamp formats into UTC datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_registration_time(payload: Dict[str, Any]) -> Optional[datetime]:
    """Extract registration event timestamp from RDAP payload."""
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            action = str(event.get("eventAction") or "").lower()
            if action in {"registration", "registered"}:
                parsed = _parse_iso_timestamp(event.get("eventDate"))
                if parsed is not None:
                    return parsed
    return _parse_iso_timestamp(
        payload.get("creationDate") or payload.get("created")
    )


def lookup_domain_age(domain: str) -> Dict[str, Any]:
    """Lookup domain age in days using RDAP."""
    result = {
        "domain": domain,
        "checked": False,
        "age_days": None,
        "recently_registered": None,
    }
    if not domain:
        return result

    try:
        response = request_with_retry(
            requests.get,
            f"{RDAP_URL}/{domain}",
            timeout=20,
            headers={"Accept": "application/json"},
        )
        if response is None:
            return result
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("[DOMAIN] RDAP lookup failed for %s: %s", domain, exc)
        return result
    except ValueError:
        logger.warning("[DOMAIN] Invalid RDAP payload for %s", domain)
        return result

    registered_at = _extract_registration_time(payload)
    if registered_at is None:
        return result

    age_days = max(0, (datetime.now(timezone.utc) - registered_at).days)
    result.update(
        {
            "checked": True,
            "age_days": age_days,
            "recently_registered": age_days < 30,
        }
    )
    return result


def lookup_domain_age_from_urls(urls: Iterable[str]) -> Dict[str, Any]:
    """Lookup domain age for the first valid domain in URL list."""
    for url in urls or []:
        domain = _extract_domain(url)
        if domain:
            response = lookup_domain_age(domain)
            response["domain"] = domain
            return response
    return {
        "domain": None,
        "checked": False,
        "age_days": None,
        "recently_registered": None,
    }
