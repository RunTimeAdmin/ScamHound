"""Scan API routes."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from engine import database, monitor


def create_scan_router(
    check_api_key_fn,
    get_current_user_fn,
    get_client_ip_fn,
    check_rate_limit_fn,
    is_valid_solana_address_fn,
    add_rate_limit_headers_fn,
    max_scans_per_minute: int,
    logger,
    background_scan_tasks: set,
) -> APIRouter:
    """Create scan router with app-level dependency injection."""
    router = APIRouter()

    @router.post("/api/scan")
    @router.post("/api/_legacy/scan")
    async def scan_token(request: Request):
        """Manually trigger token scan by mint."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error

        user = None
        if not key_row:
            user = get_current_user_fn(request)
            if not user:
                return JSONResponse(
                    {"error": "Authentication required"},
                    status_code=401,
                )

        if not key_row:
            client_ip = get_client_ip_fn(request)
            allowed, _remaining, retry_after = check_rate_limit_fn(client_ip)

            if not allowed:
                return JSONResponse(
                    content={
                        "success": False,
                        "error": (
                            f"Rate limit exceeded. Max {max_scans_per_minute} "
                            f"scans/min. Retry in {retry_after}s."
                        ),
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )

        scan_check = None
        if user:
            scan_check = database.check_and_increment_scan(
                user["id"], user.get("is_admin", False)
            )
            if not scan_check["allowed"]:
                return JSONResponse(
                    content={
                        "success": False,
                        "error": (
                            "Daily scan limit reached "
                            f"({scan_check['scans_today']}/{scan_check['limit']}). "
                            "Resets at midnight UTC."
                        ),
                    },
                    status_code=429,
                )

        try:
            data = await request.json()

            if not isinstance(data, dict):
                return JSONResponse(
                    content={"success": False, "error": "Invalid request body"},
                    status_code=400,
                )

            token_mint = data.get("mint")

            if not token_mint:
                return JSONResponse(
                    content={"success": False, "error": "Missing 'mint' field"},
                    status_code=400,
                )

            if not is_valid_solana_address_fn(token_mint):
                return JSONResponse(
                    content={"success": False, "error": "Invalid mint address format"},
                    status_code=400,
                )

            request_id = getattr(request.state, "request_id", "unknown")
            logger.info(
                f"[DASHBOARD] req={request_id} Manual scan requested for: "
                f"{token_mint[:8]}..."
            )

            result = await monitor.scan_single_token_async(
                token_mint, skip_if_scored=False
            )

            if result is None:
                return JSONResponse(
                    content={
                        "success": False,
                        "error": "Scan failed or token not found",
                    },
                    status_code=500,
                )

            if user and result and result.get("token_mint"):
                try:
                    conn = database.get_connection()
                    conn.execute(
                        "UPDATE scored_tokens SET user_id = ? WHERE token_mint = ?",
                        (user["id"], result["token_mint"]),
                    )
                    conn.commit()
                    conn.close()
                except Exception:
                    pass

            response_data = {"success": True, "result": result}

            if scan_check and scan_check["limit"] > 0:
                response_data["scans_remaining"] = (
                    scan_check["limit"] - scan_check["scans_today"]
                )
            elif scan_check and scan_check["limit"] == -1:
                response_data["scans_remaining"] = -1

            response = JSONResponse(content=response_data)

            if key_row:
                database.increment_api_key_usage(key_row["id"], "/api/scan")
                response = add_rate_limit_headers_fn(response, key_row)

            return response

        except Exception as e:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.error(f"[DASHBOARD] req={request_id} Error in scan_token: {e}")
            return JSONResponse(
                content={"success": False, "error": str(e)},
                status_code=500,
            )

    @router.post("/api/scan/batch")
    @router.post("/api/_legacy/scan/batch")
    async def api_scan_batch(request: Request):
        """Batch scan up to 50 mints. Requires Builder+ API key."""
        key_row, key_error = check_api_key_fn(request)
        if key_error:
            return key_error
        if not key_row:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "API key required. Batch scanning requires Builder tier."
                },
            )
        if key_row["tier"] not in ("builder", "enterprise"):
            return JSONResponse(
                status_code=403,
                content={"error": "Batch scan requires Builder tier or higher"},
            )

        request_id = getattr(request.state, "request_id", "unknown")

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

        mints = body.get("mints", [])

        if not isinstance(mints, list):
            return JSONResponse(status_code=400, content={"error": "mints must be an array"})
        if len(mints) == 0:
            return JSONResponse(
                status_code=400,
                content={"error": "mints array cannot be empty"},
            )
        if len(mints) > 50:
            return JSONResponse(
                status_code=400,
                content={"error": "Maximum 50 mints per batch"},
            )

        mints = [
            m.strip()
            for m in mints
            if isinstance(m, str) and is_valid_solana_address_fn(m.strip())
        ]
        if not mints:
            return JSONResponse(
                status_code=400,
                content={"error": "No valid mint addresses provided"},
            )

        results = []
        to_scan = []

        for mint in mints:
            existing = database.get_score_by_mint(mint)
            if existing:
                results.append(
                    {"token_mint": mint, "status": "cached", "data": existing}
                )
            else:
                to_scan.append(mint)
                results.append(
                    {"token_mint": mint, "status": "queued", "data": None}
                )

        usage_units = max(1, len(to_scan))
        database.increment_api_key_usage(
            key_row["id"], "/api/scan/batch", count=usage_units
        )

        if to_scan:

            async def _background_scan(mint_list):
                for mint in mint_list:
                    try:
                        await monitor.scan_single_token_async(
                            mint, skip_if_scored=False
                        )
                    except Exception:
                        pass

            task = asyncio.create_task(_background_scan(to_scan))
            background_scan_tasks.add(task)
            task.add_done_callback(background_scan_tasks.discard)
            logger.info(
                f"[DASHBOARD] req={request_id} queued {len(to_scan)} "
                f"batch scans in background (charged_units={usage_units})"
            )

        response = JSONResponse(
            content={
                "results": results,
                "total": len(mints),
                "cached": len(mints) - len(to_scan),
                "queued": len(to_scan),
                "charged_units": usage_units,
                "message": (
                    f"{len(to_scan)} tokens queued for scanning. "
                    "Check back in 30-60 seconds for results."
                    if to_scan
                    else "All tokens found in cache."
                ),
            }
        )

        return add_rate_limit_headers_fn(response, key_row)

    return router
