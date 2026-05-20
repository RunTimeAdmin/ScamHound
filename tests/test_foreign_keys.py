"""
Tests for SQLite foreign key enforcement.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def test_get_connection_enables_foreign_keys(temp_database):
    """Connections should enable PRAGMA foreign_keys."""
    conn = database.get_connection()
    try:
        value = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()

    assert value == 1


def test_score_history_rejects_orphan_token_mint(temp_database):
    """score_history inserts must reference an existing scored token."""
    conn = database.get_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO score_history (
                    token_mint, risk_score, risk_level, score_source, ai_verdict
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("MissingMint1111111111111111111111111111111", 80, "HIGH", "ai", "x"),
            )
            conn.commit()
    finally:
        conn.close()
