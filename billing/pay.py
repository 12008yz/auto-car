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
            "<b>Бесплатно каждый день</b>",
            "• 1 карточка товара",
            "• 5 вопросов по файлу (включая краткое содержание)",
            "• 1 создание документа",
            "• правки Word — за кредиты",
            "",
            "<b>Сколько стоят действия</b> (в кредитах)",
            "вопрос — 1 · содержание — 2 · карточка — 2 · "
            "создать документ — 3 · правка Word — 3",
            "",
            "<b>Pro</b> на 30 дней — сразу +200 кредитов.",
            "Бесплатные попытки на день те же. Кредиты нужны для правок Word "
            "и когда дневной лимит уже израсходован.",
            "",
            "<b>Купить</b>",
        ]
        for sku in SKUS.values():
            extra = f", +{sku.credits} кредитов" if sku.days else ""
            lines.append(
                f"• <b>{sku.title_ru}</b> — {sku.price_rub:.0f} ₽ / {sku.price_stars} ⭐"
                f"{extra}"
            )
        lines.append("")
        lines.append("Оплатить: /pay или кнопка «Тарифы»")
        return "\n".join(lines)
    lines = [
        "<b>Plans</b>",
        "",
        "<b>Free every day</b>",
        "• 1 product card",
        "• 5 file questions (including a short summary)",
        "• 1 document create",
        "• Word edits — use credits",
        "",
        "<b>What actions cost</b> (credits)",
        "question — 1 · summary — 2 · card — 2 · "
        "create document — 3 · Word edit — 3",
        "",
        "<b>Pro</b> for 30 days — +200 credits upfront.",
        "Daily free attempts stay the same. Credits cover Word edits "
        "and anything past today's free limit.",
        "",
        "<b>Buy</b>",
    ]
    for sku in SKUS.values():
        extra = f", +{sku.credits} credits" if sku.days else ""
        lines.append(
            f"• <b>{sku.title_en}</b> — {sku.price_stars} Stars / {sku.price_rub:.0f} RUB"
            f"{extra}"
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
            f"Тариф: <b>{'Pro' if info.is_pro else 'бесплатный'}</b>",
            f"Кредиты: <b>{info.credits}</b>",
            f"Оплата: {'₽ через UnitPay' if info.rail == 'unitpay' else 'Telegram Stars'}",
        ]
        if info.is_pro and info.expires_at:
            lines.append(f"Pro действует до: {info.expires_at}")
        lines.append("")
        if config.BILLING_OPEN_ACCESS:
            lines.append("<b>Тестовый режим</b> — пока всё бесплатно, лимиты не мешают.")
            lines.append(
                f"Сегодня использовано: карточка {info.daily_card} · "
                f"вопросы {info.daily_ask} · "
                f"создание {info.daily_write} · "
                f"правки {info.daily_edit}"
            )
            lines.append("Очистка чата не сбрасывает баланс и дневные счётчики.")
        else:
            lines.append("<b>Бесплатно сегодня</b> (использовано / лимит)")
            lines.append(
                f"карточка {info.daily_card}/{info.daily_card_limit} · "
                f"вопросы {info.daily_ask}/{info.daily_ask_limit} · "
                f"создание {info.daily_write}/{info.daily_write_limit}"
            )
            lines.append(
                "Правки Word — всегда за кредиты (3 за раз). "
                "Сверх дневного лимита — тоже кредиты."
            )
        return "\n".join(lines)
    lines = [
        "<b>Balance</b>",
        "",
        f"Plan: <b>{'Pro' if info.is_pro else 'Free'}</b>",
        f"Credits: <b>{info.credits}</b>",
        f"Payment: {'Telegram Stars' if info.rail == 'stars' else 'UnitPay (RUB)'}",
    ]
    if info.is_pro and info.expires_at:
        lines.append(f"Pro until: {info.expires_at}")
    lines.append("")
    if config.BILLING_OPEN_ACCESS:
        lines.append("<b>Test mode</b> — everything is free for now; limits don’t block.")
        lines.append(
            f"Used today: card {info.daily_card} · "
            f"questions {info.daily_ask} · "
            f"create {info.daily_write} · "
            f"edits {info.daily_edit}"
        )
        lines.append("Clear chat does not reset balance or daily counters.")
    else:
        lines.append("<b>Free today</b> (used / limit)")
        lines.append(
            f"card {info.daily_card}/{info.daily_card_limit} · "
            f"questions {info.daily_ask}/{info.daily_ask_limit} · "
            f"create {info.daily_write}/{info.daily_write_limit}"
        )
        lines.append(
            "Word edits always use credits (3 each). "
            "Past today’s free limit also uses credits."
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
