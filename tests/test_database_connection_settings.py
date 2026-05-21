"""
Regression tests for SQLite connection hardening settings.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def test_get_connection_applies_busy_timeout_pragma(
    temp_database,
):
    """Connection should apply configurable busy_timeout pragma."""
    with patch.dict(os.environ, {"SQLITE_BUSY_TIMEOUT_MS": "1234"}, clear=False):
        conn = database.get_connection()
        try:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
        finally:
            conn.close()

    assert row[0] == 1234


def test_get_connection_invalid_timeout_values_fall_back_defaults(
    temp_database,
):
    """Invalid timeout env values should not crash connection setup."""
    with patch.dict(
        os.environ,
        {
            "SQLITE_BUSY_TIMEOUT_MS": "not-a-number",
            "SQLITE_CONNECT_TIMEOUT_SECONDS": "nan-nope",
        },
        clear=False,
    ):
        conn = database.get_connection()
        try:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
            conn.execute("SELECT 1")
        finally:
            conn.close()

    assert row[0] == 5000
