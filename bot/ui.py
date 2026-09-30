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
        input_field_placeholder="Файл, вопрос, бланк, реферат или карточка…",
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
            [
                InlineKeyboardButton(text="Что не заполнено", callback_data="menu:gaps"),
                InlineKeyboardButton(text="Пример заполнения", callback_data="menu:docs_fill_ex"),
            ],
            [
                InlineKeyboardButton(text="Пример бланка", callback_data="menu:docs_form_ex"),
                InlineKeyboardButton(text="Пример текста", callback_data="menu:docs_text_ex"),
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    )


def clarify_intent_keyboard(options: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for opt in options:
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or oid).strip()
        if not oid or not label:
            continue
        rows.append(
            [InlineKeyboardButton(text=label[:64], callback_data=f"docs:go:{oid}")]
        )
    rows.append(
        [InlineKeyboardButton(text="Отмена", callback_data="docs:go:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        "<b>Очистить чат?</b>\n\n"
        "Удалим переписку в этом чате "
        "(сообщения старше ~48 часов Telegram боту стереть не даёт).\n"
        "Также очистятся документы, карточки и сессия.\n"
        "Баланс и тариф не трогаем."
    )


def welcome_text(name: str | None = None) -> str:
    greet = f"Привет, {escape(name)}!" if name else "Привет!"
    return (
        f"<b>My AI Assistant</b>\n"
        f"{greet}\n\n"
        "Карточки для <b>Wildberries</b> и <b>Ozon</b> "
        "и умная работа с документами — в одном чате.\n\n"
        "<b>Быстрый старт</b>\n"
        "1. «Карточка» — фото, видео или описание товара\n"
        "2. «Документы» — разобрать файл, поправить Word или создать новый\n\n"
        "Меню всегда внизу экрана."
    )


def help_text() -> str:
    return (
        "<b>Как пользоваться</b>\n\n"
        "<b>Карточка товара</b>\n"
        "Кнопка «Карточка» или /card\n"
        "• фото / короткое видео / описание\n"
        "• 3 слайда: каталог → интерьер → постер\n\n"
        "<b>Документы</b>\n"
        "Бот сам понимает задачу:\n"
        "• <b>Разобрать</b> — вопрос по файлу, содержание, «что не заполнено»\n"
        "• <b>Заполнить</b> — подставить ФИО/даты в бланк .docx\n"
        "• <b>Поправить</b> — точечные замены без поломки файла\n"
        "• <b>Создать бланк</b> — титульный, заявление, шаблон декларации\n"
        "• <b>Создать текст</b> — реферат, эссе, письмо, доклад\n\n"
        "Форматы: Word · PDF · Excel · PowerPoint · TXT · MD · CSV\n\n"
        "Сейчас все функции открыты бесплатно (тестовый режим).\n\n"
        "/balance · /clear"
    )


def card_prompt_text() -> str:
    return (
        "<b>Карточка для маркетплейса</b>\n\n"
        "Пришлите <b>фото или короткое видео</b> товара "
        "и при желании короткое описание.\n\n"
        "Соберу <b>3 слайда</b>. Стили чередуются:\n"
        "1. Каталог\n"
        "2. В интерьере\n"
        "3. Постер\n\n"
        "Пример:\n"
        "<i>Нужно продать такую кружку, керамика, объём 300 мл</i>"
    )


def card_example_text() -> str:
    return (
        "Скопируйте и отправьте боту:\n\n"
        "<code>Продаю велюровое кресло зелёное, металлические ножки "
        "с латунными наконечниками, для гостиной, сделано в России</code>"
    )


def docs_prompt_text() -> str:
    return (
        "<b>Документы</b>\n\n"
        "Пишите обычным языком — сам определю задачу.\n\n"
        "<b>Разобрать</b>\n"
        "<i>«Что в договоре про срок оплаты?»</i>\n"
        "<i>«Что ещё не заполнено?»</i>\n\n"
        "<b>Заполнить бланк</b>\n"
        "Пришлите .docx и данные:\n"
        "<i>«Добавь в файл: Чикасов Денис Владимирович, 27.11.1999, Богородицк»</i>\n\n"
        "<b>Поправить</b>\n"
        "<i>«Замени Иванова на Петрова»</i>\n\n"
        "<b>Создать</b>\n"
        "• бланк: <i>«Титульный лист декларации ИП»</i>\n"
        "• текст: <i>«Реферат на тему ИИ в медицине»</i>\n\n"
        "Помню контекст в чате: можно уточнять «сделай ФИО …» или «добавь эти данные».\n"
        "Форматы: Word · PDF · Excel · PPT · TXT · MD · CSV · до 20 МБ"
    )


def docs_form_example_text() -> str:
    return (
        "<b>Пример: бланк</b>\n"
        "Скопируйте и отправьте:\n\n"
        "<code>Сделай титульный лист налоговой декларации для ИП — "
        "только первая страница, поля пустыми для заполнения</code>\n\n"
        "Откройте полученный .docx в Word. "
        "Поля в квадратных скобках заполните сами или пришлите данные боту."
    )


def docs_text_example_text() -> str:
    return (
        "<b>Пример: текст</b>\n"
        "Скопируйте и отправьте:\n\n"
        "<code>Напиши реферат на тему «Искусственный интеллект в медицине», "
        "объём 8–10 тысяч знаков, со введением и заключением</code>\n\n"
        "Файл придёт как .docx — открывайте в Word, не в Блокноте."
    )


def docs_fill_example_text() -> str:
    return (
        "<b>Пример: заполнение</b>\n"
        "1) Пришлите .docx бланк\n"
        "2) Напишите (или в подписи к файлу):\n\n"
        "<code>В файле нужно добавить данные: "
        "Чикасов Денис Владимирович, 27.11.1999, город Богородицк</code>\n\n"
        "Покажу план замен → «Применить». "
        "Можно уточнять: <i>«Сделай ФИО …»</i> или <i>«Добавь эти данные»</i>."
    )


def status_card_copy() -> str:
    return "Готовлю текст для 3 слайдов…"


def status_card_photo() -> str:
    return "Делаю студийное фото товара… 10–40 сек."


def status_card_photo_lifestyle() -> str:
    return "Делаю фото товара в интерьере… 10–40 сек."


def status_card_layout() -> str:
    return "Собираю обложку, детали и преимущества…"


def status_doc_write(mode: str = "") -> str:
    if mode == "form":
        return "Собираю бланк / титульный лист…"
    if mode == "text":
        return "Пишу документ… обычно до минуты"
    return "Готовлю документ…"


def clarify_prompt_text(question: str) -> str:
    q = (question or "Уточните, что сделать?").strip()
    return (
        "<b>Уточните задачу</b>\n\n"
        f"{q}\n\n"
        "<i>Выберите вариант ниже — так результат будет точнее.</i>"
    )
