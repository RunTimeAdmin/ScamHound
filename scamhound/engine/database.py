"""
ScamHound Database Module
SQLite persistence for token scores and alert tracking
"""

import sqlite3
import os
import json
import hashlib
import math
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

DB_PATH = os.getenv("DB_PATH", "scamhound.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create the scored_tokens and watchlist tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scored_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint TEXT UNIQUE NOT NULL,
            name TEXT,
            symbol TEXT,
            risk_score INTEGER,
            risk_level TEXT,
            ai_verdict TEXT,
            top_risk_factors TEXT,
            top_safe_signals TEXT,
            top_10_concentration REAL,
            creator_wallet TEXT,
            creator_username TEXT,
            prior_launches INTEGER,
            wallet_age_days INTEGER,
            clustering_score REAL,
            liquidity_usd REAL,
            lifetime_fees_sol REAL,
            tweet_sent BOOLEAN DEFAULT FALSE,
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL UNIQUE,
            label TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            added_at TEXT NOT NULL,
            last_seen_at TEXT DEFAULT NULL,
            alert_count INTEGER DEFAULT 0
        )
    """)
    
    conn.commit()
    
    # Add score_source column if not exists
    try:
        cursor.execute("ALTER TABLE scored_tokens ADD COLUMN score_source TEXT DEFAULT 'ai'")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add platform column if not exists
    try:
        cursor.execute("ALTER TABLE scored_tokens ADD COLUMN platform TEXT DEFAULT 'bags'")
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # API Keys table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            email TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free',
            name TEXT DEFAULT '',
            calls_today INTEGER DEFAULT 0,
            calls_total INTEGER DEFAULT 0,
            last_used_at TEXT,
            last_reset_date TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
    """)

    # API Usage Log table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            status_code INTEGER,
            response_ms INTEGER,
            logged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (key_id) REFERENCES api_keys(id)
        )
    """)

    # User Watchlist table (Pro+ tier personal watchlists)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            label TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            added_at TEXT NOT NULL,
            last_seen_at TEXT DEFAULT NULL,
            alert_count INTEGER DEFAULT 0,
            FOREIGN KEY (key_id) REFERENCES api_keys(id),
            UNIQUE(key_id, wallet_address)
        )
    """)

    conn.commit()

    # Create indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_wallet ON scored_tokens(creator_wallet)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_score ON scored_tokens(risk_score)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_mint ON scored_tokens(token_mint)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_key_date ON api_usage_log(key_id, logged_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_level_scored ON scored_tokens(risk_level, scored_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON scored_tokens(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_watchlist_key ON user_watchlist(key_id)")

    # Score history table for tracking score changes over time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            score_source TEXT DEFAULT 'ai',
            ai_verdict TEXT,
            scored_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (token_mint) REFERENCES scored_tokens(token_mint)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_score_history_mint ON score_history(token_mint, scored_at DESC)")

    # Users table for Google OAuth accounts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture_url TEXT,
            is_admin BOOLEAN DEFAULT FALSE,
            created_at TEXT NOT NULL,
            last_login_at TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

    # Add user_id column to scored_tokens if not exists
    try:
        cursor.execute("ALTER TABLE scored_tokens ADD COLUMN user_id INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scored_user ON scored_tokens(user_id)")

    # Add scans_today column to users if not exists
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN scans_today INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add last_scan_date column to users if not exists
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_scan_date TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add tweet approval columns if not exists
    try:
        cursor.execute("ALTER TABLE scored_tokens ADD COLUMN tweet_approved_at TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE scored_tokens ADD COLUMN tweet_approved_by TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    conn.close()
    print("[SCAMHOUND] Database initialized")


def search_scored_tokens(
    search: str = None,
    risk_level: str = None,
    min_score: int = None,
    max_score: int = None,
    creator: str = None,
    from_date: str = None,
    to_date: str = None,
    sort_by: str = "scored_at",
    order: str = "desc",
    page: int = 1,
    per_page: int = 50
) -> Dict[str, Any]:
    """Search and filter scored tokens with pagination.
    
    Returns:
        {"tokens": [...], "total": int, "page": int, "per_page": int, "pages": int}
    """
    # Validate sort_by and order against whitelists
    allowed_sort = {"scored_at", "risk_score", "symbol", "name"}
    allowed_order = {"asc", "desc"}
    if sort_by not in allowed_sort:
        sort_by = "scored_at"
    if order not in allowed_order:
        order = "desc"

    conditions = []
    params = []

    if search:
        conditions.append("(name LIKE ? OR symbol LIKE ? OR token_mint LIKE ?)")
        like_val = f"%{search}%"
        params.extend([like_val, like_val, like_val])

    if risk_level:
        conditions.append("risk_level = ?")
        params.append(risk_level)

    if min_score is not None:
        conditions.append("risk_score >= ?")
        params.append(min_score)

    if max_score is not None:
        conditions.append("risk_score <= ?")
        params.append(max_score)

    if creator:
        conditions.append("creator_wallet LIKE ?")
        params.append(f"%{creator}%")

    if from_date:
        conditions.append("scored_at >= ?")
        params.append(from_date)

    if to_date:
        conditions.append("scored_at <= ?")
        params.append(to_date)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    conn = get_connection()
    cursor = conn.cursor()

    # Get total count
    count_sql = f"SELECT COUNT(*) as total FROM scored_tokens {where_clause}"
    cursor.execute(count_sql, params)
    total = cursor.fetchone()["total"]

    # Calculate pagination
    offset = (page - 1) * per_page
    pages = math.ceil(total / per_page) if per_page > 0 else 0

    # Fetch paginated results
    query_sql = f"SELECT * FROM scored_tokens {where_clause} ORDER BY {sort_by} {order} LIMIT ? OFFSET ?"
    cursor.execute(query_sql, params + [per_page, offset])

    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
        result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
        results.append(result)

    return {
        "tokens": results,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages
    }


def token_already_scored(token_mint: str) -> bool:
    """Check if a token has already been scored."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT 1 FROM scored_tokens WHERE token_mint = ?",
        (token_mint,)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    return result is not None


def was_recently_scored(token_mint: str, hours: int = 1) -> bool:
    """Check if a token was scored within the last N hours."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT 1 FROM scored_tokens WHERE token_mint = ? AND scored_at >= datetime('now', ? || ' hours')",
        (token_mint, f'-{int(hours)}')
    )
    
    result = cursor.fetchone()
    conn.close()
    
    return result is not None


