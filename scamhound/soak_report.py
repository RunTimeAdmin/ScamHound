"""
Generate a local soak-period scoring quality report.

Usage:
    python scamhound/soak_report.py
    python scamhound/soak_report.py --sample-limit 50 --risk-level HIGH
    python scamhound/soak_report.py --json
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from engine import database


def build_soak_report(
    summary_limit: int,
    sample_limit: int,
    risk_level: str | None,
    randomize: bool,
) -> Dict[str, Any]:
    """Build soak report payload from database summary and sample data."""
    database.init_db()
    summary = database.get_soak_audit_summary(limit=summary_limit)
    samples = database.get_soak_audit_samples(
        limit=sample_limit,
        risk_level=risk_level,
        randomize=randomize,
    )
    return {
        "summary_limit": summary_limit,
        "sample_limit": sample_limit,
        "risk_level_filter": risk_level,
        "randomize_samples": randomize,
        "summary": summary,
        "samples": samples,
    }


def _print_human(report: Dict[str, Any]) -> None:
    summary = report["summary"]
    print("ScamHound Soak Report")
    print("====================")
    print(f"Summary sample size: {summary.get('sample_size', 0)}")
    print(f"Fallback count:      {summary.get('fallback_count', 0)}")
    print(f"Unscored count:      {summary.get('unscored_count', 0)}")
    print(f"Retried count:       {summary.get('retried_count', 0)}")
    print(f"Avg LLM attempts:    {summary.get('avg_llm_attempts', 0.0)}")
    print(
        "Unknown claims:      "
        f"creator={summary.get('unknown_creator_wallet_count', 0)}, "
        f"wallet_age={summary.get('unknown_wallet_age_count', 0)}, "
        f"token_age={summary.get('unknown_token_age_claim_count', 0)}"
    )
    print()

    risk_breakdown = summary.get("risk_level_breakdown", {})
    source_breakdown = summary.get("score_source_breakdown", {})
    print(f"Risk breakdown:      {risk_breakdown}")
    print(f"Source breakdown:    {source_breakdown}")
    print()

    print(
        "Samples "
        f"(count={len(report['samples'])}, "
        f"risk_level={report['risk_level_filter'] or 'any'}, "
        f"randomize={report['randomize_samples']}):"
    )
    for idx, sample in enumerate(report["samples"], start=1):
        token_mint = sample.get("token_mint")
        risk_level = sample.get("risk_level")
        risk_score = sample.get("risk_score")
        score_source = sample.get("score_source")
        llm_attempts = sample.get("llm_attempts")
        print(
            f"{idx:02d}. {token_mint} | score={risk_score} | "
            f"level={risk_level} | source={score_source} | "
            f"attempts={llm_attempts}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local soak report.")
    parser.add_argument(
        "--summary-limit",
        type=int,
        default=200,
        help="Recent rows used for summary metrics (default: 200).",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=50,
        help="Number of sample rows to print (default: 50).",
    )
    parser.add_argument(
        "--risk-level",
        type=str,
        default=None,
        help="Optional risk level filter for samples (LOW/MEDIUM/HIGH/CRITICAL/UNSCORED).",
    )
    parser.add_argument(
        "--randomize",
        action="store_true",
        help="Randomize sample selection (default: most recent).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full report as JSON.",
    )
    args = parser.parse_args()

    report = build_soak_report(
        summary_limit=args.summary_limit,
        sample_limit=args.sample_limit,
        risk_level=args.risk_level,
        randomize=args.randomize,
    )

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)


if __name__ == "__main__":
    main()
