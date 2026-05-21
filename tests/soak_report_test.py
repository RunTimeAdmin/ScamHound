"""
Tests for local soak report generator.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scamhound"))

import soak_report  # noqa: E402


def test_build_soak_report_uses_database_helpers(monkeypatch):
    summary_payload = {"sample_size": 7, "fallback_count": 1}
    sample_payload = [{"token_mint": "TokenA"}]
    calls = {}

    def fake_init_db():
        calls["init"] = True

    def fake_summary(limit):
        calls["summary_limit"] = limit
        return summary_payload

    def fake_samples(limit, risk_level, randomize):
        calls["sample_limit"] = limit
        calls["risk_level"] = risk_level
        calls["randomize"] = randomize
        return sample_payload

    monkeypatch.setattr(soak_report.database, "init_db", fake_init_db)
    monkeypatch.setattr(
        soak_report.database,
        "get_soak_audit_summary",
        fake_summary,
    )
    monkeypatch.setattr(
        soak_report.database,
        "get_soak_audit_samples",
        fake_samples,
    )

    report = soak_report.build_soak_report(
        summary_limit=200,
        sample_limit=50,
        risk_level="HIGH",
        randomize=False,
    )

    assert calls == {
        "init": True,
        "summary_limit": 200,
        "sample_limit": 50,
        "risk_level": "HIGH",
        "randomize": False,
    }
    assert report["summary"] == summary_payload
    assert report["samples"] == sample_payload