def save_score(score_data: Dict[str, Any], score_source: str = 'ai') -> None:
    """Insert a new token score into the database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Convert lists to JSON strings for storage
    risk_factors = json.dumps(score_data.get("top_risk_factors", []))
    safe_signals = json.dumps(score_data.get("top_safe_signals", []))
    
    # Get score_source from score_data if available, otherwise use parameter
    source = score_data.get("score_source", score_source)
    
    # Get platform from score_data (default to 'bags' for backwards compat)
    platform = score_data.get("platform", "bags")

    cursor.execute("""
        INSERT INTO scored_tokens (
            token_mint, name, symbol, risk_score, risk_level, ai_verdict,
            top_risk_factors, top_safe_signals, top_10_concentration,
            creator_wallet, creator_username, prior_launches, wallet_age_days,
            clustering_score, liquidity_usd, lifetime_fees_sol, created_at, score_source,
            platform, user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token_mint) DO UPDATE SET
            name = excluded.name,
            symbol = excluded.symbol,
            risk_score = excluded.risk_score,
            risk_level = excluded.risk_level,
            ai_verdict = excluded.ai_verdict,
            top_risk_factors = excluded.top_risk_factors,
            top_safe_signals = excluded.top_safe_signals,
            top_10_concentration = excluded.top_10_concentration,
            creator_wallet = excluded.creator_wallet,
            creator_username = excluded.creator_username,
            prior_launches = excluded.prior_launches,
            wallet_age_days = excluded.wallet_age_days,
            clustering_score = excluded.clustering_score,
            liquidity_usd = excluded.liquidity_usd,
            lifetime_fees_sol = excluded.lifetime_fees_sol,
            created_at = excluded.created_at,
            score_source = excluded.score_source,
            platform = excluded.platform,
            user_id = COALESCE(excluded.user_id, scored_tokens.user_id),
            scored_at = CURRENT_TIMESTAMP
    """, (
        score_data.get("token_mint"),
        score_data.get("name"),
        score_data.get("symbol"),
        score_data.get("risk_score"),
        score_data.get("risk_level"),
        score_data.get("verdict"),
        risk_factors,
        safe_signals,
        score_data.get("top_10_concentration"),
        score_data.get("creator_wallet"),
        score_data.get("creator_username"),
        score_data.get("prior_launches"),
        score_data.get("wallet_age_days"),
        score_data.get("clustering_score"),
        score_data.get("liquidity_usd"),
        score_data.get("lifetime_fees_sol"),
        score_data.get("created_at"),
        source,
        platform,
        score_data.get("user_id"),
    ))
    
    # Append to score history
    cursor.execute("""
        INSERT INTO score_history (token_mint, risk_score, risk_level, score_source, ai_verdict, scored_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        score_data.get("token_mint"),
        score_data.get("risk_score"),
        score_data.get("risk_level"),
        source,
        score_data.get("verdict")
    ))
    
    conn.commit()
    conn.close()


