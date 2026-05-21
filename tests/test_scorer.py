"""
Tests for scorer output normalization safeguards.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    llm_payload = (
        '{"risk_score":"not-a-number","risk_level":null,"verdict":"x"}'
    )

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(_sample_token_data())

    assert result["risk_score"] == 50
    assert result["risk_level"] == "MEDIUM"


def test_calculate_risk_score_parses_json_with_preamble_and_trailer():
    """Scorer should parse JSON even with wrapper text around it."""
    llm_payload = (
        "Here is the analysis:\n"
        '{"risk_score":72,"risk_level":"HIGH","verdict":"wrapped",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
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
    assert result["llm_attempts"] == 3


def test_calculate_risk_score_sanitizes_age_and_unknown_data_claims():
    """Contradictory maturity/unknown-data claims should be cleaned."""
    llm_payload = (
        '{"risk_score":65,"risk_level":"HIGH",'
        '"verdict":"This token is brand new (0 minutes old). '
        'No immediate red flags.",'
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
    """Missing BubbleMaps data is never a risk factor."""
    llm_payload = (
        '{"risk_score":58,"risk_level":"MEDIUM","verdict":"ok",'
        '"top_risk_factors":['
        '"No BubbleMaps data available for cluster analysis",'
        '"Low holder count"],'
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
        '"top_risk_factors": [], "top_safe_signals": []} trailing note '
        "with {bad"
    )

    result = scorer._parse_llm_json_response(payload)

    assert result["risk_score"] == 40
    assert result["risk_level"] == "MEDIUM"


def test_build_user_prompt_marks_unknown_total_holders_when_missing():
    """Prompt should not fabricate total holder count when unknown."""
    token_data = _sample_token_data()
    token_data["holders"] = {
        "top_holders": [],
        "top_10_concentration_pct": 10.0,
        "total_holder_count": None,
    }

    prompt = scorer.build_user_prompt(token_data)

    assert "Total holders: Unknown (top-holder sample only)" in prompt


def test_build_user_prompt_includes_tier1_security_controls():
    """Prompt should include authority, token2022, LP, and honeypot fields."""
    token_data = _sample_token_data()
    token_data["security"] = {
        "mint_authority_renounced": False,
        "freeze_authority_renounced": True,
        "is_token_2022": True,
        "token_2022_extensions": ["TransferFeeConfig"],
        "token_program_owner": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    }
    token_data["lp_controls"] = {"lp_locked": False, "lp_burned": False}
    token_data["buy_count"] = 9
    token_data["sell_count"] = 0
    token_data["honeypot_suspected"] = True
    token_data["jupiter_total_price_impact_pct"] = 14.2
    token_data["jupiter_route_complexity"] = "multi_hop"

    prompt = scorer.build_user_prompt(token_data)

    assert "Mint authority renounced: False" in prompt
    assert "Freeze authority renounced: True" in prompt
    assert "Token-2022 mint: True" in prompt
    assert "Token-2022 extensions: ['TransferFeeConfig']" in prompt
    assert "LP locked (if detected): False" in prompt
    assert "LP burned (if detected): False" in prompt
    assert "Honeypot suspected from trade flow heuristic: True" in prompt
    assert "Jupiter total route price impact %: 14.2" in prompt


def test_call_llm_uses_configured_provider():
    """Provider routing should honor config-backed LLM_PROVIDER."""
    with patch.object(scorer, "get_config", return_value="deepseek"):
        with patch.object(
            scorer, "_call_deepseek", return_value="ok"
        ) as deepseek:
            with patch.object(
                scorer, "_call_anthropic", return_value="nope"
            ) as anthropic:
                result = scorer._call_llm("system", "user")

    assert result == "ok"
    deepseek.assert_called_once_with("system", "user")
    anthropic.assert_not_called()


def test_call_anthropic_uses_model_from_config():
    """Anthropic call should use configurable model name."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"risk_score": 1}')]
    )

    with patch.object(
        scorer, "_get_anthropic_client", return_value=fake_client
    ):
        with patch.object(
            scorer, "get_config", return_value="claude-sonnet-configured"
        ):
            scorer._call_anthropic("system", "user")

    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-configured"


def test_calculate_risk_score_retries_after_transient_failure():
    """A transient LLM failure should retry and then succeed."""
    token_data = _sample_token_data()
    llm_payload = (
        '{"risk_score":61,"risk_level":"HIGH","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.dict(
            "os.environ",
            {"LLM_MAX_RETRIES": "2", "LLM_RETRY_BACKOFF_SECONDS": "0"},
            clear=False,
        ):
            with patch.object(
                scorer,
                "_call_llm",
                side_effect=[ValueError("temp"), llm_payload],
            ) as llm_mock:
                result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] == 61
    assert result["risk_level"] == "HIGH"
    assert result["score_source"].startswith("ai_")
    assert result["llm_attempts"] == 2
    assert llm_mock.call_count == 2


