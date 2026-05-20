"""
ScamHound Scoring Engine
Uses LLM AI to analyze token data and generate risk scores.
Supports multiple providers: Anthropic Claude (default) and DeepSeek.
"""

import json
import logging
import os
from typing import Dict, Any
from datetime import datetime, timezone

import anthropic

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
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

    if provider == "deepseek":
        return _call_deepseek(system_prompt, user_prompt)
    else:
        return _call_anthropic(system_prompt, user_prompt)


def _call_deepseek(system_prompt: str, user_prompt: str) -> str:
    """Call DeepSeek API (OpenAI-compatible)."""
    client = _get_deepseek_client()
    if not client:
        raise ValueError("DEEPSEEK_API_KEY not configured")

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

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

    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    response = client.messages.create(
        model=model,
        max_tokens=1000,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.content[0].text


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

    # Fallback: extract first JSON object-like span
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    # Preserve original parse exception semantics for callers
    return json.loads(text)


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
    has_bubblemaps_data = (
        token_data.get("bubblemaps", {}).get("decentralization_score") is not None
    )

    sanitized = []
    for factor in factors:
        text = factor.strip()
        lower = text.lower()

        if isinstance(wallet_age_days, (int, float)) and wallet_age_days >= 0:
            if "wallet age unknown" in lower:
                continue
        if has_bubblemaps_data and "bubblemaps" in lower and "no" in lower:
            continue
        if isinstance(token_age_minutes, (int, float)) and token_age_minutes >= 60:
            if "very early stage" in lower or "brand new" in lower:
                continue

        sanitized.append(text)
    return sanitized[:5]



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
- Liquidity/MCap ratio <0.05 = critical, <0.10 = high (ONLY if token > 1 hour old)
- Two-sided trader ratio >0.7 = critical, >0.5 = high (heuristic only; not definitive wash-trading proof)
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
    total_holders = holders.get("total_holder_count", 0)
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
        "two_sided_trader_ratio",
        token_data.get("wash_trading_score", 0),
    )
    large_sell = token_data.get("large_sell_pressure", False)
    lifetime_fees = token_data.get("lifetime_fees_sol", 0)
    
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
    holder_count_text = (
        str(total_holders)
        if total_holders is not None
        else "Unknown (top-holder sample only)"
    )
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
                        "two-sided trader behavior).")
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
- Two-sided trader ratio (0.0-1.0, heuristic): {two_sided_ratio}
- Large sell pressure detected: {large_sell}

Respond with JSON only."""


def calculate_risk_score(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate risk score using the configured LLM provider.
    
    Returns a complete score dict with all fields needed for database.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
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

        # Build the complete score dict
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
            "score_source": f"ai_{provider}"
        }
        
        logger.info(f"[SCAMHOUND] {score_data['symbol']} | Score: {score_data['risk_score']} | {score_data['risk_level']} | Provider: {provider}")
        
        return score_data
        
    except (anthropic.APIError, ValueError) as e:
        logger.error(f"[SCAMHOUND] LLM API error ({provider}): {e}")
        return _fallback_score(token_data, "API error")
    except json.JSONDecodeError as e:
        logger.error(f"[SCAMHOUND] JSON parse error: {e}")
        return _fallback_score(token_data, "Parse error")
    except Exception as e:
        logger.error(f"[SCAMHOUND] Unexpected error ({provider}): {e}")
        return _fallback_score(token_data, "Unknown error")


def _fallback_score(token_data: Dict[str, Any], reason: str) -> Dict[str, Any]:
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
        "score_source": "fallback"
    }