def get_recent_scores(limit: int = 50) -> List[Dict[str, Any]]:
    """Get the most recent scored tokens."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM scored_tokens 
        ORDER BY scored_at DESC 
        LIMIT ?
    """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        result = dict(row)
        # Parse JSON arrays back
        result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
        result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
        results.append(result)
    
    return results


def get_token_score(token_mint: str) -> Optional[Dict[str, Any]]:
    """Get a single token's score by mint address."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM scored_tokens WHERE token_mint = ?",
        (token_mint,)
    )
    
    row = cursor.fetchone()
    conn.close()
    
    if row is None:
        return None
    
    result = dict(row)
    result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
    result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
    
    return result


# Alias for batch scan endpoint
get_score_by_mint = get_token_score


def get_high_risk_unnotified(threshold: int = 65) -> List[Dict[str, Any]]:
    """Get approved high-risk tokens that haven't been tweeted yet."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM scored_tokens 
        WHERE risk_score >= ?
          AND tweet_sent = FALSE
          AND tweet_approved_at IS NOT NULL
        ORDER BY risk_score DESC
    """, (threshold,))
    
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        result = dict(row)
        result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
        result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
        results.append(result)
    
    return results


def mark_tweet_sent(token_mint: str) -> None:
    """Mark a token as having been tweeted about."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE scored_tokens SET tweet_sent = TRUE WHERE token_mint = ?",
        (token_mint,)
    )
    
    conn.commit()
    conn.close()


def approve_tweet(token_mint: str, approved_by: str = "") -> bool:
    """Approve a token alert for tweet posting."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE scored_tokens
        SET tweet_approved_at = ?, tweet_approved_by = ?
        WHERE token_mint = ?
        """,
        (datetime.now(timezone.utc).isoformat(), approved_by, token_mint),
    )
    approved = cursor.rowcount > 0

    conn.commit()
    conn.close()
    return approved


