"""
Regression tests for token age parsing and defaults.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
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


async def _noop_async(*_args, **_kwargs):
    return None


def test_scan_single_token_backfills_age_from_birdeye_launch_time():
    """scan_single_token_async should recompute age when overview has launch time."""
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

    with patch("engine.monitor.database.token_already_scored", return_value=False):
        with patch("engine.monitor.database.was_recently_scored", return_value=False):
            with patch("engine.monitor._async_get_bags_profile", new=AsyncMock(return_value=None)):
                with patch(
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
                ):
                    with patch(
                        "engine.monitor._async_get_holder_data",
                        new=AsyncMock(return_value=None),
                    ):
                        with patch(
                            "engine.monitor._async_get_bubblemaps_data",
                            new=AsyncMock(return_value=None),
                        ):
                            with patch(
                                "engine.monitor._async_analyze_creator",
                                new=AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "engine.monitor.scorer.calculate_risk_score",
                                    side_effect=lambda token_data: {
                                        **fake_score,
                                        "token_age_minutes": token_data.get(
                                            "token_age_minutes"
                                        ),
                                    },
                                ):
                                    with patch(
                                        "engine.monitor.database.is_watched_wallet",
                                        return_value=False,
                                    ):
                                        with patch("engine.monitor.database.save_score"):
                                            with patch("engine.monitor._mark_processed"):
                                                with patch("engine.monitor._notify_new_score"):
                                                    result = monitor.scan_single_token(
                                                        token_mint,
                                                        skip_if_scored=False,
                                                    )

    assert result is not None
    assert result["token_age_minutes"] is not None
    assert result["token_age_minutes"] >= (5 * 60) - 2
