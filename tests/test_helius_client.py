"""
Tests for Helius client accuracy safeguards.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from clients import helius_client  # noqa: E402


class _MockResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_get_wallet_age_days_paginates_to_older_history():
    """Wallet age should use paginated history, not first page only."""
    now = datetime.now(timezone.utc)
    page_1 = [
        {
            "signature": f"sig-{i}",
            "timestamp": int((now - timedelta(days=2)).timestamp()),
        }
        for i in range(99)
    ] + [
        {"signature": "sig-older-page-1", "timestamp": int((now - timedelta(days=5)).timestamp())},
    ]
    page_2 = [
        {"signature": "sig-3", "timestamp": int((now - timedelta(days=20)).timestamp())},
    ]

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        side_effect=[page_1, page_2],
    ):
        age_days = helius_client.get_wallet_age_days("wallet-1", max_pages=3)

    assert age_days >= 19


def test_get_previous_token_launches_uses_das_assets_by_creator():
    """Launch counts should come from DAS getAssetsByCreator pagination."""
    now = int(datetime.now(timezone.utc).timestamp())
    page_1 = {
        "items": [
            {"id": f"asset-{i}", "created_at": now - 86400 - i}
            for i in range(1000)
        ]
    }
    page_2 = {
        "items": [
            {"id": "asset-c", "created_at": now - 259200},
        ]
    }

    with patch.object(
        helius_client,
        "_make_rpc_request",
        side_effect=[page_1, page_2],
    ) as rpc_call:
        result = helius_client.get_previous_token_launches("wallet-1")

    assert result["prior_launch_count"] == 1000
    assert result["abandoned_tokens"] == []
    assert result["days_since_last_launch"] in (0, 1)
    assert rpc_call.call_count == 2


def test_creator_history_summary_uses_das_for_launch_counts():
    """Creator summary should use DAS-backed launch counts, not tx guessing."""
    txs = [
        {
            "signature": "sig-1",
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }
    ]
    launches = {
        "prior_launch_count": 5,
        "abandoned_tokens": [],
        "days_since_last_launch": 2,
    }

    with patch.object(
        helius_client, "get_wallet_transaction_history", return_value=txs
    ) as tx_history:
        with patch.object(
            helius_client, "get_previous_token_launches", return_value=launches
        ) as launch_lookup:
            result = helius_client.get_creator_history_summary("wallet-1")

    assert result["prior_launch_count"] == 5
    assert result["days_since_last_launch"] == 2
    assert tx_history.call_count == 1
    launch_lookup.assert_called_once_with("wallet-1")


def test_get_token_holders_does_not_fabricate_total_holder_count():
    """When top-holder list is truncated, total_holders should be unknown."""
    largest_accounts = {
        "result": {
            "value": [
                {"address": f"Holder{i}", "amount": str(1000 - i)}
                for i in range(20)
            ]
        }
    }
    token_supply = {
        "result": {"value": {"amount": "1000000", "decimals": 0}}
    }

    with patch.dict("os.environ", {"HELIUS_API_KEY": "test-key"}, clear=False):
        with patch(
            "clients.helius_client.request_with_retry",
            side_effect=[
                _MockResponse(largest_accounts),
                _MockResponse(token_supply),
            ],
        ):
            result = helius_client.get_token_holders("Mint111111111111111111111111111")

    assert result is not None
    assert result["sampled_holder_count"] == 20
    assert result["total_holders"] is None


def test_analyze_creator_wallet_fetches_history_once():
    """Creator summary should reuse a single paginated history fetch."""
    txs = [
        {
            "signature": "sig-1",
            "type": "CREATE_TOKEN",
            "feePayer": "wallet-1",
            "tokenTransfers": [{"mint": "MintA"}],
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }
    ]

    helius_client._CREATOR_HISTORY_CACHE.clear()

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        return_value=txs,
    ) as mock_history:
        result = helius_client.analyze_creator_wallet("wallet-1")

    assert result["prior_launch_count"] == 0
    assert mock_history.call_count == 1


def test_creator_history_summary_uses_cache_between_calls():
    """Creator history summary should hit API once within cache TTL."""
    txs = [
        {
            "signature": "sig-1",
            "type": "CREATE_TOKEN",
            "feePayer": "wallet-cache",
            "tokenTransfers": [{"mint": "MintA"}],
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }
    ]
    helius_client._CREATOR_HISTORY_CACHE.clear()

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        return_value=txs,
    ) as mock_history:
        first = helius_client.get_creator_history_summary("wallet-cache")
        second = helius_client.get_creator_history_summary("wallet-cache")

    assert first["wallet_age_days"] == second["wallet_age_days"]
    assert mock_history.call_count == 1


def test_check_wallet_clustering_detects_shared_funding_source():
    """Wallets funded by same source should be counted as clustered."""

    def _history(wallet: str, limit: int = 10, before=None):
        source = "SourceShared" if wallet in {"w1", "w2"} else "SourceOther"
        return [
            {
                "nativeTransfers": [
                    {"toUserAccount": wallet, "fromUserAccount": source}
                ]
            }
        ]

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        side_effect=_history,
    ):
        result = helius_client.check_wallet_clustering(["w1", "w2", "w3"])

    assert result["clustered_wallets"] == 2
    assert result["clustering_score"] == 0.67


def test_check_wallet_clustering_limits_analysis_to_top_ten_wallets():
    """Clustering analysis should only query the first 10 holder wallets."""
    wallets = [f"w{i}" for i in range(12)]

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        return_value=[
            {
                "nativeTransfers": [
                    {"toUserAccount": "placeholder", "fromUserAccount": "S1"}
                ]
            }
        ],
    ) as mock_history:
        helius_client.check_wallet_clustering(wallets)

    assert mock_history.call_count == 10
