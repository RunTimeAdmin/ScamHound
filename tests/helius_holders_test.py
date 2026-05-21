"""
Tests for Helius holder concentration scoring.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from clients import helius_client  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_get_token_holders_uses_top10_in_concentration_score(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "test-key")

    holders_payload = {
        "result": {
            "value": [
                {"address": "holder1", "amount": "140"},
                {"address": "holder2", "amount": "70"},
                {"address": "holder3", "amount": "60"},
                {"address": "holder4", "amount": "50"},
                {"address": "holder5", "amount": "79.2"},
            ]
        }
    }
    supply_payload = {"result": {"value": {"amount": "1000", "decimals": 0}}}

    calls = {"count": 0}

    def fake_request_with_retry(func, url, json, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResponse(holders_payload)
        if calls["count"] == 2:
            return _FakeResponse(supply_payload)
        raise AssertionError("Unexpected extra RPC call")

    monkeypatch.setattr(
        helius_client, "request_with_retry", fake_request_with_retry
    )

    result = helius_client.get_token_holders(
        "Mint11111111111111111111111111111111"
    )

    assert result is not None
    assert result["top1_pct"] == 14.0
    assert result["top10_pct"] == 39.92
    assert result["top20_pct"] == 39.92
    assert result["concentration_score"] == "high"


def test_check_wallet_clustering_flags_large_same_source_cluster(monkeypatch):
    top_holders = [
        {"address": "w1", "percentage": 9.0},
        {"address": "w2", "percentage": 8.0},
        {"address": "w3", "percentage": 7.0},
        {"address": "w4", "percentage": 3.0},
    ]
    funding_map = {
        "w1": "creator",
        "w2": "creator",
        "w3": "creator",
        "w4": "other",
    }

    def fake_funding_source(wallet):
        return funding_map.get(wallet)

    monkeypatch.setattr(
        helius_client,
        "_find_wallet_funding_source",
        fake_funding_source,
    )

    result = helius_client.check_wallet_clustering(
        top_holders,
        creator_wallet="creator",
    )

    assert result["largest_funding_cluster_wallets"] == 3
    assert result["largest_funding_cluster_supply_pct"] == 24.0
    assert result["creator_funded_cluster_supply_pct"] == 24.0
    assert result["genesis_cluster_suspected"] is True
