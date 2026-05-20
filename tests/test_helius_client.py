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


def test_get_previous_token_launches_ignores_transfer_only_activity():
    """Token transfers alone should not be treated as creator launches."""
    transfer_only = [
        {
            "signature": "sig-a",
            "type": "TRANSFER",
            "feePayer": "wallet-1",
            "tokenTransfers": [{"mint": "MintTouchedOnly"}],
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        }
    ]

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        side_effect=[transfer_only],
    ):
        result = helius_client.get_previous_token_launches("wallet-1")

    assert result["prior_launch_count"] == 0
    assert result["abandoned_tokens"] == []


def test_get_previous_token_launches_counts_only_creator_create_events():
    """Only create-like transactions from the wallet should count as launches."""
    now = datetime.now(timezone.utc)
    create_events = [
        {
            "signature": "sig-1",
            "type": "CREATE_TOKEN",
            "feePayer": "wallet-1",
            "tokenTransfers": [{"mint": "MintA"}],
            "timestamp": int((now - timedelta(days=1)).timestamp()),
        },
        {
            "signature": "sig-2",
            "type": "CREATE_MINT",
            "feePayer": "wallet-1",
            "tokenTransfers": [{"mint": "MintB"}],
            "timestamp": int((now - timedelta(days=2)).timestamp()),
        },
        {
            "signature": "sig-3",
            "type": "CREATE_TOKEN",
            "feePayer": "other-wallet",
            "tokenTransfers": [{"mint": "MintC"}],
            "timestamp": int((now - timedelta(days=3)).timestamp()),
        },
    ]

    with patch.object(
        helius_client,
        "get_wallet_transaction_history",
        side_effect=[create_events],
    ):
        result = helius_client.get_previous_token_launches("wallet-1")

    assert result["prior_launch_count"] == 1
    assert result["days_since_last_launch"] in (0, 1)


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
