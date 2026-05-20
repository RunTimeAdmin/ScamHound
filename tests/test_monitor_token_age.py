"""
Regression tests for token age parsing and defaults.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
