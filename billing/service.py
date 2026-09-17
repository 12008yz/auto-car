from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from billing.db import connect, init_db
from billing.skus import ACTION_COST, FREE_DAILY, Action, Rail, get_sku

_lock = threading.RLock()
_initialized = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _today() -> str:
    return _now().date().isoformat()


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    with _lock:
        if not _initialized:
            init_db()
            _initialized = True


def default_rail(language_code: str | None) -> Rail:
    code = (language_code or "").lower()
    if code.startswith("ru"):
        return "unitpay"
    return "stars"


@dataclass
class ConsumeResult:
    ok: bool
    reason: str = ""
    message: str = ""
    charged: str = ""  # "credits" | "daily" | ""
    amount: int = 0


@dataclass
class BalanceInfo:
    telegram_id: int
    rail: Rail
    tier: str
    expires_at: str | None
    credits: int
    daily_ask: int
    daily_summary: int
    daily_card: int
    daily_ask_limit: int
    daily_summary_limit: int
    daily_card_limit: int
    is_pro: bool


def ensure_user(telegram_id: int, language_code: str | None = None) -> Rail:
    _ensure_init()
    rail = default_rail(language_code)
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT locale_rail FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row is None:
            now = _iso(_now())
            today = _today()
            conn.execute(
                "INSERT INTO users (telegram_id, locale_rail, created_at) VALUES (?, ?, ?)",
                (telegram_id, rail, now),
            )
            conn.execute(
                "INSERT INTO wallets (telegram_id, credits_balance, daily_ask, daily_summary, "
                "daily_card, daily_date) VALUES (?, 0, 0, 0, 0, ?)",
                (telegram_id, today),
            )
            conn.execute(
                "INSERT INTO subscriptions (telegram_id, tier, expires_at, source, external_id) "
                "VALUES (?, 'free', NULL, NULL, NULL)",
                (telegram_id,),
            )
            conn.commit()
            return rail
        return row["locale_rail"]  # type: ignore[return-value]


def set_rail(telegram_id: int, rail: Rail, language_code: str | None = None) -> None:
    ensure_user(telegram_id, language_code)
    if rail not in {"unitpay", "stars"}:
        raise ValueError("rail must be unitpay or stars")
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE users SET locale_rail = ? WHERE telegram_id = ?",
            (rail, telegram_id),
        )
        conn.commit()


def get_rail(telegram_id: int, language_code: str | None = None) -> Rail:
    rail = ensure_user(telegram_id, language_code)
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT locale_rail FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return (row["locale_rail"] if row else rail)  # type: ignore[return-value]


