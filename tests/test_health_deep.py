"""
Tests for deep health endpoint behavior.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))


def test_deep_health_without_probe_returns_checks(fastapi_test_client):
    """Default deep health should return component checks without live probes."""
    response = fastapi_test_client.get("/api/health/deep")

    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert payload["probe_enabled"] is False
    assert "checks" in payload
    assert "database" in payload["checks"]
    assert "helius" in payload["checks"]
    assert "birdeye" in payload["checks"]
    assert "bubblemaps" in payload["checks"]
    assert "llm" in payload["checks"]


def test_deep_health_probe_runs_live_checks(fastapi_test_client):
    """probe=true should execute live probes for supported dependencies."""
    with patch(
        "clients.helius_client.get_wallet_transaction_history",
        return_value=[],
    ):
        with patch(
            "clients.birdeye_client.get_token_overview",
            return_value={"symbol": "SOL"},
        ):
            response = fastapi_test_client.get("/api/health/deep?probe=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["probe_enabled"] is True
    checks = payload["checks"]
    assert checks["helius"]["live_probe"] == "ok"
    assert checks["birdeye"]["live_probe"] == "ok"
