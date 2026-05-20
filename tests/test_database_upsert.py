"""
Regression tests for scored_tokens upsert behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _sample_score(token_mint: str, score: int, verdict: str) -> dict:
    return {
        "token_mint": token_mint,
        "name": "Sample Token",
        "symbol": "SAMPLE",
        "risk_score": score,
        "risk_level": "HIGH" if score >= 61 else "MEDIUM",
        "verdict": verdict,
        "top_risk_factors": ["factor-a"],
        "top_safe_signals": ["signal-a"],
        "top_10_concentration": 44.2,
        "creator_wallet": "CreatorWallet11111111111111111111111111",
        "creator_username": "creator",
        "prior_launches": 1,
        "wallet_age_days": 30,
        "clustering_score": 0.2,
        "liquidity_usd": 10000.0,
        "lifetime_fees_sol": 1.1,
        "created_at": "2026-05-20T00:00:00Z",
        "platform": "pumpfun",
    }


def test_save_score_upsert_preserves_tweet_sent(temp_database):
    """Re-scoring a token should not reset tweet_sent to false."""
    mint = "MintTweetPreserve1111111111111111111111111111"

    first = _sample_score(mint, 75, "first")
    database.save_score(first)
    database.mark_tweet_sent(mint)

    updated = _sample_score(mint, 80, "updated")
    database.save_score(updated)

    row = database.get_token_score(mint)
    assert row is not None
    assert bool(row["tweet_sent"]) is True
    assert row["risk_score"] == 80


def test_save_score_upsert_preserves_existing_user_id(temp_database):
    """Re-scoring from background flow keeps existing token owner user_id."""
    mint = "MintUserPreserve11111111111111111111111111111"

    first = _sample_score(mint, 55, "initial")
    first["user_id"] = 42
    database.save_score(first)

    rescored = _sample_score(mint, 65, "rescored")
    # Simulate background re-score path where user_id is not provided.
    database.save_score(rescored)

    row = database.get_token_score(mint)
    assert row is not None
    assert row["user_id"] == 42
    assert row["risk_score"] == 65
