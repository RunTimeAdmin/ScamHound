"""
ScamHound GMGN CLI client.
Optional fallback signals via gmgn-cli token info/security commands.
"""

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Extract first JSON object from CLI output text."""
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _unwrap_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize common API wrapper shapes into a payload dict."""
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    return payload


def _run_gmgn_command(args: list[str], timeout: int) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        logger.warning("[GMGN] Failed command %s: %s", " ".join(args), exc)
        return {}

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        logger.info(
            "[GMGN] Command failed (%s): %s",
            completed.returncode,
            stderr[:200],
        )
        return {}
    return _extract_json_object(completed.stdout)


def get_token_information_fallback(token_mint: str) -> Dict[str, Any]:
    """
    Fetch optional GMGN token info + security via gmgn-cli.

    Returns a stable shape and never raises.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "cli_available": False,
        "info_available": False,
        "security_available": False,
        "name": None,
        "symbol": None,
        "creator_wallet": None,
        "created_at": None,
        "liquidity_usd": 0.0,
        "market_cap_usd": 0.0,
        "liquidity_to_mcap_ratio": 0.0,
        "holder_count": 0,
        "top_10_holder_pct": 0.0,
        "buy_count_24h": 0,
        "sell_count_24h": 0,
        "volume_24h_usd": 0.0,
        "rug_ratio": 0.0,
        "is_wash_trading": False,
        "bundler_ratio": 0.0,
        "phishing_ratio": 0.0,
        "mint_authority_renounced": None,
        "freeze_authority_renounced": None,
    }

    if not token_mint:
        return result

    gmgn_cli = shutil.which("gmgn-cli")
    if not gmgn_cli:
        return result

    result["cli_available"] = True
    timeout_seconds = _safe_int(
        os.environ.get("GMGN_CLI_TIMEOUT_SECONDS", 20),
        default=20,
    )
    timeout_seconds = max(5, timeout_seconds)

    info_payload = _run_gmgn_command(
        [
            gmgn_cli,
            "token",
            "info",
            "--chain",
            "sol",
            "--address",
            token_mint,
            "--raw",
        ],
        timeout_seconds,
    )
    info = _unwrap_payload(info_payload)
    if info:
        result["info_available"] = True
        result["name"] = info.get("name")
        result["symbol"] = info.get("symbol")
        result["creator_wallet"] = (
            (info.get("dev") or {}).get("creator_address")
            if isinstance(info.get("dev"), dict)
            else None
        )
        result["created_at"] = info.get("creation_timestamp") or info.get(
            "open_timestamp"
        )
        result["liquidity_usd"] = _safe_float(info.get("liquidity"))
        result["holder_count"] = _safe_int(info.get("holder_count"))
        top_10_rate = 0.0
        stat = info.get("stat")
        if isinstance(stat, dict):
            top_10_rate = _safe_float(stat.get("top_10_holder_rate"))
            result["bundler_ratio"] = max(
                result["bundler_ratio"],
                _safe_float(stat.get("top_bundler_trader_percentage")),
            )
            result["phishing_ratio"] = max(
                result["phishing_ratio"],
                _safe_float(stat.get("top_entrapment_trader_percentage")),
            )
        if top_10_rate <= 0:
            dev = info.get("dev")
            if isinstance(dev, dict):
                top_10_rate = _safe_float(
                    dev.get("top_10_holder_rate")
                )
        result["top_10_holder_pct"] = round(top_10_rate * 100, 2)

        price_data = info.get("price")
        if isinstance(price_data, dict):
            buy_h24 = _safe_int(price_data.get("buys_24h"))
            sell_h24 = _safe_int(price_data.get("sells_24h"))
            result["buy_count_24h"] = buy_h24
            result["sell_count_24h"] = sell_h24
            result["volume_24h_usd"] = _safe_float(price_data.get("volume_24h"))

            unit_price = _safe_float(price_data.get("price"))
            circulating = _safe_float(info.get("circulating_supply"))
            if unit_price > 0 and circulating > 0:
                result["market_cap_usd"] = unit_price * circulating
                if result["liquidity_usd"] > 0:
                    result["liquidity_to_mcap_ratio"] = round(
                        result["liquidity_usd"] / result["market_cap_usd"],
                        4,
                    )

    security_payload = _run_gmgn_command(
        [
            gmgn_cli,
            "token",
            "security",
            "--chain",
            "sol",
            "--address",
            token_mint,
            "--raw",
        ],
        timeout_seconds,
    )
    security = _unwrap_payload(security_payload)
    if security:
        result["security_available"] = True
        result["rug_ratio"] = _safe_float(
            security.get("rug_ratio")
        )
        result["is_wash_trading"] = bool(security.get("is_wash_trading"))
        result["bundler_ratio"] = max(
            result["bundler_ratio"],
            _safe_float(security.get("bundler_trader_amount_rate")),
        )
        result["phishing_ratio"] = max(
            result["phishing_ratio"],
            _safe_float(
                security.get("entrapment_ratio")
                or security.get("rat_trader_amount_rate")
            ),
        )
        result["mint_authority_renounced"] = security.get("renounced_mint")
        result["freeze_authority_renounced"] = security.get(
            "renounced_freeze_account"
        )
        if result["top_10_holder_pct"] <= 0:
            rate = _safe_float(security.get("top_10_holder_rate"))
            result["top_10_holder_pct"] = round(rate * 100, 2)

    result["checked"] = result["info_available"] or result["security_available"]
    return result
