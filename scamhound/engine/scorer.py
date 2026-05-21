"""
ScamHound Scoring Engine
Uses LLM AI to analyze token data and generate risk scores.
Supports multiple providers: Anthropic Claude (default) and DeepSeek.
"""

import json
import logging
import os
import time
from typing import Dict, Any
from datetime import datetime, timezone

import anthropic
from config import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

# Anthropic client singleton
_anthropic_client = None
_anthropic_client_key = None

# DeepSeek client singleton
_deepseek_client = None
_deepseek_api_key = None


def _get_anthropic_client():
    """Get or create Anthropic client singleton."""
    global _anthropic_client, _anthropic_client_key
    from config import get_config
    key = get_config("ANTHROPIC_API_KEY")
    if key and (key != _anthropic_client_key or _anthropic_client is None):
        _anthropic_client = anthropic.Anthropic(api_key=key)
        _anthropic_client_key = key
    return _anthropic_client


def _get_deepseek_client():
    """Get or create DeepSeek client (OpenAI-compatible)."""
    global _deepseek_client, _deepseek_api_key

    current_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if _deepseek_client is None or current_key != _deepseek_api_key:
        if not current_key:
            return None
        from openai import OpenAI
        _deepseek_client = OpenAI(
            api_key=current_key,
            base_url="https://api.deepseek.com"
        )
        _deepseek_api_key = current_key

    return _deepseek_client


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the configured LLM provider and return the response text."""
    provider = get_config("LLM_PROVIDER", "anthropic").lower()

    if provider == "deepseek":
        return _call_deepseek(system_prompt, user_prompt)
    else:
        return _call_anthropic(system_prompt, user_prompt)


def _call_deepseek(system_prompt: str, user_prompt: str) -> str:
    """Call DeepSeek API (OpenAI-compatible)."""
    client = _get_deepseek_client()
    if not client:
        raise ValueError("DEEPSEEK_API_KEY not configured")

    model = get_config("DEEPSEEK_MODEL", "deepseek-chat")

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1,
        max_tokens=1000,
        response_format={"type": "json_object"}
    )

    return response.choices[0].message.content


def _call_anthropic(system_prompt: str, user_prompt: str) -> str:
    """Call Anthropic Claude API."""
    client = _get_anthropic_client()
    if not client:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    model = get_config("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.content[0].text


def _get_retry_settings() -> tuple[int, float]:
    """Return retry settings for transient LLM failures."""
    try:
        retries = int(os.environ.get("LLM_MAX_RETRIES", "2"))
    except ValueError:
        retries = 2
    retries = max(0, retries)

    try:
        backoff_seconds = float(os.environ.get("LLM_RETRY_BACKOFF_SECONDS", "1.0"))
    except ValueError:
        backoff_seconds = 1.0
    backoff_seconds = max(0.0, backoff_seconds)

    return retries, backoff_seconds


def _coerce_risk_score(value: Any, default: int = 50) -> int:
    """Coerce model output risk score into bounded integer [0, 100]."""
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        score = default
    return max(0, min(100, score))


def _risk_level_from_score(score: int) -> str:
    """Derive canonical risk level from score ranges."""
    if score <= 30:
        return "LOW"
    if score <= 60:
        return "MEDIUM"
    if score <= 80:
        return "HIGH"
    return "CRITICAL"


def _normalize_risk_level(value: Any, score: int) -> str:
    """Normalize risk level string and fallback to score-derived level."""
    level = str(value or "").strip().upper()
    if level in ALLOWED_RISK_LEVELS:
        return level
    return _risk_level_from_score(score)


def _normalize_string_list(
    value: Any,
    max_items: int,
    max_item_length: int,
) -> list[str]:
    """Normalize list-like model output into bounded clean strings."""
    if not isinstance(value, list):
        return []

    normalized = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text[:max_item_length])
        if len(normalized) >= max_items:
            break
    return normalized


def _parse_llm_json_response(response_text: str) -> Dict[str, Any]:
    """Parse JSON object from LLM output with tolerant extraction."""
    text = (response_text or "").strip()

    # Fast path: already clean JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fenced markdown block
    if "```json" in text:
        candidate = text.split("```json", 1)[1].split("```", 1)[0].strip()
        return json.loads(candidate)
    if "```" in text:
        candidate = text.split("```", 1)[1].split("```", 1)[0].strip()
        return json.loads(candidate)

    # Fallback: extract first syntactically balanced JSON object.
    candidate = _extract_first_json_object(text)
    if candidate:
        return json.loads(candidate)

    # Preserve original parse exception semantics for callers
    return json.loads(text)


def _extract_first_json_object(text: str) -> str:
    """Extract the first balanced JSON object from noisy text."""
    start = -1
    depth = 0
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if start == -1:
            if ch == "{":
                start = i
                depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    return ""


def _sanitize_verdict(verdict: str, token_data: Dict[str, Any]) -> str:
    """Remove maturity claims that contradict computed token age."""
    cleaned = (verdict or "").strip()
    if not cleaned:
        return "Analysis complete."

    token_age_minutes = token_data.get("token_age_minutes")
    if not isinstance(token_age_minutes, (int, float)):
        return cleaned

    lower = cleaned.lower()
    contradicts_age = (
        token_age_minutes >= 60
        and (
            "brand new" in lower
            or "0 minutes old" in lower
            or "very new token" in lower
        )
    )
    if not contradicts_age:
        return cleaned

    age_hours = int(token_age_minutes // 60)
    if age_hours >= 24:
        age_label = f"{age_hours // 24} days"
    else:
        age_label = f"{age_hours} hours"

    return (
        f"Token age data indicates approximately {age_label} since launch. "
        "Risk assessment is based on current holder, creator, and liquidity signals."
    )


def _sanitize_risk_factors(
    factors: list[str], token_data: Dict[str, Any]
) -> list[str]:
    """Remove known non-risk or contradictory factors from model output."""
    wallet_age_days = token_data.get("wallet_age_days")
    token_age_minutes = token_data.get("token_age_minutes")

    sanitized = []
    for factor in factors:
        text = factor.strip()
        lower = text.lower()

        if isinstance(wallet_age_days, (int, float)) and wallet_age_days >= 0:
            if "wallet age unknown" in lower:
                continue
        if "bubblemaps" in lower and (
            "no " in lower
            or "unavailable" in lower
            or "missing" in lower
            or "not available" in lower
        ):
            continue
        if isinstance(token_age_minutes, (int, float)) and token_age_minutes >= 60:
            if "very early stage" in lower or "brand new" in lower:
                continue

        sanitized.append(text)
    return sanitized[:5]


def _sanitize_safe_signals(
    signals: list[str], token_data: Dict[str, Any]
) -> list[str]:
    """Drop safe claims that are unsupported when key source data is missing."""
    creator_wallet = token_data.get("creator", {}).get("wallet")
    wallet_age_days = token_data.get("wallet_age_days")
    has_creator_wallet = _has_known_value(creator_wallet)
    has_wallet_age = isinstance(wallet_age_days, (int, float)) and wallet_age_days >= 0

    sanitized = []
    for signal in signals:
        text = signal.strip()
        lower = text.lower()
        if "no prior rug" in lower and not has_creator_wallet:
            continue
        if "no creator history" in lower and (not has_creator_wallet or not has_wallet_age):
            continue
        sanitized.append(text)
    return sanitized[:5]


def _has_known_value(value: Any) -> bool:
    """Return True when value is present and not an unknown placeholder."""
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in {"", "unknown", "none", "n/a", "-"}


def _apply_due_diligence_guard(
    score_data: Dict[str, Any], token_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Avoid overconfident low-risk scores when core diligence data is missing."""
    unknown_count = 0

    if "token_age_minutes" in token_data:
        token_age_minutes = token_data.get("token_age_minutes")
        if not isinstance(token_age_minutes, (int, float)):
            unknown_count += 1

    if "wallet_age_days" in token_data:
        wallet_age_days = token_data.get("wallet_age_days")
        if not isinstance(wallet_age_days, (int, float)) or wallet_age_days < 0:
            unknown_count += 1

    creator = token_data.get("creator", {})
    if isinstance(creator, dict) and "wallet" in creator:
        creator_wallet = creator.get("wallet")
        if not _has_known_value(creator_wallet):
            unknown_count += 1

    if "unique_trader_count" in token_data:
        unique_traders = token_data.get("unique_trader_count")
        if not isinstance(unique_traders, (int, float)) or unique_traders <= 0:
            unknown_count += 1
    floor = 0
    if unknown_count >= 3:
        floor = 45
    elif unknown_count >= 1:
        floor = 35

    if floor and score_data["risk_score"] < floor:
        score_data["risk_score"] = floor
        score_data["risk_level"] = _risk_level_from_score(floor)

    if unknown_count >= 2:
        coverage_note = (
            "Limited due diligence data coverage (missing token age, creator age, "
            "or trading activity signals)."
        )
        factors = list(score_data.get("top_risk_factors", []))
        if coverage_note not in factors:
            factors.append(coverage_note)
            score_data["top_risk_factors"] = factors[:5]

        verdict = str(score_data.get("verdict") or "").strip()
        if "limited due diligence data coverage" not in verdict.lower():
            if verdict and not verdict.endswith("."):
                verdict += "."
            score_data["verdict"] = (
                f"{verdict} Confidence is limited due to missing due diligence data."
            ).strip()

    return score_data


