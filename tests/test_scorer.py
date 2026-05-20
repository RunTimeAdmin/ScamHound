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


def test_calculate_risk_score_parses_json_with_preamble_and_trailer():
    """Scorer should parse JSON even with wrapper text around it."""
    llm_payload = (
        "Here is the analysis:\n"
        '{"risk_score":72,"risk_level":"HIGH","verdict":"wrapped","top_risk_factors":[],"top_safe_signals":[]}'
        "\nThanks."
    )

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(_sample_token_data())

    assert result["risk_score"] == 72
    assert result["risk_level"] == "HIGH"
    assert result["verdict"] == "wrapped"


def test_calculate_risk_score_marks_fallback_as_unscored():
    """LLM failures should produce explicit unscored fallback output."""
    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", side_effect=ValueError("boom")):
            result = scorer.calculate_risk_score(_sample_token_data())

    assert result["risk_score"] == 0
    assert result["risk_level"] == "UNSCORED"
    assert result["score_source"] == "fallback"


def test_calculate_risk_score_sanitizes_age_and_unknown_data_claims():
    """Contradictory maturity/unknown-data claims should be cleaned."""
    llm_payload = (
        '{"risk_score":65,"risk_level":"HIGH",'
        '"verdict":"This token is brand new (0 minutes old). No immediate red flags.",'
        '"top_risk_factors":['
        '"Creator wallet age unknown",'
        '"No BubbleMaps data available for cluster analysis",'
        '"Very early stage with limited trading history",'
        '"Low liquidity for age"'
        '],'
        '"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["token_age_minutes"] = 8 * 24 * 60
    token_data["wallet_age_days"] = 45
    token_data["bubblemaps"] = {"decentralization_score": 72}

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert "brand new" not in result["verdict"].lower()
    assert "0 minutes old" not in result["verdict"].lower()
    assert result["top_risk_factors"] == ["Low liquidity for age"]


def test_calculate_risk_score_removes_missing_bubblemaps_as_risk_factor():
    """Missing BubbleMaps data should never be counted as a risk factor."""
    llm_payload = (
        '{"risk_score":58,"risk_level":"MEDIUM","verdict":"ok",'
        '"top_risk_factors":["No BubbleMaps data available for cluster analysis","Low holder count"],'
        '"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["token_age_minutes"] = 120
    token_data["wallet_age_days"] = 14
    token_data["bubblemaps"] = {}

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["top_risk_factors"] == ["Low holder count"]


def test_parse_llm_json_response_handles_extra_braces_in_trailing_noise():
    """Balanced object extraction should ignore trailing brace-like noise."""
    payload = (
        'prefix {"risk_score": 40, "risk_level": "MEDIUM", "verdict": "ok", '
        '"top_risk_factors": [], "top_safe_signals": []} trailing note with {bad'
    )

    result = scorer._parse_llm_json_response(payload)

    assert result["risk_score"] == 40
    assert result["risk_level"] == "MEDIUM"
