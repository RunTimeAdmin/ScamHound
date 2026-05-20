"""
Regression tests for unscored token handling in creator leaderboard.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _score(token_mint: str, risk_score, risk_level: str) -> dict:
    return {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TOK",
        "risk_score": risk_score,
        "risk_level": risk_level,
        "verdict": "v",
        "top_risk_factors": [],
        "top_safe_signals": [],
        "top_10_concentration": 10.0,
        "creator_wallet": "Creator111111111111111111111111111111111",
        "creator_username": "creator",
        "prior_launches": 1,
        "wallet_age_days": 10,
        "clustering_score": 0.1,
        "liquidity_usd": 1000.0,
        "lifetime_fees_sol": 1.0,
        "created_at": "2026-05-20T00:00:00Z",
    }


def test_creator_leaderboard_excludes_unscored_tokens(temp_database):
    """Fallback unscored rows should not affect leaderboard aggregates."""
    database.save_score(_score("MintScored1111111111111111111111111111111", 80, "HIGH"))
    database.save_score(
        _score("MintUnscored11111111111111111111111111111", 0, "UNSCORED")
    )

    leaderboard = database.get_creator_leaderboard(min_tokens=1, limit=10)
    assert len(leaderboard) == 1
    assert leaderboard[0]["creator_wallet"] == "Creator111111111111111111111111111111111"
    assert leaderboard[0]["total_tokens"] == 1
    assert leaderboard[0]["avg_risk_score"] == 80.0