def _reset_daily_if_needed(conn, telegram_id: int) -> None:
    today = _today()
    row = conn.execute(
        "SELECT daily_date FROM wallets WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return
    if row["daily_date"] != today:
        conn.execute(
            "UPDATE wallets SET daily_ask = 0, daily_summary = 0, daily_card = 0, "
            "daily_date = ? WHERE telegram_id = ?",
            (today, telegram_id),
        )


def _is_pro(conn, telegram_id: int) -> bool:
    row = conn.execute(
        "SELECT tier, expires_at FROM subscriptions WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    if row is None:
        return False
    if row["tier"] != "pro":
        return False
    expires = _parse_iso(row["expires_at"])
    if expires is None:
        return False
    return expires > _now()


def get_balance(telegram_id: int, language_code: str | None = None) -> BalanceInfo:
    ensure_user(telegram_id, language_code)
    with _lock, connect() as conn:
        _reset_daily_if_needed(conn, telegram_id)
        conn.commit()
        user = conn.execute(
            "SELECT locale_rail FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        wallet = conn.execute(
            "SELECT credits_balance, daily_ask, daily_summary, daily_card "
            "FROM wallets WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        sub = conn.execute(
            "SELECT tier, expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        is_pro = _is_pro(conn, telegram_id)
        return BalanceInfo(
            telegram_id=telegram_id,
            rail=user["locale_rail"],
            tier="pro" if is_pro else "free",
            expires_at=sub["expires_at"] if sub else None,
            credits=int(wallet["credits_balance"] if wallet else 0),
            daily_ask=int(wallet["daily_ask"] if wallet else 0),
            daily_summary=int(wallet["daily_summary"] if wallet else 0),
            daily_card=int(wallet["daily_card"] if wallet else 0),
            daily_ask_limit=FREE_DAILY["ask"],
            daily_summary_limit=FREE_DAILY["summary"],
            daily_card_limit=FREE_DAILY["card"],
            is_pro=is_pro,
        )


def consume(telegram_id: int, action: Action, language_code: str | None = None) -> ConsumeResult:
    ensure_user(telegram_id, language_code)
    cost = ACTION_COST[action]
    with _lock, connect() as conn:
        _reset_daily_if_needed(conn, telegram_id)
        is_pro = _is_pro(conn, telegram_id)

        if action == "edit" and not is_pro:
            conn.commit()
            return ConsumeResult(
                ok=False,
                reason="pro_required",
                message="Правки Word доступны на тарифе Pro. Оформите подписку: /pay",
            )

        wallet = conn.execute(
            "SELECT credits_balance FROM wallets WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        balance = int(wallet["credits_balance"] if wallet else 0)

        # Bought credits work for Free and Pro (edit still requires Pro above).
        if balance >= cost:
            conn.execute(
                "UPDATE wallets SET credits_balance = credits_balance - ? WHERE telegram_id = ?",
                (cost, telegram_id),
            )
            conn.commit()
            return ConsumeResult(ok=True, charged="credits", amount=cost)

        if is_pro:
            conn.commit()
            return ConsumeResult(
                ok=False,
                reason="no_credits",
                message=(
                    f"Не хватает кредитов (нужно {cost}, есть {balance}). "
                    "Докупите пакет: /pay"
                ),
            )

        # Free tier without credits: daily caps
        limit = FREE_DAILY[action]
        if limit <= 0:
            conn.commit()
            return ConsumeResult(
                ok=False,
                reason="pro_required",
                message="Эта функция доступна на Pro. Оформите подписку: /pay",
            )
        col = {
            "ask": "daily_ask",
            "summary": "daily_summary",
            "card": "daily_card",
        }[action]
        daily = conn.execute(
            f"SELECT {col} AS used FROM wallets WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        used = int(daily["used"] if daily else 0)
        if used >= limit:
            conn.commit()
            return ConsumeResult(
                ok=False,
                reason="daily_limit",
                message=(
                    f"Дневной лимит Free исчерпан ({used}/{limit}). "
                    "Подписка или пакет кредитов: /pay"
                ),
            )
        conn.execute(
            f"UPDATE wallets SET {col} = {col} + 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        conn.commit()
        return ConsumeResult(ok=True, charged="daily", amount=1)


def refund_consume(
    telegram_id: int,
    result: ConsumeResult,
    action: Action | None = None,
) -> None:
    """Undo a successful consume when the operation failed before delivering value."""
    if not result.ok or not result.charged or result.amount <= 0:
        return
    _ensure_init()
    with _lock, connect() as conn:
        if result.charged == "credits":
            conn.execute(
                "UPDATE wallets SET credits_balance = credits_balance + ? WHERE telegram_id = ?",
                (result.amount, telegram_id),
            )
        elif result.charged == "daily" and action in {"ask", "summary", "card"}:
            col = {
                "ask": "daily_ask",
                "summary": "daily_summary",
                "card": "daily_card",
            }[action]
            conn.execute(
                f"UPDATE wallets SET {col} = CASE WHEN {col} > 0 THEN {col} - 1 ELSE 0 END "
                "WHERE telegram_id = ?",
                (telegram_id,),
            )
        conn.commit()


def create_order(
    telegram_id: int,
    sku_id: str,
    provider: Rail,
    language_code: str | None = None,
) -> dict[str, Any]:
    ensure_user(telegram_id, language_code)
    sku = get_sku(sku_id)
    if provider == "unitpay":
        amount = float(sku.price_rub)
        currency = "RUB"
    else:
        amount = float(sku.price_stars)
        currency = "XTR"
    order_id = f"ord_{uuid.uuid4().hex[:16]}"
    now = _iso(_now())
    with _lock, connect() as conn:
        conn.execute(
            "INSERT INTO orders (order_id, telegram_id, sku, amount, currency, provider, "
            "status, created_at, paid_at) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, NULL)",
            (order_id, telegram_id, sku_id, amount, currency, provider, now),
        )
        conn.commit()
    return {
        "order_id": order_id,
        "telegram_id": telegram_id,
        "sku": sku_id,
        "amount": amount,
        "currency": currency,
        "provider": provider,
        "status": "created",
        "sku_obj": sku,
    }


def get_order(order_id: str) -> dict[str, Any] | None:
    _ensure_init()
    with _lock, connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        return dict(row)


def mark_order_pending(order_id: str) -> None:
    _ensure_init()
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE orders SET status = 'pending' WHERE order_id = ? AND status = 'created'",
            (order_id,),
        )
        conn.commit()


def grant_order(
    order_id: str,
    *,
    provider: str,
    provider_payment_id: str,
    raw: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Idempotently grant entitlements for a paid order."""
    _ensure_init()
    raw_text = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
    with _lock, connect() as conn:
        existing = conn.execute(
            "SELECT order_id FROM payments WHERE provider = ? AND provider_payment_id = ?",
            (provider, provider_payment_id),
        ).fetchone()
        if existing is not None:
            order = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?",
                (existing["order_id"],),
            ).fetchone()
            return {"ok": True, "duplicate": True, "order": dict(order) if order else None}

        order = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            return {"ok": False, "error": "order_not_found"}

        if provider and order["provider"] != provider:
            return {"ok": False, "error": "provider_mismatch"}

        if order["status"] == "paid":
            conn.execute(
                "INSERT OR IGNORE INTO payments (provider, provider_payment_id, order_id, raw, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, provider_payment_id, order_id, raw_text, _iso(_now())),
            )
            conn.commit()
            return {"ok": True, "duplicate": True, "order": dict(order)}

        sku = get_sku(order["sku"])
        telegram_id = int(order["telegram_id"])
        now = _now()
        paid_at = _iso(now)

        if sku.days > 0:
            sub = conn.execute(
                "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            base = _parse_iso(sub["expires_at"] if sub else None)
            if base is None or base < now:
                base = now
            expires = base + timedelta(days=sku.days)
            conn.execute(
                "UPDATE subscriptions SET tier = 'pro', expires_at = ?, source = ?, "
                "external_id = ? WHERE telegram_id = ?",
                (_iso(expires), provider, provider_payment_id, telegram_id),
            )

        if sku.credits > 0:
            conn.execute(
                "UPDATE wallets SET credits_balance = credits_balance + ? WHERE telegram_id = ?",
                (sku.credits, telegram_id),
            )

        conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ? WHERE order_id = ?",
            (paid_at, order_id),
        )
        conn.execute(
            "INSERT INTO payments (provider, provider_payment_id, order_id, raw, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, provider_payment_id, order_id, raw_text, paid_at),
        )
        conn.commit()
        refreshed = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        return {"ok": True, "duplicate": False, "order": dict(refreshed), "sku": sku}


def admin_grant_credits(telegram_id: int, credits: int, language_code: str | None = None) -> None:
    ensure_user(telegram_id, language_code)
    with _lock, connect() as conn:
        conn.execute(
            "UPDATE wallets SET credits_balance = credits_balance + ? WHERE telegram_id = ?",
            (credits, telegram_id),
        )
        conn.commit()


def admin_grant_pro(
    telegram_id: int,
    days: int = 30,
    language_code: str | None = None,
    credits: int | None = None,
) -> None:
    ensure_user(telegram_id, language_code)
    pro_credits = get_sku("pro_month").credits if credits is None else credits
    with _lock, connect() as conn:
        now = _now()
        sub = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        base = _parse_iso(sub["expires_at"] if sub else None)
        if base is None or base < now:
            base = now
        expires = base + timedelta(days=days)
        conn.execute(
            "UPDATE subscriptions SET tier = 'pro', expires_at = ?, source = 'admin', "
            "external_id = NULL WHERE telegram_id = ?",
            (_iso(expires), telegram_id),
        )
        if pro_credits > 0:
            conn.execute(
                "UPDATE wallets SET credits_balance = credits_balance + ? WHERE telegram_id = ?",
                (pro_credits, telegram_id),
            )
        conn.commit()


def fulfill_stars_payment(
    *,
    order_id: str,
    telegram_id: int,
    charge_id: str,
    is_recurring: bool = False,
    is_first_recurring: bool = False,
    subscription_expiration_date: int | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grant first Stars payment or renew Pro on recurring charge."""
    payment_key = charge_id
    # First charge of a subscription is a normal grant, not a renewal.
    renewing = bool(is_recurring) and not bool(is_first_recurring)
    if renewing and subscription_expiration_date:
        payment_key = f"{charge_id}:{subscription_expiration_date}"
    elif renewing:
        # Stable per-day key so Telegram retries stay idempotent.
        payment_key = f"{charge_id}:renew:{_today()}"

    order = get_order(order_id)
    if order is None:
        return {"ok": False, "error": "order_not_found"}
    if int(order["telegram_id"]) != int(telegram_id):
        return {"ok": False, "error": "order_user_mismatch"}
    if order["provider"] != "stars":
        return {"ok": False, "error": "provider_mismatch"}

    # Recurring renewals: order already paid — extend without creating a new order
    if renewing and order["status"] == "paid":
        _ensure_init()
        sku = get_sku(order["sku"])
        with _lock, connect() as conn:
            existing = conn.execute(
                "SELECT id FROM payments WHERE provider = ? AND provider_payment_id = ?",
                ("stars", payment_key),
            ).fetchone()
            if existing is not None:
                return {"ok": True, "duplicate": True, "order": order}

            now = _now()
            if subscription_expiration_date:
                expires = datetime.fromtimestamp(
                    int(subscription_expiration_date), tz=timezone.utc
                )
            else:
                base = now
                sub = conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
                    (telegram_id,),
                ).fetchone()
                prev = _parse_iso(sub["expires_at"] if sub else None)
                if prev and prev > now:
                    base = prev
                expires = base + timedelta(days=sku.days or 30)

            conn.execute(
                "UPDATE subscriptions SET tier = 'pro', expires_at = ?, source = 'stars', "
                "external_id = ? WHERE telegram_id = ?",
                (_iso(expires), charge_id, telegram_id),
            )
            if sku.credits > 0:
                conn.execute(
                    "UPDATE wallets SET credits_balance = credits_balance + ? "
                    "WHERE telegram_id = ?",
                    (sku.credits, telegram_id),
                )
            conn.execute(
                "INSERT INTO payments (provider, provider_payment_id, order_id, raw, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "stars",
                    payment_key,
                    order_id,
                    json.dumps(raw or {}, ensure_ascii=False),
                    _iso(now),
                ),
            )
            conn.commit()
        return {"ok": True, "duplicate": False, "renewed": True, "order": order}

    return grant_order(
        order_id,
        provider="stars",
        provider_payment_id=payment_key,
        raw=raw,
    )
