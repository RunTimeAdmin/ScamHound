"""
Tests for scorer output normalization safeguards.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import scorer  # noqa: E402


def _sample_token_data():
    return {
        "token_mint": "TestMint1111111111111111111111111111111111",
        "name": "Test Token",
        "symbol": "TEST",
        "creator": {"wallet": "CreatorWallet11111111111111111111111111"},
        "holders": {"top_10_concentration_pct": 12.5},
    }


def test_calculate_risk_score_clamps_and_normalizes_outputs():
    """Invalid model output fields should be normalized safely."""
    llm_payload = (
        '{"risk_score":"999","risk_level":"SEVERE","verdict":"ok",'
        '"top_risk_factors":["a", "", 123],'
        '"top_safe_signals":"not-a-list"}'
    )

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(_sample_token_data())

    assert result["risk_score"] == 100
    assert result["risk_level"] == "CRITICAL"
    assert result["top_risk_factors"] == ["a", "123"]
    assert result["top_safe_signals"] == []


def test_calculate_risk_score_invalid_numeric_falls_back_to_default():
    """Non-numeric score should fallback to 50 and MEDIUM."""
    llm_payload = '{"risk_score":"not-a-number","risk_level":null,"verdict":"x"}'

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(_sample_token_data())

    assert result["risk_score"] == 50
    assert result["risk_level"] == "MEDIUM"
