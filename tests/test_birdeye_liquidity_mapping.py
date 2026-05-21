"""
Tests for Birdeye liquidity field normalization.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from clients import birdeye_client  # noqa: E402


def test_get_token_overview_prefers_liquidity_usd_field():
    """Overview should prioritize explicit liquidityUsd over generic liquidity."""
    payload = {
        "data": {
            "price": 1.0,
            "marketCap": 1_000_000,
            "liquidity": 166_059,
            "liquidityUsd": 111_000,
        }
    }
    with patch.object(birdeye_client, "_make_request", return_value=payload):
        overview = birdeye_client.get_token_overview("mint")

    assert overview is not None
    assert overview["liquidity"] == 111_000


def test_get_liquidity_data_prefers_liquidity_usd_from_raw_payload():
    """Liquidity extraction should prioritize liquidityUsd for USD metrics."""
    payload = {
        "data": {
            "marketCap": 1_000_000,
            "liquidity": 166_059,
            "liquidityUsd": 111_000,
        }
    }
    with patch.object(birdeye_client, "_make_request", return_value=payload):
        liquidity = birdeye_client.get_liquidity_data("mint")

    assert liquidity is not None
    assert liquidity["liquidity_usd"] == 111_000.0
    assert liquidity["liquidity_to_mcap_ratio"] == 0.111


def test_get_token_overview_normalizes_creator_and_created_at_fields():
    """Overview should preserve creator and launch-time fields."""
    payload = {
        "data": {
            "marketCap": 500_000,
            "liquidityUsd": 50_000,
            "creatorWallet": "Creator111111111111111111111111111111111",
            "launchTime": 1715000000,
        }
    }
    with patch.object(birdeye_client, "_make_request", return_value=payload):
        overview = birdeye_client.get_token_overview("mint")

    assert overview is not None
    assert overview["creator_wallet"] == payload["data"]["creatorWallet"]
    assert overview["created_at"] == payload["data"]["launchTime"]
