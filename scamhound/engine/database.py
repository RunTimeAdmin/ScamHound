"""
ScamHound Database Module
SQLite persistence for token scores and alert tracking
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.getenv("DB_PATH", "scamhound.db")


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the scored_tokens table if it doesn't exist."""
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
    
    conn.commit()
    conn.close()
    print("[SCAMHOUND] Database initialized")


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


def save_score(score_data: Dict[str, Any]) -> None:
    """Insert a new token score into the database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Convert lists to JSON strings for storage
    import json
    risk_factors = json.dumps(score_data.get("top_risk_factors", []))
    safe_signals = json.dumps(score_data.get("top_safe_signals", []))
    
    cursor.execute("""
        INSERT OR REPLACE INTO scored_tokens (
            token_mint, name, symbol, risk_score, risk_level, ai_verdict,
            top_risk_factors, top_safe_signals, top_10_concentration,
            creator_wallet, creator_username, prior_launches, wallet_age_days,
            clustering_score, liquidity_usd, lifetime_fees_sol, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        score_data.get("created_at")
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
    
    import json
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
    
    import json
    result = dict(row)
    result["top_risk_factors"] = json.loads(result.get("top_risk_factors", "[]"))
    result["top_safe_signals"] = json.loads(result.get("top_safe_signals", "[]"))
    
    return result


def get_high_risk_unnotified(threshold: int = 65) -> List[Dict[str, Any]]:
    """Get high-risk tokens that haven't been tweeted yet."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM scored_tokens 
        WHERE risk_score >= ? AND tweet_sent = FALSE
        ORDER BY risk_score DESC
    """, (threshold,))
    
    rows = cursor.fetchall()
    conn.close()
    
    import json
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