def get_pending_tweet_approvals(limit: int = 100) -> List[Dict[str, Any]]:
    """Get high-risk tokens awaiting tweet approval."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM scored_tokens
        WHERE risk_score >= 65
          AND tweet_sent = FALSE
          AND tweet_approved_at IS NULL
        ORDER BY risk_score DESC, scored_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        result = dict(row)
        result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
        result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
        results.append(result)

    return results


def get_stats() -> Dict[str, int]:
    """Get overall statistics for the dashboard."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM scored_tokens")
    total = cursor.fetchone()["total"]
    
    cursor.execute("SELECT COUNT(*) as count FROM scored_tokens WHERE risk_level = 'HIGH'")
    high = cursor.fetchone()["count"]
    
    cursor.execute("SELECT COUNT(*) as count FROM scored_tokens WHERE risk_level = 'CRITICAL'")
    critical = cursor.fetchone()["count"]
    
    cursor.execute("SELECT MAX(scored_at) as last_scored FROM scored_tokens")
    last = cursor.fetchone()["last_scored"]
    
    conn.close()
    
    return {
        "total_scanned": total,
        "high_risk": high,
        "critical_alerts": critical,
        "last_updated": last
    }


# ============================================================================
# Watchlist Functions
# ============================================================================

def add_to_watchlist(wallet_address: str, label: str = "", notes: str = "") -> bool:
    """Add a wallet to the watchlist. Returns True if successful, False if already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO watchlist (wallet_address, label, notes, added_at)
            VALUES (?, ?, ?, ?)
        """, (
            wallet_address,
            label,
            notes,
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Wallet already exists
        return False
    finally:
        conn.close()


def remove_from_watchlist(wallet_address: str) -> bool:
    """Remove a wallet from the watchlist. Returns True if deleted, False if not found."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM watchlist WHERE wallet_address = ?", (wallet_address,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return deleted


def get_watchlist() -> List[Dict[str, Any]]:
    """Get all watchlist entries ordered by added_at desc."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM watchlist
        ORDER BY added_at DESC
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def is_watched_wallet(wallet_address: str) -> bool:
    """Check if a wallet is on the watchlist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM watchlist WHERE wallet_address = ?", (wallet_address,))
    result = cursor.fetchone()
    conn.close()
    
    return result is not None


def update_watchlist_seen(wallet_address: str) -> bool:
    """Update last_seen_at and increment alert_count for a watched wallet."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE watchlist
        SET last_seen_at = ?, alert_count = alert_count + 1
        WHERE wallet_address = ?
    """, (datetime.now(timezone.utc).isoformat(), wallet_address))
    
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return updated


# ============================================================================
# User Watchlist Functions (Pro+ tier)
# ============================================================================

def add_to_user_watchlist(key_id: int, wallet_address: str, label: str = "", notes: str = "") -> bool:
    """Add a wallet to a user's personal watchlist. Returns True if added, False if already exists."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO user_watchlist (key_id, wallet_address, label, notes, added_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            key_id,
            wallet_address,
            label,
            notes,
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def remove_from_user_watchlist(key_id: int, wallet_address: str) -> bool:
    """Remove a wallet from a user's personal watchlist. Returns True if removed."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM user_watchlist WHERE key_id = ? AND wallet_address = ?", (key_id, wallet_address))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return deleted


def get_user_watchlist(key_id: int) -> List[Dict[str, Any]]:
    """Get all watchlist entries for a specific API key user. ORDER BY added_at DESC."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM user_watchlist
        WHERE key_id = ?
        ORDER BY added_at DESC
    """, (key_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def is_user_watched_wallet(key_id: int, wallet_address: str) -> bool:
    """Check if a wallet is on a user's watchlist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM user_watchlist WHERE key_id = ? AND wallet_address = ?", (key_id, wallet_address))
    result = cursor.fetchone()
    conn.close()
    
    return result is not None


def update_user_watchlist_seen(key_id: int, wallet_address: str):
    """Update last_seen_at and increment alert_count for a user's watched wallet."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE user_watchlist
        SET last_seen_at = ?, alert_count = alert_count + 1
        WHERE key_id = ? AND wallet_address = ?
    """, (datetime.now(timezone.utc).isoformat(), key_id, wallet_address))
    
    conn.commit()
    conn.close()


