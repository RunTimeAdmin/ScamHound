"""
Tests for API key generation access control.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def test_generate_api_key_ignores_requested_cross_account_email(
    fastapi_test_client,
):
    """Endpoint should ignore requested email and bind to session user."""
    fake_user = {"id": 7, "email": "owner@example.com", "is_admin": False}

    with patch("dashboard.app.get_current_user", return_value=fake_user):
        with patch("engine.database.get_api_keys_by_email", return_value=[]):
            with patch(
                "engine.database.create_api_key",
                return_value={
                    "key": "sh_456",
                    "key_prefix": "sh_456",
                    "email": "owner@example.com",
                    "tier": "free",
                    "name": "bound",
                    "created_at": "2026-05-20T00:00:00Z",
                },
            ):
                response = fastapi_test_client.post(
                    "/api/keys/generate",
                    json={"email": "victim@example.com", "name": "bad-attempt"},
                )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True


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


def test_admin_endpoints_fail_closed_when_admin_token_missing(fastapi_test_client):
    """Admin-token endpoints must reject requests when token is unset."""
    with patch.dict("os.environ", {"SCAMHOUND_ADMIN_TOKEN": ""}, clear=False):
        response = fastapi_test_client.get("/api/keys/admin/list")

    assert response.status_code == 401


def test_key_generation_endpoint_is_rate_limited(fastapi_test_client):
    """Key generation should throttle repeated attempts per client IP."""
    from dashboard.routers import keys as keys_router

    keys_router._key_gen_attempts_by_ip.clear()
    with patch.object(keys_router, "_KEY_GEN_MAX_ATTEMPTS", 2):
        with patch("dashboard.app.get_current_user", return_value={"id": 1, "email": "user@example.com", "is_admin": False}):
            with patch("engine.database.get_api_keys_by_email", return_value=[]):
                with patch(
                    "engine.database.create_api_key",
                    return_value={
                        "key": "sh_abc",
                        "key_prefix": "sh_abc",
                        "email": "user@example.com",
                        "tier": "free",
                        "name": "",
                        "created_at": "2026-05-20T00:00:00Z",
                    },
                ):
                    first = fastapi_test_client.post("/api/keys/generate", json={})
                    second = fastapi_test_client.post("/api/keys/generate", json={})
                    third = fastapi_test_client.post("/api/keys/generate", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_batch_scan_charges_only_for_queued_work(fastapi_test_client):
    """Batch scan usage units should scale with uncached mints."""
    key_row = {
        "id": 99,
        "tier": "builder",
        "calls_today": 0,
    }
    mints = [
        "11111111111111111111111111111111",
        "11111111111111111111111111111112",
        "11111111111111111111111111111113",
    ]

    with patch("dashboard.app._check_api_key", return_value=(key_row, None)):
        with patch(
            "engine.database.get_score_by_mint",
            side_effect=[{"token_mint": mints[0]}, {"token_mint": mints[1]}, None],
        ):
            with patch(
                "engine.database.increment_api_key_usage"
            ) as increment_usage:
                with patch(
                    "dashboard.app.monitor.scan_single_token_async",
                    new_callable=AsyncMock,
                    return_value={"token_mint": mints[2]},
                ):
                    response = fastapi_test_client.post(
                        "/api/scan/batch",
                        json={"mints": mints},
                    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cached"] == 2
    assert payload["queued"] == 1
    assert payload["charged_units"] == 1
    increment_usage.assert_called_once_with(
        key_row["id"], "/api/scan/batch", count=1
    )
