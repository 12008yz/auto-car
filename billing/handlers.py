from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
)

from billing.pay import (
    apply_rail_choice,
    balance_text,
    pay_keyboard,
    plans_text,
    stars_invoice_kwargs,
    start_unitpay_order,
)
from billing.service import (
    admin_grant_credits,
    admin_grant_pro,
    ensure_user,
    fulfill_stars_payment,
    get_order,
    get_rail,
)
from billing.skus import SKUS, Rail, get_sku
from config import BILLING_ADMIN_IDS

router = Router(name="billing")


def _lang_ru(message_or_user_lang: str | None, rail: Rail | None = None) -> bool:
    code = (message_or_user_lang or "").lower()
    if code.startswith("ru"):
        return True
    if rail == "unitpay" and not code.startswith("en"):
        return True
    return False


@router.message(Command("plans"))
async def cmd_plans(message: Message) -> None:
    if message.from_user is None:
        return
    lang = message.from_user.language_code
    ensure_user(message.from_user.id, lang)
    rail = get_rail(message.from_user.id, lang)
    await message.answer(plans_text(_lang_ru(lang, rail)))


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    if message.from_user is None:
        return
    lang = message.from_user.language_code
    ensure_user(message.from_user.id, lang)
    await message.answer(balance_text(message.from_user.id, lang))


@router.message(Command("pay"))
async def cmd_pay(message: Message) -> None:
    if message.from_user is None:
        return
    lang = message.from_user.language_code
    ensure_user(message.from_user.id, lang)
    rail = get_rail(message.from_user.id, lang)
    ru = _lang_ru(lang, rail)
    intro = (
        "Выберите тариф. Рельс: ₽ UnitPay."
        if rail == "unitpay" and ru
        else (
            "Choose a plan. Payment: Telegram Stars."
            if rail == "stars" and not ru
            else (
                "Выберите тариф. Оплата: Stars."
                if rail == "stars"
                else "Choose a plan. Payment: UnitPay (RUB)."
            )
        )
    )
    await message.answer(intro, reply_markup=pay_keyboard(rail, ru))


@router.callback_query(F.data.startswith("rail:"))
async def on_rail_switch(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data:
        await query.answer()
        return
    rail = query.data.split(":", 1)[1]
    if rail not in {"unitpay", "stars"}:
        await query.answer("Unknown rail", show_alert=True)
        return
    lang = query.from_user.language_code
    apply_rail_choice(query.from_user.id, rail, lang)  # type: ignore[arg-type]
    ru = _lang_ru(lang, rail)  # type: ignore[arg-type]
    text = (
        "Рельс: UnitPay (₽). Выберите тариф:"
        if rail == "unitpay" and ru
        else (
            "Rail: Stars. Choose a plan:"
            if rail == "stars" and not ru
            else (
                "Рельс: Stars. Выберите тариф:"
                if rail == "stars"
                else "Rail: UnitPay (RUB). Choose a plan:"
            )
        )
    )
    await query.answer()
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(text, reply_markup=pay_keyboard(rail, ru))  # type: ignore[arg-type]
        except Exception:
            await query.message.answer(text, reply_markup=pay_keyboard(rail, ru))  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("pay:"))
