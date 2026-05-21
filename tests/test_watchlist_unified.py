"""
Regression tests for unified watchlist storage.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import database  # noqa: E402


def _create_key_and_id() -> int:
    created = database.create_api_key("watchlist@example.com", tier="pro")
    row = database.get_api_key_by_prefix(created["key_prefix"])
    return row["id"]


def test_global_and_user_watchlists_are_isolated(temp_database):
    """Global and per-user watchlists should coexist without collisions."""
    key_id = _create_key_and_id()
    wallet = "WatchWallet11111111111111111111111111111111"

    assert database.add_to_watchlist(wallet, "global", "")
    assert database.add_to_user_watchlist(key_id, wallet, "personal", "")

    global_rows = database.get_watchlist()
    user_rows = database.get_user_watchlist(key_id)

    assert len(global_rows) == 1
    assert len(user_rows) == 1
    assert global_rows[0]["wallet_address"] == wallet
    assert user_rows[0]["wallet_address"] == wallet
    assert database.is_watched_wallet(wallet) is True
    assert database.is_user_watched_wallet(key_id, wallet) is True


def test_init_db_migrates_legacy_watchlist_rows(temp_database):
    """Legacy watchlist rows should migrate to unified storage."""
    key_id = _create_key_and_id()
    global_wallet = "LegacyGlobal11111111111111111111111111111111"
    user_wallet = "LegacyUser111111111111111111111111111111111"

    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO watchlist (wallet_address, label, notes, added_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (global_wallet, "legacy-global", ""),
        )
        conn.execute(
            """
            INSERT INTO user_watchlist (
                key_id, wallet_address, label, notes, added_at
            )
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (key_id, user_wallet, "legacy-user", ""),
        )
        conn.commit()
    finally:
        conn.close()

    # Re-run init to execute migration logic.
    database.init_db()

    global_wallets = {
        row["wallet_address"] for row in database.get_watchlist()
    }
    user_wallets = {
        row["wallet_address"] for row in database.get_user_watchlist(key_id)
    }

    assert global_wallet in global_wallets
    assert user_wallet in user_wallets
