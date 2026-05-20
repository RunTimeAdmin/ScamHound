"""
Tests for tweet approval workflow.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _score(token_mint: str, risk_score: int = 80) -> dict:
    return {
        "token_mint": token_mint,
        "name": "Risky",
        "symbol": "RISK",
        "risk_score": risk_score,
        "risk_level": "HIGH" if risk_score < 85 else "CRITICAL",
        "verdict": "risk indicators",
        "top_risk_factors": ["factor-1"],
        "top_safe_signals": [],
        "top_10_concentration": 75.0,
        "creator_wallet": "CreatorWallet11111111111111111111111111",
        "creator_username": "creator",
        "prior_launches": 2,
        "wallet_age_days": 3,
        "clustering_score": 0.7,
        "liquidity_usd": 100.0,
        "lifetime_fees_sol": 0.1,
        "created_at": "2026-05-20T00:00:00Z",
    }


def test_high_risk_unnotified_requires_approval(temp_database):
    """High-risk token should not be tweetable before explicit approval."""
    mint = "TweetApprovalMint111111111111111111111111111"
    database.save_score(_score(mint, 90))

    assert database.get_high_risk_unnotified(threshold=65) == []

    assert database.approve_tweet(mint, approved_by="admin@example.com")
    approved_rows = database.get_high_risk_unnotified(threshold=65)
    assert len(approved_rows) == 1
    assert approved_rows[0]["token_mint"] == mint
    assert approved_rows[0]["tweet_approved_by"] == "admin@example.com"


def test_pending_alerts_and_approve_endpoint_require_admin(
    fastapi_test_client,
):
    """Pending/approve APIs require authenticated admin user."""
    pending = fastapi_test_client.get("/api/alerts/pending")
    assert pending.status_code == 401

    approve = fastapi_test_client.post("/api/alerts/testmint/approve")
    assert approve.status_code == 401


def test_admin_can_approve_tweet_via_api(fastapi_test_client):
    """Admin approval endpoint marks token as approved."""
    mint = "TweetApiApproveMint11111111111111111111111111"
    database.save_score(_score(mint, 88))

    admin_user = {"id": 1, "email": "admin@example.com", "is_admin": True}
    with patch("dashboard.app.get_current_user", return_value=admin_user):
        response = fastapi_test_client.post(f"/api/alerts/{mint}/approve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True

    token = database.get_token_score(mint)
    assert token is not None
    assert token["tweet_approved_by"] == "admin@example.com"
    assert token["tweet_approved_at"] is not None
