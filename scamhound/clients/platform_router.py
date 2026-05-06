"""Platform router — unified interface for multi-platform token feed discovery."""

import logging
import os
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Platform registry
_PLATFORMS = {}


def register_platform(name: str, fetch_fn, is_configured_fn):
    """Register a platform feed source."""
    _PLATFORMS[name] = {
        "fetch": fetch_fn,
        "is_configured": is_configured_fn,
    }


def _init_platforms():
    """Initialize platform registry with available sources."""
    from clients import bags_client
    from clients import pumpfun_client

    # Bags.fm is always registered (core platform)
    register_platform(
        "bags",
        fetch_fn=bags_client.get_recent_launches,
        is_configured_fn=lambda: True,  # Always active
    )

    # pump.fun via PumpPortal (opt-in via env var)
    register_platform(
        "pumpfun",
        fetch_fn=pumpfun_client.get_recent_launches,
        is_configured_fn=pumpfun_client.is_configured,
    )


def get_active_platforms() -> List[str]:
    """Get list of currently active platform names."""
    if not _PLATFORMS:
        _init_platforms()
    return [name for name, cfg in _PLATFORMS.items() if cfg["is_configured"]()]


def get_recent_launches(limit: int = 25, platforms: List[str] = None) -> List[Dict[str, Any]]:
    """Fetch recent token launches from all active platforms (or specified ones).

    Args:
        limit: Max tokens per platform
        platforms: Optional list of platform names to query. If None, queries all active.

    Returns:
        Combined list of normalized token dicts from all queried platforms.
        Each token includes a 'platform' field indicating source.
    """
    if not _PLATFORMS:
        _init_platforms()

    active = platforms or get_active_platforms()
    all_tokens = []

    for platform_name in active:
        if platform_name not in _PLATFORMS:
            logger.warning(f"[ROUTER] Unknown platform: {platform_name}")
            continue

        cfg = _PLATFORMS[platform_name]
        if not cfg["is_configured"]():
            continue

        try:
            tokens = cfg["fetch"](limit=limit)
            # Ensure platform field is set
            for token in tokens:
                token.setdefault("platform", platform_name)
            all_tokens.extend(tokens)
            logger.info(f"[ROUTER] {platform_name}: {len(tokens)} tokens")
        except Exception as e:
            logger.error(f"[ROUTER] Error fetching from {platform_name}: {e}")
            continue

    logger.info(f"[ROUTER] Total: {len(all_tokens)} tokens from {len(active)} platform(s)")
    return all_tokens


def get_platform_status() -> Dict[str, Any]:
    """Get status of all registered platforms."""
    if not _PLATFORMS:
        _init_platforms()

    status = {}
    for name, cfg in _PLATFORMS.items():
        status[name] = {
            "registered": True,
            "active": cfg["is_configured"](),
        }
    return status