# ============================================================================
# Creator Reputation Functions
# ============================================================================

def get_creator_reputation(wallet_address: str) -> Optional[Dict[str, Any]]:
    """
    Get aggregated reputation data for a creator wallet.
    
    Returns:
        Dictionary with:
        - total_tokens_launched
        - avg_risk_score
        - high_risk_count (score >= 70)
        - critical_count (score >= 85)
        - tokens list (name, mint, score, risk_level, scored_at)
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            token_mint, name, symbol, risk_score, risk_level, scored_at
        FROM scored_tokens
        WHERE creator_wallet = ?
        ORDER BY scored_at DESC
    """, (wallet_address,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return None
    
    tokens = []
    total_score = 0
    high_risk_count = 0
    critical_count = 0
    
    for row in rows:
        token = dict(row)
        tokens.append(token)
        
        score = token.get("risk_score") or 0
        total_score += score
        
        if score >= 85:
            critical_count += 1
        elif score >= 70:
            high_risk_count += 1
    
    total_tokens = len(tokens)
    avg_risk_score = round(total_score / total_tokens, 1) if total_tokens > 0 else 0
    
    return {
        "wallet_address": wallet_address,
        "total_tokens_launched": total_tokens,
        "avg_risk_score": avg_risk_score,
        "high_risk_count": high_risk_count,
        "critical_count": critical_count,
        "tokens": tokens
    }


# ============================================================================
# Creator Leaderboard Functions
# ============================================================================

def get_creator_leaderboard(sort_by: str = "avg_risk", order: str = "desc", limit: int = 50, min_tokens: int = 2) -> List[Dict[str, Any]]:
    """Get creator leaderboard — aggregated stats for all creators with at least min_tokens launches.
    
    Args:
        sort_by: "avg_risk", "total_tokens", "high_risk_count", "last_active"
        order: "asc" or "desc"
        limit: max results
        min_tokens: minimum token launches to appear on leaderboard
    
    Returns list of dicts with: creator_wallet, total_tokens, avg_risk_score,
    high_risk_count, critical_count, low_risk_count, last_active
    """
    # Validate sort_by against whitelist to prevent SQL injection
    sort_column_map = {
        "avg_risk": "avg_risk_score",
        "total_tokens": "total_tokens",
        "high_risk_count": "high_risk_count",
        "last_active": "last_active"
    }
    validated_sort_column = sort_column_map.get(sort_by, "avg_risk_score")
    
    # Validate order
    validated_order = "DESC" if order.lower() != "asc" else "ASC"
    
    conn = get_connection()
    cursor = conn.cursor()
    
    query = f"""
        SELECT 
            creator_wallet,
            COUNT(*) as total_tokens,
            ROUND(AVG(risk_score), 1) as avg_risk_score,
            SUM(CASE WHEN risk_level = 'HIGH' THEN 1 ELSE 0 END) as high_risk_count,
            SUM(CASE WHEN risk_level = 'CRITICAL' THEN 1 ELSE 0 END) as critical_count,
            SUM(CASE WHEN risk_level = 'LOW' THEN 1 ELSE 0 END) as low_risk_count,
            MAX(scored_at) as last_active
        FROM scored_tokens
        WHERE creator_wallet IS NOT NULL AND creator_wallet != ''
        GROUP BY creator_wallet
        HAVING COUNT(*) >= ?
        ORDER BY {validated_sort_column} {validated_order}
        LIMIT ?
    """
    
    cursor.execute(query, (min_tokens, limit))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


# ============================================================================
# API Key Management Functions
# ============================================================================

def create_api_key(email: str, tier: str = "free", name: str = "") -> dict:
    """Generate a new API key. Returns the raw key (shown once) and metadata."""
    raw_key = f"sh_{uuid.uuid4()}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:11]  # "sh_" + first 8 chars of UUID
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO api_keys (key_hash, key_prefix, email, tier, name, created_at, last_reset_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key_hash, key_prefix, email, tier, name, now, now[:10])
        )
        conn.commit()
        return {
            "key": raw_key,
            "key_prefix": key_prefix,
            "email": email,
            "tier": tier,
            "name": name,
            "created_at": now
        }
    finally:
        conn.close()


