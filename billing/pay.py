from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

from billing.service import create_order, get_balance, get_rail, mark_order_pending, set_rail
from billing.skus import SKUS, Rail, get_sku
from billing.unitpay import build_payment_url
import config
from config import STARS_SUBSCRIPTION_PERIOD


def plans_text(lang_ru: bool = True) -> str:
    if lang_ru:
        lines = [
            "<b>Тарифы</b>",
            "",
            "<b>Free</b> — до 100 вопросов / summary / карточек в день, "
            "до 15 генераций текста (реферат и т.п.), без правок Word",
            "<b>Pro</b> (30 дней) — пул кредитов + правки .docx",
            "<b>Пакеты кредитов</b> — докупка (правки Word только на Pro)",
            "",
        ]
        for sku in SKUS.values():
            lines.append(
                f"• <b>{sku.title_ru}</b> — {sku.price_rub:.0f} ₽ / {sku.price_stars} ⭐"
                + (f", +{sku.credits} кр." if sku.credits else "")
            )
        lines.append("")
        lines.append("Оплата: /pay или кнопка «Тарифы»")
        return "\n".join(lines)
    lines = [
        "<b>Plans</b>",
        "",
        "<b>Free</b> — up to 100 asks / summaries / cards per day, no Word edits",
        "<b>Pro</b> (30 days) — credit pool + .docx edits",
        "<b>Credit packs</b> — top-ups (except Word edits)",
        "",
    ]
    for sku in SKUS.values():
        lines.append(
            f"• <b>{sku.title_en}</b> — {sku.price_stars} Stars / {sku.price_rub:.0f} RUB"
            + (f", +{sku.credits} cr." if sku.credits else "")
        )
    lines.append("")
    lines.append("Pay: /pay")
    return "\n".join(lines)


def balance_text(telegram_id: int, language_code: str | None = None) -> str:
    info = get_balance(telegram_id, language_code)
    lang_ru = (language_code or "").lower().startswith("ru") or info.rail == "unitpay"
    if lang_ru:
        lines = [
            "<b>Баланс</b>",
            "",
            f"Тариф: <b>{'Pro' if info.is_pro else 'Free'}</b>",
            f"Кредиты: <b>{info.credits}</b>",
            f"Оплата: {'₽ UnitPay' if info.rail == 'unitpay' else 'Stars'}",
        ]
        if info.is_pro and info.expires_at:
            lines.append(f"Pro до: {info.expires_at}")
        if config.BILLING_OPEN_ACCESS:
            lines.append("")
            lines.append("<b>Тест: открытый доступ</b> — лимиты не блокируют.")
            lines.append(
                f"Сегодня использовано: вопросы {info.daily_ask} · "
                f"summary {info.daily_summary} · "
                f"карточки {info.daily_card} · "
                f"тексты {info.daily_write} · "
                f"правки {info.daily_edit}"
            )
            lines.append("Очистка чата баланс и попытки не сбрасывает.")
        elif not info.is_pro:
            lines.append("")
            lines.append("<b>Сегодня (Free)</b>")
            lines.append(
                f"Вопросы {info.daily_ask}/{info.daily_ask_limit} · "
                f"summary {info.daily_summary}/{info.daily_summary_limit} · "
                f"карточки {info.daily_card}/{info.daily_card_limit} · "
                f"тексты {info.daily_write}/{info.daily_write_limit} · "
                f"правки {info.daily_edit}/{info.daily_edit_limit}"
            )
        return "\n".join(lines)
    lines = [
        "<b>Balance</b>",
        "",
        f"Plan: <b>{'Pro' if info.is_pro else 'Free'}</b>",
        f"Credits: <b>{info.credits}</b>",
        f"Payment: {'Stars' if info.rail == 'stars' else 'UnitPay RUB'}",
    ]
    if info.is_pro and info.expires_at:
        lines.append(f"Pro until: {info.expires_at}")
    if config.BILLING_OPEN_ACCESS:
        lines.append("")
        lines.append("<b>Test: open access</b> — limits not enforced.")
        lines.append(
            f"Used today: asks {info.daily_ask} · "
            f"summary {info.daily_summary} · "
            f"cards {info.daily_card} · "
            f"texts {info.daily_write} · "
            f"edits {info.daily_edit}"
        )
        lines.append("Clear chat does not reset balance or usage.")
    elif not info.is_pro:
        lines.append("")
        lines.append("<b>Today (Free)</b>")
        lines.append(
            f"Asks {info.daily_ask}/{info.daily_ask_limit} · "
            f"summary {info.daily_summary}/{info.daily_summary_limit} · "
            f"cards {info.daily_card}/{info.daily_card_limit} · "
            f"texts {info.daily_write}/{info.daily_write_limit} · "
            f"edits {info.daily_edit}/{info.daily_edit_limit}"
        )
    return "\n".join(lines)