def _apply_security_control_weights(
    score_data: Dict[str, Any], token_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply deterministic risk weights for hard security controls."""
    security = token_data.get("security", {})
    lp_controls = token_data.get("lp_controls", {})
    creator_wallet = token_data.get("creator", {}).get("wallet")

    additions = 0
    enforced_factors: list[str] = []

    mint_renounced = security.get("mint_authority_renounced")
    if mint_renounced is False:
        additions += 25
        enforced_factors.append(
            "Mint authority is active; token supply can be inflated."
        )

    freeze_renounced = security.get("freeze_authority_renounced")
    freeze_whitelisted = bool(security.get("freeze_authority_whitelisted", False))
    if freeze_renounced is False and not freeze_whitelisted:
        additions += 30
        enforced_factors.append(
            "Freeze authority is active for a non-whitelisted mint."
        )

    permanent_delegate = security.get("permanent_delegate")
    if _has_known_value(permanent_delegate):
        additions += 20
        enforced_factors.append(
            "Token has a permanent delegate with transfer control powers."
        )

    transfer_fee_bps = security.get("transfer_fee_bps")
    if isinstance(transfer_fee_bps, (int, float)):
        if transfer_fee_bps >= 3000:
            additions += 30
            enforced_factors.append(
                f"Extreme transfer fee configured ({int(transfer_fee_bps)} bps)."
            )
        elif transfer_fee_bps >= 500:
            additions += 15
            enforced_factors.append(
                f"Elevated transfer fee configured ({int(transfer_fee_bps)} bps)."
            )

    token_extensions = security.get("token_2022_extensions", [])
    risky_ext = {
        "transferhook",
        "nontransferable",
        "mintcloseauthority",
        "interestbearingmint",
    }
    normalized_ext = {str(x).lower() for x in token_extensions}
    matched = [ext for ext in risky_ext if any(ext in e for e in normalized_ext)]
    if matched:
        additions += 10
        enforced_factors.append(
            "Token-2022 risky extension footprint detected: "
            + ", ".join(sorted(matched))
            + "."
        )

    lp_locked = lp_controls.get("lp_locked")
    lp_burned = lp_controls.get("lp_burned")
    lp_unlocked_creator_controlled = lp_controls.get(
        "lp_unlocked_creator_controlled"
    )
    if lp_locked is False and lp_burned is False:
        additions += 15
        enforced_factors.append(
            "LP appears unlocked and unburned based on available metadata."
        )
    if lp_unlocked_creator_controlled:
        additions += 30
        enforced_factors.append(
            "Creator appears to control unlocked LP supply."
        )

    if bool(token_data.get("honeypot_suspected")):
        additions += 25
        enforced_factors.append(
            "Trade-flow heuristic indicates potential sell restriction/honeypot."
        )

    simulation_status = str(token_data.get("honeypot_simulation_status") or "")
    if simulation_status == "sell_quote_unavailable":
        additions += 40
        enforced_factors.append(
            "Jupiter round-trip check found buy route but no sell route."
        )
    elif simulation_status == "high_round_trip_loss":
        additions += 35
        enforced_factors.append(
            "Jupiter round-trip check indicates extreme immediate sell loss."
        )

    if bool(token_data.get("bundle_launch_suspected")):
        additions += 25
        enforced_factors.append(
            "Launch activity resembles coordinated wallet bundle/sniping."
        )

    wash_cycle_count = int(token_data.get("wash_trade_cycle_count", 0) or 0)
    if bool(token_data.get("wash_trade_suspected")) or wash_cycle_count >= 2:
        additions += 20
        enforced_factors.append(
            "Detected repeated wallet-to-wallet round-trip wash-trade cycles."
        )

    update_authority = security.get("update_authority")
    if _has_known_value(update_authority) and _has_known_value(creator_wallet):
        if str(update_authority) == str(creator_wallet):
            additions += 15
            enforced_factors.append(
                "Metadata update authority is controlled by creator wallet."
            )

    if additions > 0:
        score_data["risk_score"] = min(100, score_data["risk_score"] + additions)
        score_data["risk_level"] = _risk_level_from_score(score_data["risk_score"])
        current = list(score_data.get("top_risk_factors", []))
        for factor in enforced_factors:
            if factor not in current:
                current.append(factor)
        score_data["top_risk_factors"] = current[:5]

    return score_data



SYSTEM_PROMPT = """You are ScamHound, an expert crypto security analyst specializing in rug pull detection on the Solana blockchain. You analyze token data and return a structured risk assessment.

You MUST respond with valid JSON only. No preamble, no explanation outside the JSON.

JSON format:
{
    "risk_score": <integer 0-100>,
    "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
    "verdict": "<2-3 sentence plain English explanation of the key risks or why the token looks clean>",
    "top_risk_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
    "top_safe_signals": ["<signal 1>", "<signal 2>"]
}

Risk score guide:
0-30 = LOW (token shows healthy signals)
31-60 = MEDIUM (some concerns, proceed with caution)
61-80 = HIGH (multiple red flags, high risk of rug pull)
81-100 = CRITICAL (strong indicators of imminent rug pull)

TOKEN MATURITY GUIDELINES - CRITICAL:
A brand new token (minutes old) with 1 holder and no liquidity is NORMAL — do NOT penalize for this.

- Tokens launched < 10 minutes ago: High concentration and zero liquidity are EXPECTED. Focus scoring on: creator wallet history, prior rug pulls, wallet clustering patterns, and contract anomalies.
- Tokens 10-60 minutes old: Some distribution should be happening. High concentration becomes a mild concern.
- Tokens > 1 hour old: High concentration and zero liquidity are now genuine red flags.
- Tokens > 24 hours old: These patterns are severe warnings.

Key risk factors to weigh heavily (adjusted for token age):
- Top 10 holder concentration >80% = critical (ONLY if token > 30 min old), >60% = high (ONLY if token > 30 min old)
- Single holder with 100% after 30+ minutes = suspicious
- Zero liquidity after 1+ hour = red flag
- Creator wallet age <7 days = critical, <30 days = high (ALWAYS matters)
- Any prior rug pulls from wallet = critical (ALWAYS matters)
- Holder wallet clustering score >0.6 = critical, >0.4 = high (ALWAYS matters - creator controlling multiple wallets is suspicious even for new tokens)
- Mint authority not renounced = elevated rug risk (supply can be inflated)
- Freeze authority not renounced = elevated control/censorship risk
- Token-2022 with dangerous extensions (transfer hooks, permanent delegate, transfer fees) = increased complexity/risk
- LP unlocked/unburned when lock evidence exists = elevated exit-liquidity risk
- Buy-heavy / zero-sell flow with enough traders can indicate sell restrictions (honeypot-like behavior)
- Jupiter round-trip buy/sell simulation failure is a critical honeypot signal
- Liquidity/MCap ratio <0.05 = critical, <0.10 = high (ONLY if token > 1 hour old)
- Two-sided trader activity ratio >0.7 = critical, >0.5 = high (heuristic signal only; not definitive manipulation proof)
- Large sell pressure = high
- Creator royalty >5% = medium concern
- BubbleMaps decentralization score <30 = critical, <50 = high (indicates centralized control)
- BubbleMaps largest cluster share >70% = critical, >50% = high (possible coordinated wallets)

For very new tokens (< 10 min), focus your analysis on:
1. Creator wallet history - has this wallet launched before? Any abandoned tokens?
2. Wallet clustering - is the creator controlling multiple "holder" wallets?
3. Contract setup anomalies - unusual royalties, locked functions

Be thorough but decisive. Traders need clear signals."""


def build_user_prompt(token_data: Dict[str, Any]) -> str:
    """Build the user prompt with token data."""
    
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "UNKNOWN")
    token_mint = token_data.get("token_mint", "")
    created_at = token_data.get("created_at", "Unknown")
    
    # Token maturity data
    token_age_minutes = token_data.get("token_age_minutes")
    token_status = token_data.get("token_status", "unknown")
    
    # Build maturity note based on age
    maturity_note = ""
    if token_age_minutes is not None:
        if token_age_minutes < 10:
            maturity_note = "VERY NEW TOKEN - concentration metrics are expected to be extreme. Focus on creator history and wallet clustering."
        elif token_age_minutes < 60:
            maturity_note = "New token - some distribution should be starting. Monitor concentration trends."
        elif token_age_minutes < 1440:  # < 24 hours
            maturity_note = "Token is hours old - high concentration and low liquidity are now concerning."
        else:
            maturity_note = "Token is over 24 hours old - extreme concentration is a severe warning sign."
    else:
        maturity_note = "Token age unknown - assess based on available data."
    
    # Creator data
    creator = token_data.get("creator", {})
    creator_wallet = creator.get("wallet", "Unknown")
    creator_username = creator.get("username", "Unknown")
    royalty_pct = creator.get("royalty_pct", 0)
    
    # Holder data
    holders = token_data.get("holders", {})
    top_holders = holders.get("top_holders", [])
    top_10_concentration = holders.get("top_10_concentration_pct", 0)
    total_holders = holders.get("total_holder_count")
    top1_pct = holders.get("top1_pct", 0)
    top5_pct = holders.get("top5_pct", 0)
    concentration_score = holders.get("concentration_score", "unknown")
    is_pumpfun = holders.get("is_pumpfun", False)
    bonding_curve_excluded = holders.get("bonding_curve_excluded", False)
    
    # On-chain data
    wallet_age = token_data.get("wallet_age_days", -1)
    prior_launches = token_data.get("prior_launch_count", 0)
    abandoned = token_data.get("abandoned_tokens", [])
    clustering_score = token_data.get("clustering_score", 0)
    
    # Market data
    liquidity_usd = token_data.get("liquidity_usd", 0)
    liquidity_ratio = token_data.get("liquidity_to_mcap_ratio", 0)
    unique_traders = token_data.get("unique_trader_count", 0)
    two_sided_ratio = token_data.get(
        "two_sided_trader_activity_ratio",
        token_data.get("two_sided_trader_ratio", 0),
    )
    large_sell = token_data.get("large_sell_pressure", False)
    lifetime_fees = token_data.get("lifetime_fees_sol", 0)
    honeypot_suspected = bool(token_data.get("honeypot_suspected", False))
    buy_count = int(token_data.get("buy_count", 0) or 0)
    sell_count = int(token_data.get("sell_count", 0) or 0)
    honeypot_simulation_status = token_data.get("honeypot_simulation_status")
    honeypot_round_trip_loss_pct = token_data.get("honeypot_round_trip_loss_pct")
    bundle_launch_suspected = token_data.get("bundle_launch_suspected")
    bundle_same_slot_or_window = token_data.get("bundle_same_slot_or_window")
    bundle_amount_clustered = token_data.get("bundle_amount_clustered")
    bundle_funded_by_creator_count = token_data.get(
        "bundle_funded_by_creator_count"
    )
    wash_trade_cycle_count = token_data.get("wash_trade_cycle_count")
    wash_trade_suspected = token_data.get("wash_trade_suspected")

    # Token security controls
    security = token_data.get("security", {})
    mint_authority_renounced = security.get("mint_authority_renounced")
    freeze_authority_renounced = security.get("freeze_authority_renounced")
    is_token_2022 = bool(security.get("is_token_2022", False))
    token_2022_extensions = security.get("token_2022_extensions", [])
    token_program_owner = security.get("token_program_owner", "Unknown")
    update_authority = security.get("update_authority")
    transfer_fee_bps = security.get("transfer_fee_bps")
    transfer_fee_max = security.get("transfer_fee_max")
    permanent_delegate = security.get("permanent_delegate")
    freeze_authority_whitelisted = security.get("freeze_authority_whitelisted")
    freeze_authority_high_risk = security.get("freeze_authority_high_risk")

    lp_controls = token_data.get("lp_controls", {})
    lp_locked = lp_controls.get("lp_locked")
    lp_burned = lp_controls.get("lp_burned")
    lp_unlocked_creator_controlled = lp_controls.get(
        "lp_unlocked_creator_controlled"
    )
    lp_burned_share_pct = lp_controls.get("lp_burned_share_pct")
    lp_locked_share_pct = lp_controls.get("lp_locked_share_pct")
    lp_creator_share_pct = lp_controls.get("lp_creator_share_pct")
    
    # BubbleMaps data
    bubblemaps = token_data.get("bubblemaps", {})
    has_bubblemaps_data = bubblemaps and bubblemaps.get("decentralization_score") is not None
    decentralization_score = bubblemaps.get("decentralization_score", 0)
    cluster_count = bubblemaps.get("cluster_count", 0)
    largest_cluster_share = bubblemaps.get("largest_cluster_share", 0)
    bubblemaps_risk_signal = bubblemaps.get("risk_signal", "NOT_AVAILABLE")
    
    # Format age string
    if token_age_minutes is not None:
        if token_age_minutes < 60:
            age_str = f"{token_age_minutes} minutes"
        elif token_age_minutes < 1440:
            age_str = f"{token_age_minutes // 60} hours, {token_age_minutes % 60} minutes"
        else:
            days = token_age_minutes // 1440
            hours = (token_age_minutes % 1440) // 60
            age_str = f"{days} days, {hours} hours"
    else:
        age_str = "Unknown"
    
    # Pre-compute warning strings (Python 3.10 doesn't allow complex expressions in f-strings)
    holder_count_text = "Unknown (top-holder sample only)"
    if isinstance(total_holders, (int, float)) and total_holders >= 0:
        holder_count_text = str(int(total_holders))
    new_wallet_warning = "(NEW WALLET - HIGH RISK)" if 0 <= wallet_age < 7 else ""
    abandoned_count = len(abandoned)
    abandoned_warning = "(RUG HISTORY DETECTED)" if abandoned else ""
    clustering_warning = "(HIGH CLUSTERING - SUSPICIOUS)" if clustering_score > 0.4 else ""
    bubblemaps_unavailable_note = (
        "\nNOTE: BubbleMaps cluster analysis data is UNAVAILABLE for this token. "
        "Scoring should rely on Helius holder data, Birdeye market data, and Bags.fm metadata only. "
        "Do not penalize or reward the absence of BubbleMaps data."
    ) if not has_bubblemaps_data else ""
    pumpfun_note = ""
    if is_pumpfun:
        pumpfun_note = ("\nIMPORTANT: This is a pump.fun token. High initial holder concentration is "
                        "expected due to the bonding curve mechanism and does NOT indicate rug pull risk "
                        "by itself. Focus on other risk signals (creator history, wallet clustering, "
                        "two-sided trader activity).")
        if bonding_curve_excluded:
            pumpfun_note += ("\nThe bonding curve address has been excluded from holder concentration "
                            "analysis. The percentages shown reflect real wallet distribution only.")
    decentralization_warning = (
        "(CENTRALIZED - HIGH RISK)" if decentralization_score < 30
        else "(MODERATE RISK)" if decentralization_score < 50
        else ""
    )
    cluster_share_warning = (
        "(HIGHLY CENTRALIZED)" if largest_cluster_share > 70
        else "(MODERATE CONCERN)" if largest_cluster_share > 50
        else ""
    )

    return f"""Analyze this Solana token launched on Bags.fm for rug pull risk:

TOKEN DETAILS:
- Name: {name}
- Symbol: {symbol}
- Token Mint: {token_mint}
- Launched: {created_at}

TOKEN MATURITY:
- Age: {age_str} since launch
- Status: {token_status}
- Note: {maturity_note}

BAGS.FM DATA:
- Creator username: {creator_username}
- Creator wallet: {creator_wallet}
- Creator royalty: {royalty_pct}%
- Top holder concentration: {top1_pct}% (top 1), {top5_pct}% (top 5), {top_10_concentration}% (top 10)
- Concentration risk level: {concentration_score}
- Total holders: {holder_count_text}
- Lifetime trading fees collected: {lifetime_fees} SOL
- Top holders: {json.dumps(top_holders[:5])}{pumpfun_note}

ON-CHAIN CREATOR HISTORY (Helius):
- Creator wallet age: {wallet_age} days {new_wallet_warning}
- Prior token launches from this wallet: {prior_launches}
- Previously abandoned tokens: {abandoned_count} {abandoned_warning}

HOLDER CLUSTERING ANALYSIS:
- Clustering score (0.0-1.0): {clustering_score} {clustering_warning}

BUBBLEMAPS ANALYSIS (Token Holder Clustering):{bubblemaps_unavailable_note}
- Decentralization Score (0-100, higher = better): {decentralization_score} {decentralization_warning}
- Number of clusters detected: {cluster_count}
- Largest cluster share: {largest_cluster_share}% {cluster_share_warning}
- Note: Decentralization score uses BubbleMaps' native algorithm (based on on-chain clustering analysis)
- BubbleMaps risk signal: {bubblemaps_risk_signal}

MARKET DATA (Birdeye):
- Liquidity (USD): ${liquidity_usd:,.2f}
- Liquidity to market cap ratio: {liquidity_ratio}
- Unique traders (24h): {unique_traders}
- Two-sided trader activity ratio (0.0-1.0, heuristic): {two_sided_ratio}
- Large sell pressure detected: {large_sell}
- Recent buys: {buy_count}
- Recent sells: {sell_count}
- Honeypot suspected from trade flow heuristic: {honeypot_suspected}
- Honeypot simulation status: {honeypot_simulation_status}
- Honeypot round-trip loss %: {honeypot_round_trip_loss_pct}
- Bundle launch suspected: {bundle_launch_suspected}
- Buys clustered in slot/time window: {bundle_same_slot_or_window}
- Buy amounts clustered: {bundle_amount_clustered}
- Buyer wallets funded by creator (count): {bundle_funded_by_creator_count}
- Wash-trade cycle count: {wash_trade_cycle_count}
- Wash-trade suspected: {wash_trade_suspected}

TOKEN SECURITY CONTROLS (On-chain):
- Mint authority renounced: {mint_authority_renounced}
- Freeze authority renounced: {freeze_authority_renounced}
- Token-2022 mint: {is_token_2022}
- Token program owner: {token_program_owner}
- Token-2022 extensions: {token_2022_extensions}
- Metadata update authority: {update_authority}
- Transfer fee bps/max: {transfer_fee_bps} / {transfer_fee_max}
- Permanent delegate: {permanent_delegate}
- Freeze authority whitelisted mint: {freeze_authority_whitelisted}
- Freeze authority high risk flag: {freeze_authority_high_risk}
- LP locked (if detected): {lp_locked}
- LP burned (if detected): {lp_burned}
- LP burned share %: {lp_burned_share_pct}
- LP locked share %: {lp_locked_share_pct}
- LP creator-held share %: {lp_creator_share_pct}
- LP unlocked and creator-controlled: {lp_unlocked_creator_controlled}

Respond with JSON only."""


def calculate_risk_score(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate risk score using the configured LLM provider.
    
    Returns a complete score dict with all fields needed for database.
    """
    provider = get_config("LLM_PROVIDER", "anthropic").lower()
    logger.info(f"[SCORER] Using LLM provider: {provider}")

    # Verify the selected provider has credentials
    if provider == "deepseek":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            logger.error("[SCAMHOUND] DEEPSEEK_API_KEY not configured")
            return _fallback_score(token_data, "API key not configured")
    else:
        client = _get_anthropic_client()
        if not client:
            logger.error("[SCAMHOUND] Anthropic API key not configured")
            return _fallback_score(token_data, "API key not configured")
    
    user_prompt = build_user_prompt(token_data)
    max_retries, base_backoff = _get_retry_settings()
    attempts = max_retries + 1
    last_error = "Unknown error"

    for attempt in range(attempts):
        try:
            response_text = _call_llm(SYSTEM_PROMPT, user_prompt)
            result = _parse_llm_json_response(response_text)
        
            normalized_score = _coerce_risk_score(result.get("risk_score", 50))
            normalized_level = _normalize_risk_level(
                result.get("risk_level"), normalized_score
            )
            verdict = _sanitize_verdict(
                str(result.get("verdict", "Analysis complete.")).strip(),
                token_data,
            )
            risk_factors = _normalize_string_list(
                result.get("top_risk_factors", []), max_items=5, max_item_length=200
            )
            risk_factors = _sanitize_risk_factors(risk_factors, token_data)
            safe_signals = _normalize_string_list(
                result.get("top_safe_signals", []), max_items=5, max_item_length=200
            )
            safe_signals = _sanitize_safe_signals(safe_signals, token_data)

            score_data = {
                "token_mint": token_data.get("token_mint"),
                "name": token_data.get("name"),
                "symbol": token_data.get("symbol"),
                "risk_score": normalized_score,
                "risk_level": normalized_level,
                "verdict": verdict,
                "top_risk_factors": risk_factors,
                "top_safe_signals": safe_signals,
                "creator_wallet": token_data.get("creator", {}).get("wallet"),
                "creator_username": token_data.get("creator", {}).get("username"),
                "top_10_concentration": token_data.get("holders", {}).get(
                    "top_10_concentration_pct", 0
                ),
                "prior_launches": token_data.get("prior_launch_count", 0),
                "wallet_age_days": token_data.get("wallet_age_days", -1),
                "clustering_score": token_data.get("clustering_score", 0),
                "liquidity_usd": token_data.get("liquidity_usd", 0),
                "lifetime_fees_sol": token_data.get("lifetime_fees_sol", 0),
                "token_age_minutes": token_data.get("token_age_minutes"),
                "token_status": token_data.get("token_status", "unknown"),
                "scored_at": datetime.now(timezone.utc).isoformat(),
                "created_at": token_data.get("created_at"),
                "score_source": f"ai_{provider}",
                "llm_attempts": attempt + 1,
                "mint_authority_renounced": token_data.get(
                    "security", {}
                ).get("mint_authority_renounced"),
                "freeze_authority_renounced": token_data.get(
                    "security", {}
                ).get("freeze_authority_renounced"),
                "is_token_2022": token_data.get("security", {}).get(
                    "is_token_2022"
                ),
                "token_2022_extensions": token_data.get(
                    "security", {}
                ).get("token_2022_extensions", []),
                "lp_locked": token_data.get("lp_controls", {}).get("lp_locked"),
                "lp_burned": token_data.get("lp_controls", {}).get("lp_burned"),
                "honeypot_suspected": token_data.get("honeypot_suspected"),
                "buy_count": token_data.get("buy_count"),
                "sell_count": token_data.get("sell_count"),
                "honeypot_simulation_status": token_data.get(
                    "honeypot_simulation_status"
                ),
                "honeypot_round_trip_loss_pct": token_data.get(
                    "honeypot_round_trip_loss_pct"
                ),
                "bundle_launch_suspected": token_data.get(
                    "bundle_launch_suspected"
                ),
                "bundle_same_slot_or_window": token_data.get(
                    "bundle_same_slot_or_window"
                ),
                "bundle_amount_clustered": token_data.get(
                    "bundle_amount_clustered"
                ),
                "bundle_funded_by_creator_count": token_data.get(
                    "bundle_funded_by_creator_count"
                ),
                "wash_trade_cycle_count": token_data.get(
                    "wash_trade_cycle_count"
                ),
                "wash_trade_suspected": token_data.get(
                    "wash_trade_suspected"
                ),
                "bundle_launch_suspected": token_data.get(
                    "bundle_launch_suspected"
                ),
                "bundle_same_slot_or_window": token_data.get(
                    "bundle_same_slot_or_window"
                ),
                "bundle_amount_clustered": token_data.get(
                    "bundle_amount_clustered"
                ),
                "update_authority": token_data.get("security", {}).get(
                    "update_authority"
                ),
                "transfer_fee_bps": token_data.get("security", {}).get(
                    "transfer_fee_bps"
                ),
                "transfer_fee_max": token_data.get("security", {}).get(
                    "transfer_fee_max"
                ),
                "permanent_delegate": token_data.get("security", {}).get(
                    "permanent_delegate"
                ),
                "freeze_authority_whitelisted": token_data.get(
                    "security", {}
                ).get("freeze_authority_whitelisted"),
            }
            score_data = _apply_security_control_weights(score_data, token_data)
            score_data = _apply_due_diligence_guard(score_data, token_data)

            logger.info(
                f"[SCAMHOUND] {score_data['symbol']} | Score: "
                f"{score_data['risk_score']} | {score_data['risk_level']} | "
                f"Provider: {provider} | Attempts: {attempt + 1}"
            )
            return score_data

        except (anthropic.APIError, ValueError) as e:
            last_error = "API error"
            logger.error(
                f"[SCAMHOUND] LLM API error ({provider}) "
                f"attempt {attempt + 1}/{attempts}: {e}"
            )
        except json.JSONDecodeError as e:
            last_error = "Parse error"
            logger.error(
                f"[SCAMHOUND] JSON parse error "
                f"attempt {attempt + 1}/{attempts}: {e}"
            )
        except Exception as e:
            last_error = "Unknown error"
            logger.error(
                f"[SCAMHOUND] Unexpected error ({provider}) "
                f"attempt {attempt + 1}/{attempts}: {e}"
            )

        if attempt < attempts - 1 and base_backoff > 0:
            time.sleep(base_backoff * (2 ** attempt))

    return _fallback_score(token_data, last_error, attempts=attempts)


def _fallback_score(
    token_data: Dict[str, Any], reason: str, attempts: int = 1
) -> Dict[str, Any]:
    """Generate a fallback score when Claude API fails."""
    return {
        "token_mint": token_data.get("token_mint"),
        "name": token_data.get("name"),
        "symbol": token_data.get("symbol"),
        "risk_score": 0,
        "risk_level": "UNSCORED",
        "verdict": (
            "AI analysis temporarily unavailable. "
            "Token marked as unscored and will be re-scored automatically."
        ),
        "top_risk_factors": [f"AI scoring pending ({reason})"],
        "top_safe_signals": [],
        "creator_wallet": token_data.get("creator", {}).get("wallet"),
        "creator_username": token_data.get("creator", {}).get("username"),
        "top_10_concentration": token_data.get("holders", {}).get("top_10_concentration_pct", 0),
        "prior_launches": token_data.get("prior_launch_count", 0),
        "wallet_age_days": token_data.get("wallet_age_days", -1),
        "clustering_score": token_data.get("clustering_score", 0),
        "liquidity_usd": token_data.get("liquidity_usd", 0),
        "lifetime_fees_sol": token_data.get("lifetime_fees_sol", 0),
        "token_age_minutes": token_data.get("token_age_minutes"),
        "token_status": token_data.get("token_status", "unknown"),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "created_at": token_data.get("created_at"),
        "score_source": "fallback",
        "llm_attempts": attempts,
        "mint_authority_renounced": token_data.get("security", {}).get(
            "mint_authority_renounced"
        ),
        "freeze_authority_renounced": token_data.get("security", {}).get(
            "freeze_authority_renounced"
        ),
        "is_token_2022": token_data.get("security", {}).get("is_token_2022"),
        "token_2022_extensions": token_data.get(
            "security", {}
        ).get("token_2022_extensions", []),
        "lp_locked": token_data.get("lp_controls", {}).get("lp_locked"),
        "lp_burned": token_data.get("lp_controls", {}).get("lp_burned"),
        "honeypot_suspected": token_data.get("honeypot_suspected"),
        "buy_count": token_data.get("buy_count"),
        "sell_count": token_data.get("sell_count"),
        "honeypot_simulation_status": token_data.get(
            "honeypot_simulation_status"
        ),
        "honeypot_round_trip_loss_pct": token_data.get(
            "honeypot_round_trip_loss_pct"
        ),
        "bundle_launch_suspected": token_data.get(
            "bundle_launch_suspected"
        ),
        "bundle_same_slot_or_window": token_data.get(
            "bundle_same_slot_or_window"
        ),
        "bundle_amount_clustered": token_data.get(
            "bundle_amount_clustered"
        ),
        "bundle_funded_by_creator_count": token_data.get(
            "bundle_funded_by_creator_count"
        ),
        "wash_trade_cycle_count": token_data.get(
            "wash_trade_cycle_count"
        ),
        "wash_trade_suspected": token_data.get(
            "wash_trade_suspected"
        ),
        "bundle_launch_suspected": token_data.get(
            "bundle_launch_suspected"
        ),
        "bundle_same_slot_or_window": token_data.get(
            "bundle_same_slot_or_window"
        ),
        "bundle_amount_clustered": token_data.get(
            "bundle_amount_clustered"
        ),
        "update_authority": token_data.get("security", {}).get(
            "update_authority"
        ),
        "transfer_fee_bps": token_data.get("security", {}).get(
            "transfer_fee_bps"
        ),
        "transfer_fee_max": token_data.get("security", {}).get(
            "transfer_fee_max"
        ),
        "permanent_delegate": token_data.get("security", {}).get(
            "permanent_delegate"
        ),
        "freeze_authority_whitelisted": token_data.get("security", {}).get(
            "freeze_authority_whitelisted"
        ),
    }
