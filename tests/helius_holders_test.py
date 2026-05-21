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
    assert result["concentration_score"] == "moderate"