def test_calculate_risk_score_applies_due_diligence_floor_on_missing_data():
    """Low scores should be lifted when core diligence signals are missing."""
    llm_payload = (
        '{"risk_score":12,"risk_level":"LOW","verdict":"Looks safe.",'
        '"top_risk_factors":[],"top_safe_signals":['
        '"No prior rug pulls from creator"]}'
    )
    token_data = _sample_token_data()
    token_data["creator"] = {"wallet": "Unknown"}
    token_data["wallet_age_days"] = -1
    token_data["token_age_minutes"] = None
    token_data["unique_trader_count"] = 0

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] == 45
    assert result["risk_level"] == "MEDIUM"
    assert "No prior rug pulls from creator" not in result["top_safe_signals"]
    assert any(
        "Limited due diligence data coverage" in factor
        for factor in result["top_risk_factors"]
    )
    assert "missing due diligence data" in result["verdict"].lower()


def test_due_diligence_guard_softens_overconfident_safe_verdict():
    """Missing core signals should block 'legitimate/no red flags' phrasing."""
    llm_payload = (
        '{"risk_score":20,"risk_level":"LOW",'
        '"verdict":"Appears to be a legitimate token with no significant red '
        'flags. Overall low risk. No prior rug pulls detected.",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["creator"] = {"wallet": "Unknown"}
    token_data["wallet_age_days"] = -1
    token_data["token_age_minutes"] = None
    token_data["unique_trader_count"] = 0

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    verdict = result["verdict"]
    assert verdict.startswith("Core diligence signals are incomplete")
    assert "missing due diligence data" in verdict.lower()
    assert result["risk_score"] == 45


def test_due_diligence_guard_accepts_trade_activity_fallback_counts():
    """Non-zero buy/sell fallback should satisfy activity coverage."""
    llm_payload = (
        '{"risk_score":22,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["creator"] = {
        "wallet": "CreatorWallet11111111111111111111111111"
    }
    token_data["wallet_age_days"] = 40
    token_data["token_age_minutes"] = 120
    token_data["unique_trader_count"] = 0
    token_data["buy_count"] = 5
    token_data["sell_count"] = 1

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] == 22


def test_calculate_risk_score_applies_authority_security_weights():
    """Hard authority controls should increase score beyond LLM output."""
    llm_payload = (
        '{"risk_score":15,"risk_level":"LOW","verdict":"Looks clean.",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["security"] = {
        "mint_authority_renounced": False,
        "freeze_authority_renounced": False,
        "freeze_authority_whitelisted": False,
        "permanent_delegate": "Delegate111111111111111111111111111111",
        "transfer_fee_bps": 600,
        "token_2022_extensions": ["PermanentDelegate", "TransferFeeConfig"],
    }

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 75
    assert result["risk_level"] in {"HIGH", "CRITICAL"}
    assert any(
        "Mint authority is active" in factor
        for factor in result["top_risk_factors"]
    )


def test_calculate_risk_score_applies_jupiter_simulation_weight():
    """Critical Jupiter simulation outcomes should force higher risk scores."""
    llm_payload = (
        '{"risk_score":22,"risk_level":"LOW","verdict":"Looks fine.",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["honeypot_simulation_status"] = "sell_quote_unavailable"

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 60
    assert any(
        "buy route but no sell route" in factor.lower()
        for factor in result["top_risk_factors"]
    )


def test_calculate_risk_score_applies_jupiter_price_impact_weight():
    """Elevated combined route price impact should add deterministic risk."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["honeypot_simulation_status"] = "round_trip_ok"
    token_data["jupiter_total_price_impact_pct"] = 13.5

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 38
    assert any(
        "combined price impact" in factor.lower()
        for factor in result["top_risk_factors"]
    )


def test_calculate_risk_score_weights_creator_controlled_lp():
    """Creator-controlled unlocked LP should increase score materially."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["lp_controls"] = {
        "lp_locked": False,
        "lp_burned": False,
        "lp_unlocked_creator_controlled": True,
    }

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 60
    assert any(
        "creator appears to control unlocked lp supply" in factor.lower()
        for factor in result["top_risk_factors"]
    )