def pay_keyboard(rail: Rail, lang_ru: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for sku_id, sku in SKUS.items():
        if lang_ru:
            label = f"{sku.title_ru} — {sku.price_rub:.0f} ₽" if rail == "unitpay" else (
                f"{sku.title_ru} — {sku.price_stars} ⭐"
            )
        else:
            label = (
                f"{sku.title_en} — {sku.price_stars} ⭐"
                if rail == "stars"
                else f"{sku.title_en} — {sku.price_rub:.0f} RUB"
            )
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"pay:{rail}:{sku_id}")]
        )
    if rail == "unitpay":
        switch = InlineKeyboardButton(
            text="Pay with Stars ⭐" if not lang_ru else "Оплата Stars ⭐",
            callback_data="rail:stars",
        )
    else:
        switch = InlineKeyboardButton(
            text="Оплата в ₽" if lang_ru else "Pay in RUB ₽",
            callback_data="rail:unitpay",
        )
    rows.append([switch])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def upsell_keyboard(telegram_id: int, language_code: str | None = None) -> InlineKeyboardMarkup:
    rail = get_rail(telegram_id, language_code)
    lang_ru = (language_code or "").lower().startswith("ru") or rail == "unitpay"
    return pay_keyboard(rail, lang_ru)


def start_unitpay_order(telegram_id: int, sku_id: str, language_code: str | None = None) -> str:
    if not config.UNITPAY_PUBLIC_KEY or not config.UNITPAY_SECRET_KEY:
        raise RuntimeError(
            "UnitPay не настроен. Заполните UNITPAY_PUBLIC_KEY и UNITPAY_SECRET_KEY в .env"
        )
    order = create_order(telegram_id, sku_id, "unitpay", language_code)
    mark_order_pending(order["order_id"])
    sku = order["sku_obj"]
    desc = sku.title_ru
    return build_payment_url(
        account=order["order_id"],
        sum_value=float(order["amount"]),
        desc=desc,
        currency="RUB",
    )


def stars_invoice_kwargs(
    telegram_id: int,
    sku_id: str,
    language_code: str | None = None,
) -> dict:
    """Build kwargs for answer_invoice (one-time) or create_invoice_link (subscription)."""
    order = create_order(telegram_id, sku_id, "stars", language_code)
    mark_order_pending(order["order_id"])
    sku = get_sku(sku_id)
    lang_ru = (language_code or "").lower().startswith("ru")
    title = sku.title_ru if lang_ru else sku.title_en
    description = (
        f"{sku.credits} кредитов" + (f", Pro {sku.days} дн." if sku.days else "")
        if lang_ru
        else f"{sku.credits} credits" + (f", Pro {sku.days} days" if sku.days else "")
    )
    base: dict = {
        "title": title[:32],
        "description": description[:255],
        "payload": f"stars:{order['order_id']}",
        "currency": "XTR",
        "prices": [LabeledPrice(label=title[:32], amount=int(sku.price_stars))],
    }
    if sku.days > 0:
        # Stars subscriptions are created via invoice links in current Bot API / aiogram.
        base["subscription_period"] = STARS_SUBSCRIPTION_PERIOD
        base["provider_token"] = ""
        base["mode"] = "link"
    else:
        base["provider_token"] = ""
        base["mode"] = "invoice"
    return base


def apply_rail_choice(telegram_id: int, rail: Rail, language_code: str | None = None) -> None:
    set_rail(telegram_id, rail, language_code)
