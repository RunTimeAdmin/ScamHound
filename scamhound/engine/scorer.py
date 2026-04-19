"""
ScamHound Scoring Engine
Uses Claude AI to analyze token data and generate risk scores
"""

import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Anthropic client singleton
_anthropic_client = None
_anthropic_client_key = None


def _get_anthropic_client():
    """Get or create Anthropic client singleton."""
    global _anthropic_client, _anthropic_client_key
    from config import get_config
    key = get_config("ANTHROPIC_API_KEY")
    if key and (key != _anthropic_client_key or _anthropic_client is None):
        _anthropic_client = anthropic.Anthropic(api_key=key)
        _anthropic_client_key = key
    return _anthropic_client



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
- Wash trading score >0.7 = critical, >0.5 = high
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
    
    # On-chain data
    wallet_age = token_data.get("wallet_age_days", -1)
    prior_launches = token_data.get("prior_launch_count", 0)
    abandoned = token_data.get("abandoned_tokens", [])
    clustering_score = token_data.get("clustering_score", 0)
    
    # Market data
    liquidity_usd = token_data.get("liquidity_usd", 0)
    liquidity_ratio = token_data.get("liquidity_to_mcap_ratio", 0)
    unique_traders = token_data.get("unique_trader_count", 0)
    wash_score = token_data.get("wash_trading_score", 0)
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
- Total holders: {total_holders}
- Lifetime trading fees collected: {lifetime_fees} SOL
- Top holders: {json.dumps(top_holders[:5])}

ON-CHAIN CREATOR HISTORY (Helius):
- Creator wallet age: {wallet_age} days {f"(NEW WALLET - HIGH RISK)" if 0 <= wallet_age < 7 else ""}
- Prior token launches from this wallet: {prior_launches}
- Previously abandoned tokens: {len(abandoned)} {("(RUG HISTORY DETECTED)" if abandoned else "")}

HOLDER CLUSTERING ANALYSIS:
- Clustering score (0.0-1.0): {clustering_score} {("(HIGH CLUSTERING - SUSPICIOUS)" if clustering_score > 0.4 else "")}

BUBBLEMAPS ANALYSIS (Token Holder Clustering):{f"\nNOTE: BubbleMaps cluster analysis data is UNAVAILABLE for this token. Scoring should rely on Helius holder data, Birdeye market data, and Bags.fm metadata only. Do not penalize or reward the absence of BubbleMaps data." if not has_bubblemaps_data else ""}
- Decentralization Score (0-100, higher = better): {decentralization_score} {("(CENTRALIZED - HIGH RISK)" if decentralization_score < 30 else "(MODERATE RISK)" if decentralization_score < 50 else "")}
- Number of clusters detected: {cluster_count}
- Largest cluster share: {largest_cluster_share}% {("(HIGHLY CENTRALIZED)" if largest_cluster_share > 70 else "(MODERATE CONCERN)" if largest_cluster_share > 50 else "")}
- BubbleMaps risk signal: {bubblemaps_risk_signal}

MARKET DATA (Birdeye):
- Liquidity (USD): ${liquidity_usd:,.2f}
- Liquidity to market cap ratio: {liquidity_ratio}
- Unique traders (24h): {unique_traders}
- Wash trading score (0.0-1.0): {wash_score}
- Large sell pressure detected: {large_sell}

Respond with JSON only."""


def calculate_risk_score(token_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate risk score using Claude AI.
    
    Returns a complete score dict with all fields needed for database.
    """
    client = _get_anthropic_client()
    if not client:
        logger.error("[SCAMHOUND] Anthropic API key not configured")
        return _fallback_score(token_data, "API key not configured")
    
    user_prompt = build_user_prompt(token_data)
    
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        
        # Parse the response
        response_text = message.content[0].text.strip()
        
        # Try to parse JSON from the response
        # Sometimes Claude might add markdown code blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        result = json.loads(response_text)
        
        # Build the complete score dict
        score_data = {
            "token_mint": token_data.get("token_mint"),
            "name": token_data.get("name"),
            "symbol": token_data.get("symbol"),
            "risk_score": result.get("risk_score", 50),
            "risk_level": result.get("risk_level", "MEDIUM"),
            "verdict": result.get("verdict", "Analysis complete."),
            "top_risk_factors": result.get("top_risk_factors", []),
            "top_safe_signals": result.get("top_safe_signals", []),
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
            "scored_at": datetime.utcnow().isoformat(),
            "created_at": token_data.get("created_at"),
            "score_source": "ai"
        }
        
        logger.info(f"[SCAMHOUND] {score_data['symbol']} | Score: {score_data['risk_score']} | {score_data['risk_level']}")
        
        return score_data
        
    except anthropic.APIError as e:
        logger.error(f"[SCAMHOUND] Claude API error: {e}")
        return _fallback_score(token_data, "API error")
    except json.JSONDecodeError as e:
        logger.error(f"[SCAMHOUND] JSON parse error: {e}")
        return _fallback_score(token_data, "Parse error")
    except Exception as e:
        logger.error(f"[SCAMHOUND] Unexpected error: {e}")
        return _fallback_score(token_data, "Unknown error")


def _fallback_score(token_data: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Generate a fallback score when Claude API fails."""
    return {
        "token_mint": token_data.get("token_mint"),
        "name": token_data.get("name"),
        "symbol": token_data.get("symbol"),
        "risk_score": 50,
        "risk_level": "MEDIUM",
        "verdict": f"Analysis unavailable ({reason}) - treat with caution",
        "top_risk_factors": ["Unable to complete AI analysis"],
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
        "scored_at": datetime.utcnow().isoformat(),
        "created_at": token_data.get("created_at"),
        "score_source": "fallback"
    }