def validate_api_key(raw_key: str) -> Optional[dict]:
    """Validate an API key. Returns key row dict if valid, None if invalid/expired/inactive."""
    if not raw_key or not raw_key.startswith("sh_"):
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
        ).fetchone()

        if not row:
            return None

        result = dict(row)

        # Check if active
        if not result.get("is_active"):
            return None

        # Check if expired
        if result.get("expires_at"):
            expires = datetime.fromisoformat(result["expires_at"])
            if datetime.now(timezone.utc) > expires:
                return None

        return result
    finally:
        conn.close()


def increment_api_key_usage(key_id: int, endpoint: str, status_code: int = 200, response_ms: int = 0, count: int = 1):
    """Increment usage counters and log the request."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        # Check if we need to reset daily counter
        row = conn.execute(
            "SELECT last_reset_date FROM api_keys WHERE id = ?", (key_id,)
        ).fetchone()

        if row and row["last_reset_date"] != today:
            conn.execute(
                "UPDATE api_keys SET calls_today = ?, calls_total = calls_total + ?, last_used_at = ?, last_reset_date = ? WHERE id = ?",
                (count, count, now.isoformat(), today, key_id)
            )
        else:
            conn.execute(
                "UPDATE api_keys SET calls_today = calls_today + ?, calls_total = calls_total + ?, last_used_at = ? WHERE id = ?",
                (count, count, now.isoformat(), key_id)
            )

        # Log usage
        conn.execute(
            "INSERT INTO api_usage_log (key_id, endpoint, status_code, response_ms) VALUES (?, ?, ?, ?)",
            (key_id, endpoint, status_code, response_ms)
        )
        conn.commit()
    finally:
        conn.close()


def reset_daily_counters():
    """Reset calls_today for all keys where last_reset_date is not today."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE api_keys SET calls_today = 0, last_reset_date = ? WHERE last_reset_date != ? OR last_reset_date IS NULL",
            (today, today)
        )
        conn.commit()
    finally:
        conn.close()


