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


def test_soak_audit_endpoint_returns_summary(fastapi_test_client):
    """Soak audit endpoint should return monitoring summary payload."""
    summary = {
        "sample_size": 10,
        "requested_limit": 10,
        "unscored_count": 2,
        "fallback_count": 1,
        "retried_count": 3,
        "avg_llm_attempts": 1.4,
        "unknown_creator_wallet_count": 1,
        "unknown_wallet_age_count": 2,
        "unknown_token_age_claim_count": 1,
        "risk_level_breakdown": {"LOW": 3, "UNSCORED": 2},
        "score_source_breakdown": {"ai_anthropic": 8, "fallback": 2},
    }

    with patch(
        "engine.database.get_soak_audit_summary",
        return_value=summary,
    ) as audit_mock:
        response = fastapi_test_client.get("/api/soak/audit?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload == summary
    audit_mock.assert_called_once_with(limit=10)
