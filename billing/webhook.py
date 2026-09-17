from __future__ import annotations

from aiohttp import web

import config
from billing.service import get_order, grant_order
from billing.unitpay import (
    UNITPAY_IPS,
    error_response,
    success_response,
    verify_handler_signature,
)


def _client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.remote:
        return request.remote
    return ""


async def unitpay_handler(request: web.Request) -> web.Response:
    method = request.query.get("method") or request.rel_url.query.get("method")
    params: dict[str, str] = {}
    for key, value in request.query.items():
        if key == "method":
            continue
        if key.startswith("params[") and key.endswith("]"):
            params[key[7:-1]] = value
        else:
            params[key] = value

    if not method:
        return web.Response(
            text=error_response("method missing"),
            content_type="application/json",
        )

    ip = _client_ip(request)
    if not config.UNITPAY_SKIP_IP_CHECK and ip not in UNITPAY_IPS:
        return web.Response(
            text=error_response(f"IP not allowed: {ip}"),
            content_type="application/json",
        )

    if not verify_handler_signature(method, params):
        return web.Response(
            text=error_response("Invalid signature"),
            content_type="application/json",
        )

    account = str(params.get("account") or "")
    order = get_order(account) if account else None

    if method == "check":
        if order is None:
            return web.Response(
                text=error_response("Order not found"),
                content_type="application/json",
            )
        if order["status"] not in {"created", "pending", "paid"}:
            return web.Response(
                text=error_response("Bad order status"),
                content_type="application/json",
            )
        if order["provider"] != "unitpay":
            return web.Response(
                text=error_response("Provider mismatch"),
                content_type="application/json",
            )
        try:
            order_sum = float(params.get("orderSum") or params.get("sum") or 0)
        except ValueError:
            order_sum = -1
        currency = str(params.get("orderCurrency") or params.get("currency") or "")
        if abs(order_sum - float(order["amount"])) > 0.01:
            return web.Response(
                text=error_response("Sum mismatch"),
                content_type="application/json",
            )
        if currency and currency.upper() != str(order["currency"]).upper():
            return web.Response(
                text=error_response("Currency mismatch"),
                content_type="application/json",
            )
        if config.UNITPAY_PROJECT_ID:
            project_id = str(params.get("projectId") or "")
            if project_id and project_id != config.UNITPAY_PROJECT_ID:
                return web.Response(
                    text=error_response("Project mismatch"),
                    content_type="application/json",
                )
        return web.Response(
            text=success_response("Check OK"),
            content_type="application/json",
        )

    if method == "pay":
        if order is None:
            return web.Response(
                text=error_response("Order not found"),
                content_type="application/json",
            )
        if order["provider"] != "unitpay":
            return web.Response(
                text=error_response("Provider mismatch"),
                content_type="application/json",
            )
        try:
            order_sum = float(params.get("orderSum") or params.get("sum") or 0)
        except ValueError:
            order_sum = -1
        if abs(order_sum - float(order["amount"])) > 0.01:
            return web.Response(
                text=error_response("Sum mismatch"),
                content_type="application/json",
            )
        unitpay_id = str(params.get("unitpayId") or params.get("paymentId") or "")
        if not unitpay_id:
            return web.Response(
                text=error_response("unitpayId missing"),
                content_type="application/json",
            )
        result = grant_order(
            account,
            provider="unitpay",
            provider_payment_id=unitpay_id,
            raw=dict(params),
        )
        if not result.get("ok"):
            return web.Response(
                text=error_response(str(result.get("error") or "grant failed")),
                content_type="application/json",
            )
        return web.Response(
            text=success_response("Pay OK"),
            content_type="application/json",
        )

    if method in {"error", "preauth"}:
        return web.Response(
            text=success_response("OK"),
            content_type="application/json",
        )

    return web.Response(
        text=error_response(f"Unknown method: {method}"),
        content_type="application/json",
    )


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/billing/unitpay", unitpay_handler)
    app.router.add_post("/billing/unitpay", unitpay_handler)
    app.router.add_get("/health", health)
    return app


async def start_billing_server(host: str, port: int) -> web.AppRunner:
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