def get_api_keys_by_email(email: str) -> list:
    """Get all API keys for a given email."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, key_prefix, email, tier, name, calls_today, calls_total, last_used_at, is_active, created_at, expires_at FROM api_keys WHERE email = ?",
            (email,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revoke_api_key(key_id: int) -> bool:
    """Revoke an API key by setting is_active to False."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE api_keys SET is_active = FALSE WHERE id = ?", (key_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_all_api_keys() -> list:
    """Get all API keys with usage stats (admin)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, key_prefix, email, tier, name, calls_today, calls_total, last_used_at, is_active, created_at, expires_at FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_api_key_by_prefix(key_prefix: str) -> Optional[dict]:
    """Get a single API key by its prefix."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_prefix = ?", (key_prefix,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================================
# Score History Functions
# ============================================================================

def get_score_history(token_mint: str) -> List[Dict[str, Any]]:
    """Get all historical scores for a token, ordered by scored_at DESC."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT risk_score, risk_level, score_source, ai_verdict, scored_at FROM score_history WHERE token_mint = ? ORDER BY scored_at DESC",
        (token_mint,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def clear_all_scores():
    """Delete all scored tokens and score history. Returns count of deleted records."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM scored_tokens")
    scored_count = c.rowcount
    c.execute("DELETE FROM score_history")
    history_count = c.rowcount
    conn.commit()
    conn.close()
    return {"scored_deleted": scored_count, "history_deleted": history_count}


def create_or_update_user(google_id: str, email: str, name: str = None, picture_url: str = None) -> dict:
    """Create a new user or update existing one on login. Returns user dict."""
    conn = get_connection()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Check if user exists
    c.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
    existing = c.fetchone()

    if existing:
        # Update last login and profile info
        c.execute("""UPDATE users SET name = ?, picture_url = ?, last_login_at = ?
                     WHERE google_id = ?""", (name, picture_url, now, google_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
        result = dict(c.fetchone())
        conn.close()
        return result
    else:
        # Check if email is in admin list
        admin_emails = [e.strip() for e in os.environ.get("SCAMHOUND_ADMIN_EMAILS", "").split(",") if e.strip()]
        is_admin = email.lower() in [e.lower() for e in admin_emails]

        c.execute("""INSERT INTO users (google_id, email, name, picture_url, is_admin, created_at, last_login_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (google_id, email, name, picture_url, is_admin, now, now))
        conn.commit()
        c.execute("SELECT * FROM users WHERE id = ?", (c.lastrowid,))
        result = dict(c.fetchone())
        conn.close()
        return result


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Get user by ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_google_id(google_id: str) -> Optional[dict]:
    """Get user by Google ID."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE google_id = ?", (google_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def clear_user_scans(user_id: int) -> dict:
    """Delete all scans for a specific user. Returns count."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM scored_tokens WHERE user_id = ?", (user_id,))
    scored_count = c.rowcount
    conn.commit()
    conn.close()
    return {"scored_deleted": scored_count}


def get_scores_for_user(user_id: int, limit: int = 100, offset: int = 0) -> list:
    """Get scored tokens for a specific user."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""SELECT * FROM scored_tokens WHERE user_id = ?
                 ORDER BY scored_at DESC LIMIT ? OFFSET ?""", (user_id, limit, offset))
    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results


def check_and_increment_scan(user_id: int, is_admin: bool) -> dict:
    """
    Check if user can scan. If yes, increment counter.
    Returns {"allowed": True/False, "scans_today": N, "limit": N}
    Admin users are never rate limited.
    """
    if is_admin:
        return {"allowed": True, "scans_today": 0, "limit": -1}

    from datetime import date
    limit = int(os.environ.get("USER_DAILY_SCAN_LIMIT", "10"))
    today = date.today().isoformat()

    conn = get_connection()
    c = conn.cursor()
    try:
        # Serialize competing scan updates for this DB.
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "SELECT scans_today, last_scan_date FROM users WHERE id = ?",
            (user_id,),
        )
        row = c.fetchone()

        if not row:
            conn.rollback()
            return {"allowed": False, "scans_today": 0, "limit": limit}

        scans_today = row["scans_today"] or 0
        last_scan_date = row["last_scan_date"]

        # Reset if new day
        if last_scan_date != today:
            scans_today = 0

        if scans_today >= limit:
            conn.rollback()
            return {"allowed": False, "scans_today": scans_today, "limit": limit}

        # Increment
        new_scans_today = scans_today + 1
        c.execute(
            "UPDATE users SET scans_today = ?, last_scan_date = ? WHERE id = ?",
            (new_scans_today, today, user_id),
        )
        conn.commit()
        return {"allowed": True, "scans_today": new_scans_today, "limit": limit}
    finally:
        conn.close()


def get_tokens_for_rescore(max_age_days: int = 7, min_score: int = 40, limit: int = 25) -> List[Dict[str, Any]]:
    """Get tokens eligible for re-scoring.
    Criteria: risk_score >= min_score, first scored within last max_age_days,
    and last score_history entry is older than 24 hours.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT st.token_mint, st.name, st.symbol, st.risk_score, st.creator_wallet, st.scored_at
        FROM scored_tokens st
        WHERE st.risk_score >= ?
        AND st.scored_at >= datetime('now', ? || ' days')
        AND (
            SELECT MAX(sh.scored_at) FROM score_history sh WHERE sh.token_mint = st.token_mint
        ) < datetime('now', '-24 hours')
        ORDER BY st.risk_score DESC
        LIMIT ?
    """, (min_score, f'-{int(max_age_days)}', limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]