"""
Tests for monitor sync wrapper hardening.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

from engine import monitor  # noqa: E402


def test_scan_single_token_runs_with_local_loop_for_sync_callers():
    """Sync callers should execute scan via a local event loop."""
    with patch.object(
        monitor, "scan_single_token_async", return_value={"token_mint": "x"}
    ) as mock_scan:
        result = monitor.scan_single_token("mint123")

    assert result == {"token_mint": "x"}
    mock_scan.assert_called_once_with("mint123", True)


@pytest.mark.asyncio
async def test_scan_single_token_rejects_active_event_loop_callers():
    """Async callers must use scan_single_token_async directly."""
    with pytest.raises(RuntimeError, match="active event loop"):
        monitor.scan_single_token("mint123")
    await asyncio.sleep(0)