async def on_pay_sku(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data:
        await query.answer()
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Bad payload", show_alert=True)
        return
    _, rail, sku_id = parts
    if rail not in {"unitpay", "stars"} or sku_id not in SKUS:
        await query.answer("Unknown plan", show_alert=True)
        return
    lang = query.from_user.language_code
    uid = query.from_user.id
    apply_rail_choice(uid, rail, lang)  # type: ignore[arg-type]
    await query.answer()

    if rail == "unitpay":
        try:
            url = start_unitpay_order(uid, sku_id, lang)
        except Exception as exc:
            if isinstance(query.message, Message):
                await query.message.answer(f"UnitPay недоступен: {exc}")
            return
        text = (
            f"Оплата UnitPay:\n{url}\n\nПосле оплаты кредиты/Pro появятся автоматически."
            if _lang_ru(lang, "unitpay")
            else f"UnitPay checkout:\n{url}\n\nCredits/Pro will unlock after payment."
        )
        if isinstance(query.message, Message):
            await query.message.answer(text)
        return

    # Stars
    if query.message is None or not isinstance(query.message, Message):
        return
    try:
        kwargs = stars_invoice_kwargs(uid, sku_id, lang)
        mode = kwargs.pop("mode", "invoice")
        if mode == "link":
            link = await query.bot.create_invoice_link(**kwargs)
            text = (
                f"Подписка Stars (автопродление 30 дней):\n{link}"
                if _lang_ru(lang, "stars")
                else f"Stars subscription (auto-renews every 30 days):\n{link}"
            )
            await query.message.answer(text)
        else:
            await query.message.answer_invoice(**kwargs)
    except Exception as exc:
        await query.message.answer(f"Stars invoice error: {exc}")


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    payload = query.invoice_payload or ""
    if not payload.startswith("stars:"):
        await query.answer(ok=False, error_message="Unknown invoice")
        return
    order_id = payload.split(":", 1)[1]
    order = get_order(order_id)
    if order is None:
        await query.answer(ok=False, error_message="Order not found")
        return
    if int(order["telegram_id"]) != int(query.from_user.id):
        await query.answer(ok=False, error_message="Order mismatch")
        return
    if order["currency"] != "XTR":
        await query.answer(ok=False, error_message="Bad currency")
        return
    # Stars amounts are whole units; reject tampered invoices.
    try:
        expected = int(float(order["amount"]))
    except (TypeError, ValueError):
        expected = -1
    if int(query.total_amount) != expected:
        await query.answer(ok=False, error_message="Amount mismatch")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    if not payload.startswith("stars:"):
        await message.answer("Payment received, but order payload is unknown.")
        return
    order_id = payload.split(":", 1)[1]
    is_recurring = bool(getattr(sp, "is_recurring", False))
    exp = getattr(sp, "subscription_expiration_date", None)
    if hasattr(exp, "timestamp"):
        exp_ts = int(exp.timestamp())
    elif isinstance(exp, int):
        exp_ts = exp
    else:
        exp_ts = None
    is_first = bool(getattr(sp, "is_first_recurring", False))
    result = fulfill_stars_payment(
        order_id=order_id,
        telegram_id=message.from_user.id,
        charge_id=sp.telegram_payment_charge_id,
        is_recurring=is_recurring,
        is_first_recurring=is_first,
        subscription_expiration_date=exp_ts,
        raw={
            "currency": sp.currency,
            "total_amount": sp.total_amount,
            "payload": payload,
            "is_recurring": is_recurring,
            "is_first_recurring": is_first,
        },
    )
    if not result.get("ok"):
        await message.answer(f"Payment saved, but grant failed: {result.get('error')}")
        return
    lang = message.from_user.language_code
    await message.answer(
        "Оплата прошла. Баланс: /balance"
        if _lang_ru(lang, "stars")
        else "Payment successful. Balance: /balance"
    )


@router.message(Command("grant"))
async def cmd_grant(message: Message) -> None:
    """Admin test: /grant credits 50 | /grant pro 30"""
    if message.from_user is None:
        return
    if message.from_user.id not in BILLING_ADMIN_IDS:
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Usage: /grant credits N | /grant pro DAYS")
        return
    kind = parts[1].lower()
    try:
        value = int(parts[2])
    except ValueError:
        await message.answer("Number expected")
        return
    uid = message.from_user.id
    lang = message.from_user.language_code
    if kind == "credits":
        admin_grant_credits(uid, value, lang)
        await message.answer(f"Granted {value} credits. /balance")
    elif kind == "pro":
        admin_grant_pro(uid, value, lang)
        await message.answer(
            f"Granted Pro for {value} days (+{get_sku('pro_month').credits} credits). /balance"
        )
    else:
        await message.answer("Usage: /grant credits N | /grant pro DAYS")
