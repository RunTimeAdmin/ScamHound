"""
Tests for OAuth initialization hardening.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))


def _fresh_auth_module():
    """Reload auth module to reset OAuth initialization globals."""
    import auth

    return importlib.reload(auth)


def test_init_oauth_disabled_when_no_google_credentials():
    """When no Google env vars are set, OAuth should stay disabled."""
    auth = _fresh_auth_module()
    with patch.dict(auth.os.environ, {}, clear=True):
        assert auth.init_oauth() is False
        assert auth.is_oauth_enabled() is False


def test_init_oauth_raises_on_partial_configuration():
    """Partial OAuth credentials should fail loudly at startup."""
    auth = _fresh_auth_module()
    with patch.dict(
        auth.os.environ,
        {"GOOGLE_CLIENT_ID": "abc.apps.googleusercontent.com"},
        clear=True,
    ):
        try:
            auth.init_oauth()
            assert False, (
                "Expected RuntimeError for partial OAuth configuration"
            )
        except RuntimeError as exc:
            assert "both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET" in str(exc)
