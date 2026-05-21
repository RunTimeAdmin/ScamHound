"""
Tests for API key generation access control.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))


def test_generate_api_key_requires_authenticated_session(fastapi_test_client):
    """Anonymous callers should be blocked from key generation."""
    response = fastapi_test_client.post(
        "/api/keys/generate",
        json={"email": "a@b.com"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert "Authentication required" in payload["error"]


def test_generate_api_key_rejects_cross_account_for_non_admin(
    fastapi_test_client,
):
    """Non-admin users cannot generate API keys for another email."""
    fake_user = {"id": 7, "email": "owner@example.com", "is_admin": False}

    with patch("dashboard.app.get_current_user", return_value=fake_user):
        response = fastapi_test_client.post(
            "/api/keys/generate",
            json={"email": "victim@example.com", "name": "bad-attempt"},
        )

    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert "another user" in payload["error"]


def test_generate_api_key_defaults_to_authenticated_user_email(
    fastapi_test_client,
):
    """When email is omitted, endpoint should use auth user email."""
    fake_user = {"id": 8, "email": "member@example.com", "is_admin": False}

    with patch("dashboard.app.get_current_user", return_value=fake_user):
        with patch("engine.database.get_api_keys_by_email", return_value=[]):
            with patch(
                "engine.database.create_api_key",
                return_value={
                    "key": "sh_123",
                    "key_prefix": "sh_123",
                    "email": "member@example.com",
                    "tier": "free",
                    "name": "my-key",
                    "created_at": "2026-05-20T00:00:00Z",
                },
            ):
                response = fastapi_test_client.post(
                    "/api/keys/generate",
                    json={"name": "my-key"},
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["tier"] == "free"


def test_global_watchlist_endpoints_require_admin_auth(fastapi_test_client):
    """Global watchlist routes must reject anonymous callers."""
    assert fastapi_test_client.get("/api/watchlist").status_code == 401
    assert (
        fastapi_test_client.post(
            "/api/watchlist",
            json={"wallet_address": "Watch1111111111111111111111111111111111"},
        ).status_code
        == 401
    )
    assert (
        fastapi_test_client.delete(
            "/api/watchlist/Watch1111111111111111111111111111111111"
        ).status_code
        == 401
    )


def test_global_watchlist_allows_legacy_admin_token_fallback(
    fastapi_test_client,
):
    """Legacy token path should still authorize global watchlist access."""
    with patch("dashboard.app.get_current_user", return_value=None):
        with patch("dashboard.app._verify_auth", return_value=True):
            create_resp = fastapi_test_client.post(
                "/api/watchlist",
                json={
                    "wallet_address": "Watch2222222222222222222222222222222222",
                    "label": "legacy",
                },
            )
            list_resp = fastapi_test_client.get("/api/watchlist")
            delete_resp = fastapi_test_client.delete(
                "/api/watchlist/Watch2222222222222222222222222222222222"
            )

    assert create_resp.status_code == 200
    assert create_resp.json()["success"] is True
    assert list_resp.status_code == 200
    assert delete_resp.status_code == 200
