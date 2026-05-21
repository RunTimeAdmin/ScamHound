"""
Tests for Birdeye liquidity field normalization.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from clients import birdeye_client  # noqa: E402


def test_get_token_overview_prefers_liquidity_usd_field():
    """Overview should prioritize liquidityUsd over generic liquidity."""
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


def test_get_full_market_data_uses_fresh_overview_when_requested():
    """Fresh market reads should bypass overview cache."""
    with patch.object(
        birdeye_client,
        "get_token_overview",
        return_value={"marketcap": 10, "liquidity": 1},
    ) as mock_overview:
        with patch.object(
            birdeye_client, "get_liquidity_data", return_value={}
        ):
            with patch.object(
                birdeye_client, "get_trade_history", return_value={}
            ):
                birdeye_client.get_full_market_data("mint", fresh=True)

    mock_overview.assert_called_once_with("mint", use_cache=False)


def test_get_trade_history_flags_honeypot_like_flow():
    """Buy-heavy flow without sells should raise heuristic honeypot signal."""
    payload = {
        "data": [
            {"owner": "w1", "side": "buy", "amountUsd": 100},
            {"owner": "w2", "side": "buy", "amountUsd": 120},
            {"owner": "w3", "side": "buy", "amountUsd": 80},
            {"owner": "w4", "side": "buy", "amountUsd": 50},
            {"owner": "w5", "side": "buy", "amountUsd": 90},
            {"owner": "w6", "side": "buy", "amountUsd": 70},
            {"owner": "w7", "side": "buy", "amountUsd": 60},
            {"owner": "w8", "side": "buy", "amountUsd": 75},
        ]
    }
    with patch.object(birdeye_client, "_make_request", return_value=payload):
        trades = birdeye_client.get_trade_history("mint")

    assert trades is not None
    assert trades["buy_count"] == 8
    assert trades["sell_count"] == 0
    assert trades["honeypot_suspected"] is True


def test_get_trade_history_detects_wash_trade_cycles():
    """Round-trip A<->B transfers in short windows flag wash cycles."""
    payload = {
        "data": [
            {
                "fromOwner": "A",
                "toOwner": "B",
                "amountUsd": 100,
                "timestamp": 1000,
            },
            {
                "fromOwner": "B",
                "toOwner": "A",
                "amountUsd": 95,
                "timestamp": 1100,
            },
            {
                "fromOwner": "C",
                "toOwner": "D",
                "amountUsd": 200,
                "timestamp": 1200,
            },
            {
                "fromOwner": "D",
                "toOwner": "C",
                "amountUsd": 185,
                "timestamp": 1250,
            },
        ]
    }
    with patch.object(birdeye_client, "_make_request", return_value=payload):
        trades = birdeye_client.get_trade_history("mint")

    assert trades is not None
    assert trades["wash_trade_cycle_count"] >= 2
    assert trades["wash_trade_suspected"] is True


def test_get_trade_history_flags_holder_velocity_spike():
    """Buyer velocity should spike when recent buyers sharply accelerate."""
    now = 20000
    payload = {
        "data": []
    }
    for idx in range(25):
        payload["data"].append(
            {
                "owner": f"new{idx}",
                "side": "buy",
                "amountUsd": 25,
                "timestamp": now - 300,
            }
        )
    for idx in range(5):
        payload["data"].append(
            {
                "owner": f"old{idx}",
                "side": "buy",
                "amountUsd": 20,
                "timestamp": now - 4500,
            }
        )

    with patch.object(birdeye_client, "_make_request", return_value=payload):
        trades = birdeye_client.get_trade_history("mint")

    assert trades is not None
    assert trades["unique_buyers_last_hour"] >= 20
    assert trades["unique_buyers_prev_hour"] <= 10
    assert trades["holder_velocity_spike"] is True
    assert trades["holder_velocity_band"] in {"high", "explosive"}
    assert trades["unique_buyers_last_15m"] >= 12
