"""
Regression tests for token age parsing and defaults.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import monitor  # noqa: E402


def test_calculate_token_age_minutes_handles_millisecond_epoch_string():
    """Millisecond epoch strings should not be interpreted as seconds."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    three_days_ms = now_ms - (3 * 24 * 60 * 60 * 1000)

    age_minutes = monitor._calculate_token_age_minutes(str(three_days_ms))

    assert age_minutes is not None
    assert age_minutes >= (3 * 24 * 60) - 2


def test_calculate_token_age_minutes_returns_none_for_missing_timestamp():
    """Missing launch timestamps should remain unknown, not zero."""
    assert monitor._calculate_token_age_minutes(None) is None
    assert monitor._calculate_token_age_minutes("") is None


def test_scan_single_token_backfills_age_from_birdeye_launch_time():
    """scan_single_token_async should recompute age from launch time."""
    five_hours_ago = int(datetime.now(timezone.utc).timestamp()) - (5 * 60 * 60)
    token_mint = "11111111111111111111111111111112"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 25,
        "risk_level": "LOW",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {
                            "created_at": five_hours_ago,
                            "creator_wallet": (
                                "Creator111111111111111111111111111111111"
                            ),
                        },
                        "liquidity": {"liquidity_usd": 1000},
                        "trades": {},
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "token_age_minutes": token_data.get("token_age_minutes"),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["token_age_minutes"] is not None
    assert result["token_age_minutes"] >= (5 * 60) - 2


