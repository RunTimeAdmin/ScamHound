"""
Regression tests for clear scans endpoint error handling.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))


def test_clear_scores_returns_json_on_internal_error(fastapi_test_client):
    """Endpoint should return JSON body (not plain text) on failures."""
    with patch(
        "dashboard.app.get_current_user",
        return_value={"id": 1, "email": "u@example.com", "is_admin": False},
    ):
        with patch(
            "dashboard.app.database.clear_user_scans",
            side_effect=RuntimeError("db fail"),
        ):
            response = fastapi_test_client.delete("/api/scores/clear")

    assert response.status_code == 500
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "Internal Server Error"