def test_calculate_risk_score_weights_bundle_launch_signal():
    """Bundle launch suspicion should raise risk materially."""
    llm_payload = (
        '{"risk_score":28,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["bundle_launch_suspected"] = True
    token_data["bundle_funded_by_creator_count"] = 4

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 53
    assert result["bundle_launch_suspected"] is True


def test_calculate_risk_score_weights_wash_trade_cycles():
    """Detected wash trade cycles should increase score."""
    llm_payload = (
        '{"risk_score":25,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["wash_trade_cycle_count"] = 3
    token_data["wash_trade_suspected"] = True

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 45
    assert result["wash_trade_suspected"] is True


def test_calculate_risk_score_weights_top_holder_dumping():
    """Top-holder dumping suspicion should add deterministic risk weight."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["top_holder_dumping_suspected"] = True
    token_data["top_holder_sell_count"] = 3
    token_data["top_holder_sell_volume_usd"] = 15000

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 55
    assert result["top_holder_dumping_suspected"] is True


def test_calculate_risk_score_weights_holder_velocity_spike():
    """Holder-velocity spike should add deterministic risk weight."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["holder_velocity_spike"] = True
    token_data["unique_buyers_last_hour"] = 40
    token_data["unique_buyers_prev_hour"] = 8

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 40
    assert result["holder_velocity_spike"] is True


def test_calculate_risk_score_weights_dexscreener_warning_label():
    """DexScreener warning labels should add deterministic risk."""
    llm_payload = (
        '{"risk_score":35,"risk_level":"MEDIUM","verdict":"watch",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["dexscreener_checked"] = True
    token_data["dexscreener_has_warning_label"] = True
    token_data["dexscreener_warning_labels"] = ["honeypot"]

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 55
    assert result["dexscreener_has_warning_label"] is True


def test_calculate_risk_score_weights_recent_project_domain():
    """Very new project domains should add deterministic risk."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["domain_name"] = "project.io"
    token_data["domain_age_checked"] = True
    token_data["domain_age_days"] = 12
    token_data["domain_recently_registered"] = True

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 50
    assert result["domain_recently_registered"] is True


def test_calculate_risk_score_applies_supply_burn_reduction():
    """Meaningful burned supply should slightly reduce risk score."""
    llm_payload = (
        '{"risk_score":40,"risk_level":"MEDIUM","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["supply_burn_checked"] = True
    token_data["supply_burn_share_pct"] = 30.0
    token_data["supply_burn_meaningful"] = True

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] <= 36
    assert result["supply_burn_meaningful"] is True


def test_calculate_risk_score_weights_explosive_holder_velocity_band():
    """Explosive velocity band should add additional deterministic risk."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["holder_velocity_band"] = "explosive"
    token_data["unique_buyers_last_15m"] = 25
    token_data["unique_buyers_prev_15m"] = 5

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 45
    assert result["holder_velocity_band"] == "explosive"


def test_calculate_risk_score_weights_genesis_funding_cluster():
    """Large same-source funding cluster should add deterministic risk."""
    llm_payload = (
        '{"risk_score":32,"risk_level":"MEDIUM","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["largest_funding_cluster_supply_pct"] = 24.0
    token_data["creator_funded_cluster_supply_pct"] = 24.0
    token_data["genesis_cluster_suspected"] = True

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 57
    assert any(
        "same-source funding cluster" in factor.lower()
        for factor in result["top_risk_factors"]
    )


def test_calculate_risk_score_applies_low_holder_concentration_reduction():
    """Low top-10/top-20 concentration should add a small risk reduction."""
    llm_payload = (
        '{"risk_score":40,"risk_level":"MEDIUM","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["holders"] = {
        "top_10_concentration_pct": 12.0,
        "top_20_concentration_pct": 22.0,
    }

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] <= 35
    assert any(
        "top-10 <15%" in signal.lower()
        for signal in result["top_safe_signals"]
    )


def test_calculate_risk_score_applies_high_holder_concentration_weight():
    """High concentration should add deterministic risk weight."""
    llm_payload = (
        '{"risk_score":30,"risk_level":"LOW","verdict":"ok",'
        '"top_risk_factors":[],"top_safe_signals":[]}'
    )
    token_data = _sample_token_data()
    token_data["holders"] = {
        "top_10_concentration_pct": 34.0,
        "top_20_concentration_pct": 50.0,
    }

    with patch.object(scorer, "_get_anthropic_client", return_value=object()):
        with patch.object(scorer, "_call_llm", return_value=llm_payload):
            result = scorer.calculate_risk_score(token_data)

    assert result["risk_score"] >= 50
    assert any(
        "holder concentration is high" in factor.lower()
        for factor in result["top_risk_factors"]
    )
