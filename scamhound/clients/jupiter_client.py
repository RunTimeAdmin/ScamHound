"""
Jupiter quote/simulation client.

Provides a no-broadcast round-trip swap check used as a honeypot signal.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

from .retry import request_with_retry

logger = logging.getLogger(__name__)

JUPITER_API_BASE = os.environ.get(
    "JUPITER_API_BASE", "https://lite-api.jup.ag"
)
SOL_MINT = "So11111111111111111111111111111111111111112"


def _get_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 50,
) -> Optional[Dict[str, Any]]:
    """Get a Jupiter quote route for exact-in swaps."""
    url = f"{JUPITER_API_BASE}/swap/v1/quote"
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(max(1, int(amount))),
        "slippageBps": str(max(1, int(slippage_bps))),
        "swapMode": "ExactIn",
        "restrictIntermediateTokens": "true",
    }
    try:
        response = request_with_retry(
            requests.get,
            url,
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning(f"[JUPITER] Quote request failed: {exc}")
        return None

    if not isinstance(data, dict):
        return None
    if data.get("error"):
        logger.info(f"[JUPITER] Quote unavailable: {data.get('error')}")
        return None
    if not data.get("outAmount"):
        return None
    return data


def simulate_round_trip(
    token_mint: str,
    buy_sol: float = 0.01,
    loss_threshold_pct: float = 30.0,
) -> Dict[str, Any]:
    """
    Run a no-broadcast round-trip check using Jupiter routes.

    Returns status + loss percentage and suspicion flag.
    """
    if not token_mint:
        return {
            "checked": False,
            "status": "invalid_input",
            "honeypot_suspected": False,
            "round_trip_loss_pct": None,
            "reason": "missing token mint",
        }

    buy_lamports = int(max(0.0001, float(buy_sol)) * 1_000_000_000)
    loss_threshold = max(0.0, float(loss_threshold_pct))

    buy_quote = _get_quote(SOL_MINT, token_mint, buy_lamports)
    if not buy_quote:
        return {
            "checked": False,
            "status": "buy_quote_unavailable",
            "honeypot_suspected": False,
            "round_trip_loss_pct": None,
            "reason": "buy route unavailable",
        }

    try:
        tokens_out = int(buy_quote.get("outAmount", "0"))
    except (TypeError, ValueError):
        tokens_out = 0
    if tokens_out <= 0:
        return {
            "checked": False,
            "status": "buy_quote_invalid",
            "honeypot_suspected": False,
            "round_trip_loss_pct": None,
            "reason": "invalid buy route output",
        }

    sell_quote = _get_quote(token_mint, SOL_MINT, tokens_out)
    if not sell_quote:
        return {
            "checked": True,
            "status": "sell_quote_unavailable",
            "honeypot_suspected": True,
            "round_trip_loss_pct": None,
            "reason": "sell route unavailable after buy route",
        }

    try:
        sol_back = int(sell_quote.get("outAmount", "0"))
    except (TypeError, ValueError):
        sol_back = 0

    if buy_lamports <= 0:
        loss_pct = None
    else:
        loss_pct = max(0.0, (1.0 - (sol_back / buy_lamports)) * 100.0)

    suspected = loss_pct is not None and loss_pct >= loss_threshold
    status = "high_round_trip_loss" if suspected else "round_trip_ok"
    reason = (
        f"round-trip loss {loss_pct:.2f}%"
        if loss_pct is not None
        else "round-trip loss unavailable"
    )
    return {
        "checked": True,
        "status": status,
        "honeypot_suspected": bool(suspected),
        "round_trip_loss_pct": (
            round(loss_pct, 2) if loss_pct is not None else None
        ),
        "reason": reason,
    }
