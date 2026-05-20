"""
Regression tests for scan-clearing with foreign-key-enforced score history.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _sample_score(token_mint: str, user_id: int) -> dict:
    return {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TOK",
        "risk_score": 70,
        "risk_level": "HIGH",
        "verdict": "v",
        "top_risk_factors": [],
        "top_safe_signals": [],
        "top_10_concentration": 50.0,
        "creator_wallet": "Creator111111111111111111111111111111111",
        "creator_username": "creator",
        "prior_launches": 1,
        "wallet_age_days": 12,
        "clustering_score": 0.1,
        "liquidity_usd": 1000.0,
        "lifetime_fees_sol": 1.0,
        "created_at": "2026-05-20T00:00:00Z",
        "user_id": user_id,
    }


def test_clear_all_scores_deletes_history_before_parent_rows(temp_database):
    """clear_all_scores should not violate score_history FK constraints."""
    database.save_score(_sample_score("MintFK111111111111111111111111111111111", 1))

    result = database.clear_all_scores()

    assert result["scored_deleted"] == 1
    assert result["history_deleted"] == 1
    assert database.get_recent_scores(limit=10) == []


def test_clear_user_scans_removes_user_history_and_scores(temp_database):
    """clear_user_scans should remove dependent score_history rows first."""
    database.save_score(_sample_score("MintUserA1111111111111111111111111111111", 1))
    database.save_score(_sample_score("MintUserB1111111111111111111111111111111", 2))

    result = database.clear_user_scans(1)

    assert result["scored_deleted"] == 1
    assert result["history_deleted"] == 1
    remaining = database.get_recent_scores(limit=10)
    assert len(remaining) == 1
    assert remaining[0]["token_mint"] == "MintUserB1111111111111111111111111111111"
