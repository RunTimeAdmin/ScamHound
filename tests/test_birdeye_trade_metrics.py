"""
Tests for Birdeye trade metric normalization.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from clients import birdeye_client  # noqa: E402


def test_get_trade_history_returns_two_sided_ratio_and_legacy_alias():
    """Trade metrics should expose both new ratio key and legacy alias."""
    mock_trades = [
        {"owner": "wallet_a", "side": "buy", "amountUsd": 200},
        {"owner": "wallet_a", "side": "sell", "amountUsd": 100},
        {"owner": "wallet_b", "side": "buy", "amountUsd": 50},
    ]

    with patch.object(birdeye_client, "_make_request", return_value={"data": mock_trades}):
        result = birdeye_client.get_trade_history("TestMint111")

    assert result is not None
    assert result["unique_trader_count"] == 2
    assert result["two_sided_trader_ratio"] == 0.5
    assert result["wash_trading_score"] == result["two_sided_trader_ratio"]
