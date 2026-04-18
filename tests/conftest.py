"""
ScamHound test fixtures.

Shared pytest fixtures for temp database, mock env vars, and FastAPI test
client.
"""

import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add the scamhound package to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))


@pytest.fixture
def temp_config_file(tmp_path):
    """Create a temporary config.json file for testing."""
    config_path = tmp_path / "config.json"
    return config_path


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    env_vars = {
        "BAGS_API_KEY": "test_bags_key",
        "HELIUS_API_KEY": "test_helius_key",
        "BIRDEYE_API_KEY": "test_birdeye_key",
        "BUBBLEMAPS_API_KEY": "test_bubblemaps_key",
        "ANTHROPIC_API_KEY": "test_anthropic_key",
        "RISK_ALERT_THRESHOLD": "65",
        "POLL_INTERVAL_SECONDS": "60",
        "SCAMHOUND_ADMIN_TOKEN": "test_admin_token",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def temp_database(tmp_path):
    """Create a temporary SQLite database for testing."""
    db_path = tmp_path / "test_scamhound.db"
    
    # Patch the DB_PATH in the database module
    with patch("engine.database.DB_PATH", str(db_path)):
        # Initialize the database
        from engine import database
        database.init_db()
        yield str(db_path)
        
    # Cleanup: remove the database file
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def mock_database_with_data(tmp_path):
    """Create a temporary database with sample token data."""
    db_path = tmp_path / "test_scamhound.db"
    
    with patch("engine.database.DB_PATH", str(db_path)):
        from engine import database
        database.init_db()
        
        # Insert sample data
        sample_scores = [
            {
                "token_mint": "TokenMint1111111111111111111111111111111111",
                "name": "Test Token 1",
                "symbol": "TEST1",
                "risk_score": 75,
                "risk_level": "HIGH",
                "verdict": "High concentration in top holders",
                "top_risk_factors": ["High concentration", "Low liquidity"],
                "top_safe_signals": ["Creator has history"],
                "top_10_concentration": 85.5,
                "creator_wallet": "CreatorWallet11111111111111111111111111111",
                "creator_username": "testcreator1",
                "prior_launches": 2,
                "wallet_age_days": 30,
                "clustering_score": 0.6,
                "liquidity_usd": 5000.0,
                "lifetime_fees_sol": 1.5,
                "created_at": "2024-01-01T00:00:00Z"
            },
            {
                "token_mint": "TokenMint2222222222222222222222222222222222",
                "name": "Test Token 2",
                "symbol": "TEST2",
                "risk_score": 25,
                "risk_level": "LOW",
                "verdict": "Healthy distribution pattern",
                "top_risk_factors": [],
                "top_safe_signals": ["Good liquidity",
                                     "Decentralized holders"],
                "top_10_concentration": 25.0,
                "creator_wallet": "CreatorWallet22222222222222222222222222222",
                "creator_username": "testcreator2",
                "prior_launches": 0,
                "wallet_age_days": 365,
                "clustering_score": 0.1,
                "liquidity_usd": 50000.0,
                "lifetime_fees_sol": 10.0,
                "created_at": "2024-01-02T00:00:00Z"
            },
            {
                "token_mint": "TokenMint3333333333333333333333333333333333",
                "name": "Test Token 3",
                "symbol": "TEST3",
                "risk_score": 90,
                "risk_level": "CRITICAL",
                "verdict": "Multiple red flags detected",
                "top_risk_factors": ["New wallet", "High clustering",
                                     "Single holder"],
                "top_safe_signals": [],
                "top_10_concentration": 95.0,
                "creator_wallet": "CreatorWallet33333333333333333333333333333",
                "creator_username": "testcreator3",
                "prior_launches": 5,
                "wallet_age_days": 2,
                "clustering_score": 0.8,
                "liquidity_usd": 100.0,
                "lifetime_fees_sol": 0.1,
                "created_at": "2024-01-03T00:00:00Z"
            }
        ]
        
        for score in sample_scores:
            database.save_score(score)
        
        yield str(db_path)
        
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def fastapi_test_client(mock_env_vars, tmp_path):
    """Create a FastAPI test client with mocked dependencies."""
    # Use a temp database
    db_path = tmp_path / "test_dashboard.db"
    
    with patch("engine.database.DB_PATH", str(db_path)):
        from fastapi.testclient import TestClient
        from dashboard.app import app
        
        # Initialize database
        from engine import database
        database.init_db()
        
        client = TestClient(app)
        yield client
        
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def mock_requests_get():
    """Mock requests.get for client testing."""
    with patch("requests.get") as mock_get:
        yield mock_get


@pytest.fixture
def mock_requests_post():
    """Mock requests.post for client testing."""
    with patch("requests.post") as mock_post:
        yield mock_post


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client for scorer testing."""
    with patch("anthropic.Anthropic") as mock_client_class:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "risk_score": 50,
            "risk_level": "MEDIUM",
            "verdict": "Test verdict from mock",
            "top_risk_factors": ["Test risk factor"],
            "top_safe_signals": ["Test safe signal"]
        }))]
        mock_client.messages.create.return_value = mock_response
        mock_client_class.return_value = mock_client
        yield mock_client_class


@pytest.fixture(autouse=True)
def reset_config_module():
    """Reset config module state between tests."""
    # Store original env vars
    original_env = dict(os.environ)
    
    yield
    
    # Restore original env vars (only config-related keys)
    config_keys = [
        "BAGS_API_KEY", "HELIUS_API_KEY", "BIRDEYE_API_KEY",
        "BUBBLEMAPS_API_KEY", "ANTHROPIC_API_KEY", "TWITTER_API_KEY",
        "TWITTER_API_SECRET", "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_SECRET", "TWITTER_BEARER_TOKEN",
        "RISK_ALERT_THRESHOLD", "POLL_INTERVAL_SECONDS",
        "SCAMHOUND_ADMIN_TOKEN"
    ]
    
    for key in config_keys:
        if key in original_env:
            os.environ[key] = original_env[key]
        elif key in os.environ:
            del os.environ[key]
