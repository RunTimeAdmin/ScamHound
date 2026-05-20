"""
Tests for user scan limit increment behavior.
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _insert_user(user_id: int, scans_today: int, last_scan_date: str):
    conn = database.get_connection()
    conn.execute(
        """
        INSERT INTO users (
            id, google_id, email, name, is_admin, created_at, last_login_at,
            scans_today, last_scan_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            f"google-{user_id}",
            f"user-{user_id}@example.com",
            "User",
            False,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            scans_today,
            last_scan_date,
        ),
    )
    conn.commit()
    conn.close()


def test_check_and_increment_scan_resets_on_new_day(temp_database):
    """A stale last_scan_date should reset the counter before incrementing."""
    _insert_user(user_id=101, scans_today=7, last_scan_date="2000-01-01")

    with patch.dict(
        "os.environ", {"USER_DAILY_SCAN_LIMIT": "10"}, clear=False
    ):
        result = database.check_and_increment_scan(user_id=101, is_admin=False)

    assert result["allowed"] is True
    assert result["scans_today"] == 1
    assert result["limit"] == 10


def test_check_and_increment_scan_blocks_when_limit_reached(temp_database):
    """Users at limit should be blocked and not incremented."""
    today = date.today().isoformat()
    _insert_user(user_id=102, scans_today=10, last_scan_date=today)

    with patch.dict(
        "os.environ", {"USER_DAILY_SCAN_LIMIT": "10"}, clear=False
    ):
        result = database.check_and_increment_scan(user_id=102, is_admin=False)

    assert result["allowed"] is False
    assert result["scans_today"] == 10
    assert result["limit"] == 10
