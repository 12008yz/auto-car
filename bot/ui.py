from __future__ import annotations

from html import escape

from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

import config

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
                InlineKeyboardButton(text="Факты", callback_data="menu:extract"),
                InlineKeyboardButton(text="Риски договора", callback_data="menu:risks"),
            ],
            [
                InlineKeyboardButton(text="Сравнить 2 файла", callback_data="menu:compare"),
                InlineKeyboardButton(text="Что не заполнено", callback_data="menu:gaps"),
            ],
            [
                InlineKeyboardButton(text="Пример заполнения", callback_data="menu:docs_fill_ex"),
                InlineKeyboardButton(text="Ещё примеры", callback_data="menu:docs_more_ex"),
            ],
            [
                InlineKeyboardButton(text="Пример бланка", callback_data="menu:docs_form_ex"),
                InlineKeyboardButton(text="Пример текста", callback_data="menu:docs_text_ex"),
            ],
            [
                InlineKeyboardButton(
                    text="Создать документ", callback_data="docs:go:write_text"
                ),
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    )


def business_kind_keyboard() -> InlineKeyboardMarkup:
    from docs.business_types import KIND_LABELS_RU

    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"docs:go:biz:{kind}")]
        for kind, label in KIND_LABELS_RU.items()
    ]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="docs:go:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def official_forms_keyboard(form_ids: list[str]) -> InlineKeyboardMarkup:
    from docs.official_forms import get_form

    rows: list[list[InlineKeyboardButton]] = []
    for fid in form_ids:
        form = get_form(fid)
        if form is None:
            continue
        label = form.title
        if len(label) > 64:
            label = label[:61] + "…"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"docs:go:official:{fid}")]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="docs:go:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def soft_landing_keyboard(*, has_files: bool) -> InlineKeyboardMarkup:
    """Кнопки, когда бот не уверен в намерении — без тупика."""
    if has_files:
        rows = [
            [
                InlineKeyboardButton(text="Ответить по файлу", callback_data="docs:go:ask"),
                InlineKeyboardButton(text="Заполнить / вставить", callback_data="docs:go:fill"),
            ],
            [
                InlineKeyboardButton(text="Поправить текст", callback_data="docs:go:edit"),
                InlineKeyboardButton(text="Что не заполнено", callback_data="menu:gaps"),
            ],
            [
                InlineKeyboardButton(text="Меню документов", callback_data="menu:docs"),
                InlineKeyboardButton(text="« В меню", callback_data="menu:home"),
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(text="Пришлю файл", callback_data="docs:go:need_file"),
                InlineKeyboardButton(text="Создать документ", callback_data="docs:go:write_text"),
            ],
            [
                InlineKeyboardButton(text="Документы", callback_data="menu:docs"),
                InlineKeyboardButton(text="Карточка", callback_data="menu:card"),
            ],
            [InlineKeyboardButton(text="« В меню", callback_data="menu:home")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def soft_landing_text(*, has_files: bool, hint: str = "") -> str:
    base = "Не до конца понял задачу — выберите, что сделать, или перефразируйте."
    if hint:
        base = f"{hint}\n\n{base}"
    if has_files:
        return (
            f"{base}\n\n"
            "Можно: ответить по тексту, заполнить поля, поправить файл "
            "или открыть меню."
        )
    return (
        f"{base}\n\n"
        "Пришлите файл, создайте новый документ или откройте раздел «Документы»."
    )


def understand_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    """Подтверждение понимания перед дорогим действием (создать документ)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, делай",
                    callback_data=f"docs:go:{action}",
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="docs:go:cancel")],
        ]
    )


def interpret_confirm_text(interpretation: str) -> str:
    """Неявное подтверждение: как бот понял запрос (простым языком)."""
    text = (interpretation or "").strip()
    if not text:
        return ""
    # Уже готовая дружелюбная фраза
    if text[0].isupper() and not text.lower().startswith("понял"):
        low = text.lower()
        if low.startswith(("сейчас", "ок", "хорошо", "смотрю", "ищу", "заполн", "правл", "сравн")):
            return text
    return f"Ок: {text}."


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
        "Также очистятся документы, карточки и сессия диалога.\n\n"
        "<b>Баланс, кредиты и дневные бесплатные попытки не трогаем.</b>"
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
    if config.BILLING_OPEN_ACCESS:
        billing_line = (
            "Пока тестовый режим: всё бесплатно. "
            "Тарифы и лимиты — /plans · баланс — /balance"
        )
    else:
        billing_line = (
            "Каждый день есть бесплатные попытки; дальше — кредиты или Pro. "
            "Подробнее: /plans · баланс: /balance"
        )
    return (
        "<b>Как пользоваться</b>\n\n"
        "<b>Карточка товара</b>\n"
        "Кнопка «Карточка» или /card — фото/видео/описание → 3 слайда\n\n"
        "<b>Документы</b>\n"
        "• вопрос / содержание / факты / риски договора\n"
        "• сравнить 2 файла · что не заполнено\n"
        "• заполнить бланк · точечная правка · сменить тон\n"
        "• оформить по образцу\n"
        "• создать: бланк, реферат, письмо, претензия, КП\n"
        "• деловые черновики: договор · акт · счёт · претензия · письмо\n"
        "• официальные бланки ФНС: «официальный бланк Р21001», «декларация УСН»\n\n"
        "Форматы: Word · PDF · Excel · PowerPoint · TXT · MD · CSV\n"
        f"{billing_line}\n\n"
        "/clear · /use имя_файла"
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
        "• <i>«Что в договоре про срок оплаты?»</i>\n"
        "• <i>«Проверь договор на риски»</i>\n"
        "• <i>«Вытащи стороны, даты и суммы»</i>\n"
        "• <i>«Сравни эти два файла»</i> (нужно ≥2 файла)\n"
        "• <i>«Что не заполнено?»</i>\n\n"
        "<b>Заполнить / поправить</b>\n"
        "• <i>«Добавь: Чикасов Денис…, 27.11.1999, Богородицк»</i>\n"
        "• <i>«Замени Иванова на Петрова»</i>\n"
        "• <i>«Сделай текст официальнее / короче»</i>\n\n"
        "<b>Создать</b>\n"
        "• бланк · реферат · письмо · претензия · КП\n"
        "• договор · акт · счёт (черновик Word — проверьте реквизиты)\n"
        "• официальный бланк ФНС — напишите код/название (Р21001, УСН, патент…)\n"
        "• <i>«Оформи по образцу»</i> — 2 файла: содержание + образец\n\n"
        "Форматы: Word · PDF · Excel · PPT · TXT · MD · CSV · до 20 МБ"
    )


def docs_form_example_text() -> str:
    return (
        "<b>Пример: бланк</b>\n\n"
        "<b>1) Официальный бланк ФНС</b>\n"
        "Напишите название формы обычным языком — пришлю "
        "<b>ссылку на страницу ФНС</b> и <b>файл</b>, если он есть на сайте.\n\n"
        "Примеры (скопируйте):\n"
        "<code>Нужен официальный бланк Р21001 — регистрация ИП</code>\n"
        "<code>Пришли уведомление о переходе на УСН с сайта ФНС</code>\n"
        "<code>Официальная декларация по УСН</code>\n"
        "<code>Заявление на патент официальное</code>\n\n"
        "В каталоге сейчас: Р21001 · Р24001 · Р26001 · уведомление по УСН · "
        "декларация УСН · заявление на патент.\n\n"
        "<b>2) Простой шаблон Word</b> (не бланк ФНС — макет для заполнения):\n"
        "<code>Сделай титульный лист налоговой декларации для ИП — "
        "только первая страница, поля пустыми для заполнения</code>"
    )


def docs_text_example_text() -> str:
    return (
        "<b>Пример: текст</b>\n\n"
        "<code>Напиши реферат на тему «Искусственный интеллект в медицине», "
        "объём 8–10 тысяч знаков, со введением и заключением</code>\n\n"
        "Или деловой:\n"
        "<code>Составь претензию поставщику о просрочке поставки на 14 дней, "
        "сумма договора 200000 руб</code>"
    )


def docs_fill_example_text() -> str:
    return (
        "<b>Пример: заполнение</b>\n"
        "1) Пришлите .docx\n"
        "2) Напишите:\n\n"
        "<code>В файле нужно добавить данные: "
        "Чикасов Денис Владимирович, 27.11.1999, город Богородицк</code>\n\n"
        "План замен → «Применить»."
    )


def docs_more_examples_text() -> str:
    return (
        "<b>Ещё примеры</b>\n\n"
        "<b>Риски:</b>\n"
        "<code>Проверь договор на риски</code>\n\n"
        "<b>Факты:</b>\n"
        "<code>Вытащи стороны, даты и суммы</code>\n\n"
        "<b>Сравнение:</b> загрузите 2 файла, затем\n"
        "<code>Сравни эти два файла</code>\n\n"
        "<b>Тон:</b>\n"
        "<code>Сделай текст официальнее</code>\n\n"
        "<b>По образцу:</b> содержание + образец оформления, затем\n"
        "<code>Оформи первый файл по образцу второго</code>\n\n"
        "<b>КП / письмо:</b>\n"
        "<code>Напиши коммерческое предложение на разработку сайта</code>"
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