def test_scan_single_token_enriches_lp_controls_from_lp_mint():
    """LP controls should be enriched when overview exposes LP mint."""
    token_mint = "11111111111111111111111111111113"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 65,
        "risk_level": "HIGH",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {
                            "creator_wallet": (
                                "Creator111111111111111111111111111111111"
                            ),
                            "lpMint": (
                                "LpMint11111111111111111111111111111111111"
                            ),
                        },
                        "liquidity": {"liquidity_usd": 1000},
                        "trades": {},
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_lp_controls",
                new=AsyncMock(
                    return_value={
                        "lp_locked": False,
                        "lp_burned": False,
                        "lp_unlocked_creator_controlled": True,
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "lp_locked": token_data.get("lp_controls", {}).get("lp_locked"),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["lp_locked"] is False


def test_scan_single_token_enriches_domain_age_from_dexscreener():
    """Domain age lookup should enrich token data when websites exist."""
    token_mint = "11111111111111111111111111111114"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 45,
        "risk_level": "MEDIUM",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {},
                        "liquidity": {},
                        "trades": {},
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(
                    return_value={
                        "checked": True,
                        "website_urls": ["https://project.io"],
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_domain_age",
                new=AsyncMock(
                    return_value={
                        "domain": "project.io",
                        "checked": True,
                        "age_days": 14,
                        "recently_registered": True,
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "domain_age_days": token_data.get("domain_age_days"),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["domain_age_days"] == 14


def test_scan_single_token_uses_dexscreener_trade_fallback_when_empty():
    """DexScreener txn totals should backfill empty Birdeye counts."""
    token_mint = "11111111111111111111111111111117"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 40,
        "risk_level": "MEDIUM",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {},
                        "liquidity": {},
                        "trades": {
                            "buy_count": 0,
                            "sell_count": 0,
                            "unique_trader_count": 0,
                        },
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(
                    return_value={
                        "checked": True,
                        "pair_created_at": 1716000000,
                        "liquidity_usd": 50000,
                        "liquidity_to_mcap_ratio": 0.15,
                        "txns_h24_buys": 8,
                        "txns_h24_sells": 2,
                        "website_urls": [],
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_domain_age",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "buy_count": token_data.get("buy_count"),
                    "sell_count": token_data.get("sell_count"),
                    "trade_activity_source": token_data.get("trade_activity_source"),
                    "liquidity_usd": token_data.get("liquidity_usd"),
                    "created_at": token_data.get("created_at"),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["buy_count"] == 8
    assert result["sell_count"] == 2
    assert result["trade_activity_source"] == "dexscreener_fallback"
    assert result["liquidity_usd"] == 50000
    assert result["created_at"] == 1716000000


def test_scan_single_token_uses_gecko_market_fallback_when_missing():
    """Gecko fallback should fill liquidity and trades when missing."""
    token_mint = "11111111111111111111111111111118"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 40,
        "risk_level": "MEDIUM",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {},
                        "liquidity": {
                            "liquidity_usd": 0,
                            "liquidity_to_mcap_ratio": 0,
                        },
                        "trades": {
                            "buy_count": 0,
                            "sell_count": 0,
                            "unique_trader_count": 0,
                        },
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(
                    return_value={
                        "checked": True,
                        "liquidity_usd": 123456.0,
                        "liquidity_to_mcap_ratio": 0.23,
                        "txns_h24_buys": 9,
                        "txns_h24_sells": 4,
                        "pool_created_at": "2026-05-20T00:00:00Z",
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "liquidity_usd": token_data.get("liquidity_usd"),
                    "buy_count": token_data.get("buy_count"),
                    "trade_activity_source": token_data.get("trade_activity_source"),
                    "created_at": token_data.get("created_at"),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["liquidity_usd"] == 123456.0
    assert result["buy_count"] == 9
    assert result["trade_activity_source"] == "geckoterminal_fallback"
    assert result["created_at"] == "2026-05-20T00:00:00Z"


def test_scan_single_token_enriches_supply_burn_ratio_from_holders():
    """Holder burn-share signal should flow into scoring input."""
    token_mint = "11111111111111111111111111111115"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 40,
        "risk_level": "MEDIUM",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }
    holders = {
        "top_holders": [
            {
                "address": "1nc1nerator11111111111111111111111111111111",
                "percentage": 25.0,
            },
            {"address": "HolderA", "percentage": 20.0},
        ]
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=holders),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "supply_burn_share_pct": token_data.get(
                        "supply_burn_share_pct"
                    ),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["supply_burn_share_pct"] == 25.0


def test_scan_single_token_propagates_holder_velocity_band():
    """Velocity band from trade history should flow to score payload."""
    token_mint = "11111111111111111111111111111116"
    fake_score = {
        "token_mint": token_mint,
        "name": "Token",
        "symbol": "TKN",
        "risk_score": 55,
        "risk_level": "HIGH",
        "verdict": "ok",
        "top_risk_factors": [],
        "top_safe_signals": [],
    }
    trades = {
        "unique_buyers_last_hour": 30,
        "unique_buyers_prev_hour": 6,
        "holder_velocity_spike": True,
        "unique_buyers_last_15m": 14,
        "unique_buyers_prev_15m": 3,
        "unique_buyers_last_6h": 80,
        "unique_buyers_prev_6h": 30,
        "holder_velocity_band": "explosive",
    }

    with ExitStack() as stack:
        stack.enter_context(
            patch("engine.monitor.database.token_already_scored", return_value=False)
        )
        stack.enter_context(
            patch("engine.monitor.database.was_recently_scored", return_value=False)
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bags_profile",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_market_data",
                new=AsyncMock(
                    return_value={
                        "overview": {},
                        "liquidity": {},
                        "trades": trades,
                    }
                ),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_geckoterminal_fallback",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_holder_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_token_security_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_bubblemaps_data",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_simulate_honeypot",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_get_dexscreener_signals",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor._async_analyze_creator",
                new=AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch(
                "engine.monitor.scorer.calculate_risk_score",
                side_effect=lambda token_data: {
                    **fake_score,
                    "holder_velocity_band": token_data.get(
                        "holder_velocity_band"
                    ),
                },
            )
        )
        stack.enter_context(
            patch("engine.monitor.database.is_watched_wallet", return_value=False)
        )
        stack.enter_context(patch("engine.monitor.database.save_score"))
        stack.enter_context(patch("engine.monitor._mark_processed"))
        stack.enter_context(patch("engine.monitor._notify_new_score"))

        result = monitor.scan_single_token(token_mint, skip_if_scored=False)

    assert result is not None
    assert result["holder_velocity_band"] == "explosive"


def test_detect_top_holder_dumping_flags_heavy_top_holder_sells():
    """Top-holder sell flow should trigger dumping signal."""
    top_holders = [
        {"address": "TopA"},
        {"address": "TopB"},
    ]
    recent_trades = [
        {"wallet": "TopA", "side": "sell", "amount_usd": 4000},
        {"wallet": "TopB", "side": "sell", "amount_usd": 7000},
        {"wallet": "Other", "side": "buy", "amount_usd": 1000},
    ]

    result = monitor._detect_top_holder_dumping(top_holders, recent_trades)

    assert result["top_holder_dumping_suspected"] is True
    assert result["top_holder_sell_count"] == 2
    assert result["top_holder_sell_volume_usd"] == 11000.0
    assert result["top_holder_net_sell_usd"] == 11000.0
    assert result["top_holder_net_sell_suspected"] is True
