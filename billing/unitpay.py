from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlencode

import config

# Official UnitPay callback source IPs (from UnitPay PHP SDK / docs)
UNITPAY_IPS = frozenset(
    {
        "31.186.100.49",
        "51.250.20.9",
        "52.29.152.23",
        "52.19.56.234",
        "127.0.0.1",
        "::1",
    }
)


def form_signature(account: str, currency: str, desc: str, sum_value: str | float) -> str:
    """Signature for payment form / redirect URL."""
    sum_str = _format_sum(sum_value)
    raw = (
        f"{account}{{up}}{currency}{{up}}{desc}{{up}}{sum_str}"
        f"{{up}}{config.UNITPAY_SECRET_KEY}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def handler_signature(method: str, params: dict[str, Any]) -> str:
    """Signature for UnitPay payment handler callbacks."""
    cleaned = {k: v for k, v in params.items() if k not in {"signature", "sign"}}
    parts = [str(cleaned[k]) for k in sorted(cleaned.keys())]
    raw = (
        method
        + "{up}"
        + "{up}".join(parts)
        + "{up}"
        + config.UNITPAY_SECRET_KEY
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_handler_signature(method: str, params: dict[str, Any]) -> bool:
    expected = handler_signature(method, params)
    got = str(params.get("signature") or params.get("sign") or "")
    return bool(got) and got.lower() == expected.lower()


def _format_sum(sum_value: str | float) -> str:
    if isinstance(sum_value, float):
        if sum_value == int(sum_value):
            return str(int(sum_value))
        return f"{sum_value:.2f}".rstrip("0").rstrip(".")
    return str(sum_value)


def build_payment_url(
    *,
    account: str,
    sum_value: float,
    desc: str,
    currency: str = "RUB",
) -> str:
    if not config.UNITPAY_PUBLIC_KEY:
        raise RuntimeError("UNITPAY_PUBLIC_KEY is not set")
    if not config.UNITPAY_SECRET_KEY:
        raise RuntimeError("UNITPAY_SECRET_KEY is not set")
    sum_str = _format_sum(sum_value)
    signature = form_signature(account, currency, desc, sum_str)
    query = urlencode(
        {
            "sum": sum_str,
            "account": account,
            "desc": desc,
            "currency": currency,
            "signature": signature,
        },
        encoding="utf-8",
    )
    return f"https://unitpay.ru/pay/{config.UNITPAY_PUBLIC_KEY}?{query}"


def success_response(message: str = "OK") -> str:
    return json.dumps({"result": {"message": message}}, ensure_ascii=False)


def error_response(message: str) -> str:
    return json.dumps({"error": {"message": message}}, ensure_ascii=False)
