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


def test_save_score_persists_and_updates_llm_attempts(temp_database):
    """llm_attempts should persist and update across rescoring."""
    mint = "MintAttempts11111111111111111111111111111111"

    first = _sample_score(mint, 62, "first")
    first["llm_attempts"] = 3
    database.save_score(first)

    row = database.get_token_score(mint)
    assert row is not None
    assert row["llm_attempts"] == 3

    rescored = _sample_score(mint, 70, "rescored")
    rescored["llm_attempts"] = 1
    database.save_score(rescored)

    updated = database.get_token_score(mint)
    assert updated is not None
    assert updated["risk_score"] == 70
    assert updated["llm_attempts"] == 1


def test_save_score_persists_security_check_fields(temp_database):
    """Tier-1 security check fields should persist for dashboard visibility."""
    mint = "MintChecks111111111111111111111111111111111"
    score = _sample_score(mint, 52, "checks")
    score["mint_authority_renounced"] = False
    score["freeze_authority_renounced"] = True
    score["is_token_2022"] = True
    score["token_2022_extensions"] = ["TransferFeeConfig"]
    score["lp_locked"] = False
    score["lp_burned"] = False
    score["honeypot_suspected"] = True
    score["buy_count"] = 9
    score["sell_count"] = 0
    score["update_authority"] = "Update11111111111111111111111111111111"
    score["transfer_fee_bps"] = 500
    score["transfer_fee_max"] = 1000.0
    score["permanent_delegate"] = "Delegate111111111111111111111111111111"
    score["freeze_authority_whitelisted"] = False
    score["honeypot_simulation_status"] = "high_round_trip_loss"
    score["honeypot_round_trip_loss_pct"] = 82.1
    score["bundle_launch_suspected"] = True
    score["bundle_same_slot_or_window"] = True
    score["bundle_amount_clustered"] = True
    score["bundle_funded_by_creator_count"] = 4
    score["wash_trade_cycle_count"] = 3
    score["wash_trade_suspected"] = True
    score["top_holder_dumping_suspected"] = True
    score["top_holder_sell_count"] = 3
    score["top_holder_sell_volume_usd"] = 15000.0
    score["dexscreener_checked"] = True
    score["dexscreener_has_pair"] = True
    score["dexscreener_pair_count"] = 2
    score["dexscreener_labels"] = ["verified"]
    score["dexscreener_has_trust_badge"] = True
    score["dexscreener_has_warning_label"] = False
    score["dexscreener_warning_labels"] = []
    score["domain_name"] = "project.io"
    score["domain_age_checked"] = True
    score["domain_age_days"] = 12
    score["domain_recently_registered"] = True
    score["supply_burn_checked"] = True
    score["supply_burn_share_pct"] = 31.5
    score["supply_burn_meaningful"] = True

    database.save_score(score)
    row = database.get_token_score(mint)

    assert row is not None
    assert row["mint_authority_renounced"] == 0
    assert row["freeze_authority_renounced"] == 1
    assert row["is_token_2022"] == 1
    assert row["token_2022_extensions"] == ["TransferFeeConfig"]
    assert row["lp_locked"] == 0
    assert row["lp_burned"] == 0
    assert row["honeypot_suspected"] == 1
    assert row["buy_count"] == 9
    assert row["sell_count"] == 0
    assert row["update_authority"] == score["update_authority"]
    assert row["transfer_fee_bps"] == 500
    assert row["transfer_fee_max"] == 1000.0
    assert row["permanent_delegate"] == score["permanent_delegate"]
    assert row["freeze_authority_whitelisted"] == 0
    assert row["honeypot_simulation_status"] == "high_round_trip_loss"
    assert row["honeypot_round_trip_loss_pct"] == 82.1
    assert row["bundle_launch_suspected"] == 1
    assert row["bundle_same_slot_or_window"] == 1
    assert row["bundle_amount_clustered"] == 1
    assert row["bundle_funded_by_creator_count"] == 4
    assert row["wash_trade_cycle_count"] == 3
    assert row["wash_trade_suspected"] == 1
    assert row["top_holder_dumping_suspected"] == 1
    assert row["top_holder_sell_count"] == 3
    assert row["top_holder_sell_volume_usd"] == 15000.0
    assert row["dexscreener_checked"] == 1
    assert row["dexscreener_has_pair"] == 1
    assert row["dexscreener_pair_count"] == 2
    assert row["dexscreener_labels"] == ["verified"]
    assert row["dexscreener_has_trust_badge"] == 1
    assert row["dexscreener_has_warning_label"] == 0
    assert row["dexscreener_warning_labels"] == []
    assert row["domain_name"] == "project.io"
    assert row["domain_age_checked"] == 1
    assert row["domain_age_days"] == 12
    assert row["domain_recently_registered"] == 1
    assert row["supply_burn_checked"] == 1
    assert row["supply_burn_share_pct"] == 31.5
    assert row["supply_burn_meaningful"] == 1


def test_soak_audit_summary_reports_retry_and_unknown_signals(
    temp_database,
):
    """Soak summary should reflect retry counts and unknown-data rates."""
    mint_a = "MintSoakA111111111111111111111111111111111"
    mint_b = "MintSoakB111111111111111111111111111111111"

    first = _sample_score(mint_a, 20, "token age unknown right now")
    first["risk_level"] = "LOW"
    first["score_source"] = "ai_anthropic"
    first["llm_attempts"] = 2
    first["creator_wallet"] = "Unknown"
    first["wallet_age_days"] = -1
    first["top_risk_factors"] = ["Unknown token age prevents full assessment"]
    database.save_score(first)

    second = _sample_score(mint_b, 0, "fallback")
    second["risk_level"] = "UNSCORED"
    second["score_source"] = "fallback"
    second["llm_attempts"] = 1
    second["creator_wallet"] = "CreatorWallet11111111111111111111111111"
    second["wallet_age_days"] = 10
    second["top_risk_factors"] = []
    database.save_score(second)

    summary = database.get_soak_audit_summary(limit=10)

    assert summary["sample_size"] >= 2
    assert summary["fallback_count"] >= 1
    assert summary["retried_count"] >= 1
    assert summary["unknown_creator_wallet_count"] >= 1
    assert summary["unknown_wallet_age_count"] >= 1
    assert summary["unknown_token_age_claim_count"] >= 1


def test_get_soak_audit_samples_applies_risk_filter(temp_database):
    """Soak samples should respect supported risk-level filters."""
    low = _sample_score(
        "MintSampleLow111111111111111111111111111111", 20, "ok"
    )
    low["risk_level"] = "LOW"
    low["llm_attempts"] = 1
    database.save_score(low)

    high = _sample_score(
        "MintSampleHigh11111111111111111111111111111", 80, "ok"
    )
    high["risk_level"] = "HIGH"
    high["llm_attempts"] = 2
    database.save_score(high)

    high_samples = database.get_soak_audit_samples(
        limit=10, risk_level="high", randomize=False
    )
    assert len(high_samples) >= 1
    assert all(sample["risk_level"] == "HIGH" for sample in high_samples)
