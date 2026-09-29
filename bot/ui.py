from __future__ import annotations

from html import escape

from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Подписи нижней клавиатуры (должны совпадать с обработчиками)
BTN_CARD = "Карточка"
BTN_DOCS = "Документы"
BTN_BALANCE = "Баланс"
BTN_PLANS = "Тарифы"
BTN_HELP = "Помощь"
BTN_CLEAR = "Очистить"

REPLY_BUTTONS = {BTN_CARD, BTN_DOCS, BTN_BALANCE, BTN_PLANS, BTN_HELP, BTN_CLEAR}

BOT_COMMANDS = [
    # /menu остаётся как алиас в коде, в списке не дублируем /start
    BotCommand(command="start", description="Открыть главное меню"),
    BotCommand(command="card", description="Собрать карточку WB / Ozon"),
    BotCommand(command="files", description="Мои загруженные файлы"),
    BotCommand(command="summary", description="Краткое содержание файла"),
    BotCommand(command="balance", description="Баланс и лимиты"),
    BotCommand(command="plans", description="Тарифы"),
    BotCommand(command="pay", description="Оплатить тариф"),
    BotCommand(command="clear", description="Очистить чат, файлы и сессию"),
    BotCommand(command="help", description="Как пользоваться ботом"),
]


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CARD), KeyboardButton(text=BTN_DOCS)],
            [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_PLANS)],
            [KeyboardButton(text=BTN_HELP), KeyboardButton(text=BTN_CLEAR)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Описание товара, файл или вопрос…",
    )


def home_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сделать карточку", callback_data="menu:card"),
                InlineKeyboardButton(text="Документы", callback_data="menu:docs"),
            ],
            [
                InlineKeyboardButton(text="Баланс", callback_data="menu:balance"),
                InlineKeyboardButton(text="Тарифы", callback_data="menu:plans"),
            ],
            [InlineKeyboardButton(text="Как пользоваться", callback_data="menu:help")],
        ]
    )


def card_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пример описания", callback_data="menu:card_example")],
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    )


def docs_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мои файлы", callback_data="menu:files"),
                InlineKeyboardButton(text="Содержание", callback_data="menu:summary"),
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    )


def back_home_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    )


def clear_confirm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить", callback_data="menu:clear_yes"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="menu:clear_no"),
            ],
        ]
    )


def clear_prompt_text() -> str:
    return (
        "<b>Очистить чат и рабочий стол?</b>\n\n"
        "Будет удалена вся переписка в этом чате "
        "(сообщения старше ~48 часов Telegram не даёт стереть боту).\n"
        "Также удалятся документы, карточки и сессия.\n"
        "Баланс и тариф сохранятся."
    )


def welcome_text(name: str | None = None) -> str:
    hi = f", {escape(name)}" if name else ""
    return (
        f"<b>My AI Assistant</b>{hi}\n\n"
        "Красивые карточки для <b>Wildberries</b> и <b>Ozon</b> "
        "и умная работа с документами — в одном чате.\n\n"
        "<b>Быстрый старт</b>\n"
        "1. «Карточка» — описание или фото товара\n"
        "2. «Документы» — файл и вопросы по тексту\n\n"
        "Меню внизу экрана всегда под рукой."
    )


def help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "<b>Карточка товара</b>\n"
        "Кнопка «Карточка» или /card\n"
        "• текст: что продаёте, цвет, материал\n"
        "• или своё фото с подписью\n"
        "На выходе — инфографика + текст для маркетплейса.\n\n"
        "<b>Документы</b>\n"
        "Пришлите docx / pdf / xlsx / pptx / txt\n"
        "Затем вопрос, /summary или правку Word (Pro).\n\n"
        "<b>Тариф</b>\n"
        "Free — до 100 действий в день\n"
        "Pro — правки .docx и расширенные лимиты\n\n"
        "/balance · /plans · /pay · /clear"
    )


def card_prompt_text() -> str:
    return (
        "<b>Карточка для маркетплейса</b>\n\n"
        "Пришлите <b>фото</b> или короткое описание.\n\n"
        "Бот соберёт <b>3 слайда</b> как у сильных карточек WB/Ozon:\n"
        "1. Обложка\n"
        "2. Детали с выносками\n"
        "3. Преимущества\n\n"
        "<i>Продаю деревянные стулья, красный цвет, массив дуба</i>"
    )


def card_example_text() -> str:
    return (
        "Скопируйте и отправьте:\n\n"
        "<code>Продаю велюровое кресло зелёное, металлические ножки "
        "с латунными наконечниками, для гостиной, сделано в России</code>"
    )


def docs_prompt_text() -> str:
    return (
        "<b>Документы</b>\n\n"
        "Отправьте файл в чат:\n"
        "Word · PDF · Excel · PowerPoint · TXT · MD · CSV\n\n"
        "Дальше:\n"
        "• вопрос по тексту\n"
        "• краткое содержание\n"
        "• правка .docx (Pro)\n\n"
        "До 20 МБ на файл."
    )


def status_card_copy() -> str:
    return "Готовлю текст для 3 слайдов…"


def status_card_photo() -> str:
    return "Рисую фото товара… 10–40 сек"


def status_card_layout() -> str:
    return "Собираю обложку, детали и преимущества…"
