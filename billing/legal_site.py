"""Публичные страницы для модерации UnitPay (оферта, ПДн, возврат, реквизиты)."""

from __future__ import annotations

from html import escape

from aiohttp import web

import config
from billing.skus import SKUS


def _base_url() -> str:
    return (config.BILLING_PUBLIC_BASE_URL or "").rstrip("/")


def _bot_link() -> str:
    u = (config.BOT_USERNAME or "").lstrip("@")
    return f"https://t.me/{u}" if u else "Telegram-бот (укажите BOT_USERNAME в .env)"


def _merchant() -> dict[str, str]:
    return {
        "name": config.MERCHANT_LEGAL_NAME or "— укажите MERCHANT_LEGAL_NAME в .env —",
        "inn": config.MERCHANT_INN or "— укажите MERCHANT_INN —",
        "ogrnip": config.MERCHANT_OGRNIP or "— укажите MERCHANT_OGRNIP —",
        # Достаточно региона/города — улицу и квартиру не светим
        "address": config.MERCHANT_ADDRESS or "Российская Федерация",
        "email": config.MERCHANT_EMAIL or "— укажите MERCHANT_EMAIL в .env —",
        "phone": (config.MERCHANT_PHONE or "").strip(),
    }


def _contacts_block() -> str:
    m = _merchant()
    phone_line = (
        f"Тел.: {escape(m['phone'])}<br/>"
        if m["phone"]
        else "Связь: через Telegram-бота и email (телефон по запросу).<br/>"
    )
    return (
        f"<p><b>{escape(m['name'])}</b><br/>"
        f"ИНН {escape(m['inn'])}, ОГРНИП {escape(m['ogrnip'])}<br/>"
        f"Место нахождения: {escape(m['address'])}<br/>"
        f"Email: <a href=\"mailto:{escape(m['email'])}\">{escape(m['email'])}</a><br/>"
        f"{phone_line}"
        f"Бот: <a href=\"{escape(_bot_link())}\">{escape(_bot_link())}</a></p>"
    )


def _brand() -> str:
    return config.BOT_DISPLAY_NAME or "Telegram-бот"


def _logo_html() -> str:
    brand = escape(_brand())
    return (
        '<a href="/" class="logo" style="display:inline-flex;align-items:center;'
        'gap:.55rem;color:inherit;text-decoration:none">'
        '<svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true">'
        '<rect width="36" height="36" rx="8" fill="#0b6e4f"/>'
        '<path d="M10 12h16v2.2H10V12zm0 5h16v2.2H10V17zm0 5h11v2.2H10V22z" fill="#fff"/>'
        "</svg>"
        f"<strong>{brand}</strong></a>"
    )


def _nav(active: str = "") -> str:
    items = [
        ("/", "Главная"),
        ("/order", "Заказать"),
        ("/offer", "Оферта"),
        ("/privacy", "Персональные данные"),
        ("/refund", "Возврат"),
        ("/security", "Безопасность"),
        ("/payment", "Оплата"),
        ("/contacts", "Контакты"),
    ]
    parts = []
    for href, title in items:
        cls = ' class="active"' if active == href else ""
        parts.append(f'<a href="{href}"{cls}>{escape(title)}</a>')
    return " · ".join(parts)


def _page(title: str, body: str, *, active: str = "") -> str:
    brand = escape(_brand())
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{escape(title)} — {brand}</title>
  <style>
    :root {{ --fg:#1a1a1a; --muted:#555; --bg:#f7f7f5; --card:#fff; --accent:#0b6e4f; }}
    body {{ margin:0; font-family: system-ui, Segoe UI, Roboto, Arial, sans-serif;
      background:var(--bg); color:var(--fg); line-height:1.55; }}
    header, main, footer {{ max-width:820px; margin:0 auto; padding:1.2rem 1rem; }}
    header {{ border-bottom:1px solid #ddd; }}
    nav a {{ color:var(--accent); text-decoration:none; margin-right:.2rem; }}
    nav a.active {{ font-weight:700; text-decoration:underline; }}
    .card {{ background:var(--card); border:1px solid #e5e5e5; border-radius:10px;
      padding:1.2rem 1.3rem; margin:1rem 0; }}
    h1 {{ font-size:1.55rem; margin:0 0 .6rem; }}
    h2 {{ font-size:1.15rem; margin:1.2rem 0 .4rem; }}
    p, li {{ color:var(--fg); }}
    .muted {{ color:var(--muted); font-size:.95rem; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:.45rem .3rem; border-bottom:1px solid #eee; }}
    footer {{ color:var(--muted); font-size:.9rem; border-top:1px solid #ddd; }}
    .btn {{ display:inline-block; background:var(--accent); color:#fff; padding:.55rem 1rem;
      border-radius:8px; text-decoration:none; border:0; cursor:pointer; font-size:1rem; }}
    label {{ display:block; margin:.7rem 0 .25rem; font-weight:600; }}
    input {{ width:100%; max-width:420px; padding:.5rem .6rem; border:1px solid #ccc;
      border-radius:6px; box-sizing:border-box; }}
  </style>
</head>
<body>
  <header>
    {_logo_html()}
    <nav style="margin-top:.5rem">{_nav(active)}</nav>
  </header>
  <main>
    <div class="card">
      {body}
    </div>
  </main>
  <footer>
    Исполнитель: {escape(_merchant()["name"])}, ИНН {escape(_merchant()["inn"])}.
    Сервис цифровых услуг в Telegram.
  </footer>
</body>
</html>"""


def _prices_table() -> str:
    rows = []
    for sku in SKUS.values():
        rows.append(
            "<tr>"
            f"<td>{escape(sku.title_ru)}</td>"
            f"<td>{sku.price_rub:.0f} ₽</td>"
            f"<td>{sku.credits} кредитов"
            + (f", Pro {sku.days} дн." if sku.days else "")
            + "</td></tr>"
        )
    return (
        "<table><thead><tr><th>Тариф</th><th>Цена</th><th>Что входит</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


async def page_home(_request: web.Request) -> web.Response:
    brand = escape(_brand())
    bot = escape(_bot_link())
    body = f"""
    <h1>{brand}</h1>
    <p>Telegram-бот для работы с документами и карточками маркетплейсов:
    вопросы по файлам, краткое содержание, создание и правка Word,
    карточки товара WB/Ozon, гид «ИП на НПД».</p>
    <p>
      <a class="btn" href="{bot}" target="_blank" rel="noopener">Открыть бота</a>
      <a class="btn" href="/order" style="margin-left:.4rem;background:#14532d">Оформить заказ</a>
    </p>
    <h2>Услуги и цены</h2>
    <p class="muted">Цифровой доступ к функциям бота (кредиты и подписка Pro).
    Услуга оказывается мгновенно после оплаты — в аккаунте Telegram.</p>
    {_prices_table()}
    <h2>Как оформить заказ</h2>
    <ol>
      <li>Откройте бота в Telegram.</li>
      <li>Команда /pay — выберите тариф.</li>
      <li>Укажите email для чека 54‑ФЗ.</li>
      <li>Оплатите через UnitPay. Кредиты/Pro начисляются автоматически.</li>
    </ol>
    <h2>Исполнитель</h2>
    {_contacts_block()}
    <p class="muted">Документы:
      <a href="/offer">оферта</a>,
      <a href="/privacy">персональные данные</a>,
      <a href="/refund">возврат</a>,
      <a href="/security">безопасность</a>,
      <a href="/payment">оплата</a>,
      <a href="/contacts">контакты</a>.
    </p>
    """
    return web.Response(
        text=_page(f"О сервисе — { _brand() }", body, active="/"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_offer(_request: web.Request) -> web.Response:
    m = _merchant()
    brand = escape(_brand())
    body = f"""
    <h1>Публичная оферта (пользовательское соглашение)</h1>
    <p class="muted">Редакция для цифровых услуг в Telegram-боте {brand}.</p>
    <h2>1. Общие положения</h2>
    <p>Настоящий документ является официальным предложением
    {escape(m["name"])} (далее — Исполнитель) заключить договор на условиях оферты
    с любым дееспособным лицом (далее — Заказчик) на оказание цифровых услуг
    доступа к функциям Telegram-бота {brand}.</p>
    <p>Акцепт оферты — оплата выбранного тарифа и/или начало использования
    платных функций бота после оплаты. С момента акцепта договор считается заключённым.</p>
    <h2>2. Предмет</h2>
    <p>Исполнитель предоставляет Заказчику доступ к цифровым функциям бота
    (кредиты и/или статус Pro) согласно выбранному тарифу. Услуга оказывается
    дистанционно через интерфейс Telegram.</p>
    <h2>3. Стоимость и оплата</h2>
    <p>Актуальные цены указаны на <a href="/">главной</a> и в боте (/plans).
    Оплата банковской картой и иными способами через платёжного партнёра UnitPay.
    После оплаты формируется кассовый чек 54‑ФЗ (email Заказчика).</p>
    <h2>4. Срок и порядок оказания</h2>
    <p>Услуга оказывается автоматически сразу после подтверждения оплаты
    (начисление кредитов / активация Pro). Доставка материального товара не производится.
    Срок оказания — мгновенно (онлайн), не позднее 24 часов с момента успешной оплаты
    при технических сбоях.</p>
    <h2>5. Права и обязанности</h2>
    <p>Заказчик обязуется указывать достоверные данные (в т.ч. email для чека)
    и не использовать сервис для целей, запрещённых законодательством РФ.
    Исполнитель обеспечивает работу бота в пределах разумной доступности,
    но не отвечает за сбои Telegram, сети и платёжных систем.</p>
    <h2>6. Возврат</h2>
    <p>Условия возврата — на странице <a href="/refund">Возврат</a>.</p>
    <h2>7. Персональные данные</h2>
    <p>Обработка ПДн — по <a href="/privacy">Политике</a>.</p>
    <h2>8. Реквизиты Исполнителя</h2>
    {_contacts_block()}
    """
    return web.Response(
        text=_page("Оферта", body, active="/offer"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_privacy(_request: web.Request) -> web.Response:
    m = _merchant()
    body = f"""
    <h1>Политика обработки персональных данных</h1>
    <p>Оператор: {escape(m["name"])}, ИНН {escape(m["inn"])},
    email {escape(m["email"])}.</p>
    <h2>Какие данные обрабатываем</h2>
    <ul>
      <li>идентификатор Telegram (telegram_id), язык интерфейса;</li>
      <li>email — для отправки кассового чека при оплате;</li>
      <li>сведения об оплатах и начисленных услугах;</li>
      <li>файлы и тексты, которые пользователь добровольно присылает в бота
      для оказания услуги (обработка документов / карточек).</li>
    </ul>
    <h2>Цели</h2>
    <p>Оказание услуг бота, приём оплаты, выдача чека 54‑ФЗ, поддержка пользователей,
    исполнение требований законодательства РФ.</p>
    <h2>Правовые основания</h2>
    <p>Договор-оферта, согласие субъекта при предоставлении данных,
    исполнение обязанностей оператора по 152‑ФЗ и 54‑ФЗ.</p>
    <h2>Передача третьим лицам</h2>
    <p>Платёжному партнёру UnitPay (для оплаты и чека), инфраструктурным
    провайдерам хостинга и LLM API — в объёме, необходимом для услуги.</p>
    <h2>Срок хранения</h2>
    <p>Данные платежей — согласно требованиям бухучёта и 54‑ФЗ.
    Сессионные файлы пользователя могут удаляться по команде очистки в боте
    и по истечении разумного срока хранения.</p>
    <h2>Права пользователя</h2>
    <p>Запрос на уточнение/удаление данных (кроме случаев, когда хранение обязательно
    законом) — на email {escape(m["email"])}.</p>
    """
    return web.Response(
        text=_page("Персональные данные", body, active="/privacy"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_refund(_request: web.Request) -> web.Response:
    m = _merchant()
    body = f"""
    <h1>Условия возврата и отмены</h1>
    <p>Услуга является цифровой и начинает исполняться сразу после оплаты
    (начисление кредитов / Pro).</p>
    <h2>Когда возврат возможен</h2>
    <ul>
      <li>двойное списание / техническая ошибка оплаты;</li>
      <li>услуга не предоставлена в течение 24 часов после успешной оплаты
      по вине Исполнителя.</li>
    </ul>
    <h2>Когда возврат не производится</h2>
    <ul>
      <li>кредиты или Pro уже начислены и могли быть использованы;</li>
      <li>Заказчик передумал после оказания услуги;</li>
      <li>сбои на стороне Telegram или устройства Заказчика при уже начисленной услуге.</li>
    </ul>
    <h2>Как оформить</h2>
    <p>Напишите на {escape(m["email"])} с указанием: telegram_id / @username,
    дата и сумма платежа, причина. Рассмотрение — до 10 рабочих дней.
    Возврат при одобрении — тем же способом оплаты (через платёжного партнёра),
    срок зачисления зависит от банка.</p>
    <h2>Отмена до оплаты</h2>
    <p>До завершения оплаты на стороне UnitPay заказ можно не оплачивать —
    списания не будет.</p>
    """
    return web.Response(
        text=_page("Возврат", body, active="/refund"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_security(_request: web.Request) -> web.Response:
    body = f"""
    <h1>Политика информационной безопасности</h1>
    <ul>
      <li>Доступ к серверу и панели управления ограничен;</li>
      <li>секреты (токены, ключи UnitPay) хранятся в переменных окружения, не в публичном коде;</li>
      <li>платёжные данные карт Исполнителем не собираются и не хранятся —
      оплату обрабатывает UnitPay;</li>
      <li>обмен с клиентом идёт по HTTPS (сайт) и через защищённые каналы Telegram;</li>
      <li>callback UnitPay проверяется по подписи и списку IP;</li>
      <li>резервные копии и логи используются только для поддержки и расследования инцидентов.</li>
    </ul>
    <p>О инцидентах безопасности: {escape(_merchant()["email"])}.</p>
    """
    return web.Response(
        text=_page("Безопасность", body, active="/security"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_payment(_request: web.Request) -> web.Response:
    body = f"""
    <h1>Порядок оплаты</h1>
    <ol>
      <li>В Telegram-боте команда /pay или раздел «Тарифы».</li>
      <li>Выбор тарифа (см. цены на <a href="/">главной</a>).</li>
      <li>Указание email для кассового чека (54‑ФЗ).</li>
      <li>Переход на защищённую страницу UnitPay и оплата.</li>
      <li>После подтверждения платежа кредиты/Pro начисляются в боте автоматически.</li>
    </ol>
    <p>Чек направляется на указанный email. Способы оплаты — согласно UnitPay
    (банковские карты и другие доступные методы).</p>
    <p>Оплачивая тариф, Заказчик принимает <a href="/offer">оферту</a>
    и <a href="/privacy">политику ПДн</a>.</p>
    {_prices_table()}
    """
    return web.Response(
        text=_page("Оплата", body, active="/payment"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_contacts(_request: web.Request) -> web.Response:
    m = _merchant()
    phone = m["phone"] or "по запросу на email (для защиты персональных данных)"
    body = f"""
    <h1>Контакты и реквизиты</h1>
    {_contacts_block()}
    <p>Контактный телефон: {escape(phone)}</p>
    <p class="muted">По оплате, чекам и возврату — email.
    Юридический адрес для корреспонденции — по запросу.</p>
    """
    return web.Response(
        text=_page("Контакты", body, active="/contacts"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_order_get(_request: web.Request) -> web.Response:
    body = f"""
    <h1>Оформление заказа</h1>
    <p>Цифровая услуга оказывается в Telegram-боте. Заполните форму —
    мы подтвердим заказ и направим к оплате / в бота.</p>
    <form method="post" action="/order">
      <label for="name">Имя</label>
      <input id="name" name="name" required maxlength="120" placeholder="Как к вам обращаться"/>
      <label for="email">Email (для чека 54‑ФЗ)</label>
      <input id="email" name="email" type="email" required maxlength="200" placeholder="name@mail.ru"/>
      <label for="telegram">Telegram (@username или номер)</label>
      <input id="telegram" name="telegram" required maxlength="120" placeholder="@username"/>
      <label for="sku">Тариф</label>
      <select id="sku" name="sku" style="width:100%;max-width:420px;padding:.5rem">
        <option value="pro_month">Pro на 30 дней — 299 ₽</option>
        <option value="credits_20">20 кредитов — 99 ₽</option>
        <option value="credits_50">50 кредитов — 199 ₽</option>
      </select>
      <p class="muted" style="margin-top:1rem">Нажимая «Отправить», вы принимаете
      <a href="/offer">оферту</a> и <a href="/privacy">политику ПДн</a>.</p>
      <p><button class="btn" type="submit">Отправить заявку</button></p>
    </form>
    """
    return web.Response(
        text=_page("Заказать", body, active="/order"),
        content_type="text/html",
        charset="utf-8",
    )


async def page_order_post(request: web.Request) -> web.Response:
    data = await request.post()
    name = escape(str(data.get("name") or "").strip()[:120])
    email = escape(str(data.get("email") or "").strip()[:200])
    tg = escape(str(data.get("telegram") or "").strip()[:120])
    sku = escape(str(data.get("sku") or "").strip()[:40])
    bot = escape(_bot_link())
    body = f"""
    <h1>Заявка принята</h1>
    <p>Спасибо, {name}. Мы получили данные для заказа:</p>
    <ul>
      <li>Email: {email}</li>
      <li>Telegram: {tg}</li>
      <li>Тариф: {sku}</li>
    </ul>
    <p>Продолжите в боте — там же можно сразу оплатить выбранный тариф (/pay):</p>
    <p><a class="btn" href="{bot}" target="_blank" rel="noopener">Открыть Telegram-бота</a></p>
    <p class="muted">Чек 54‑ФЗ придёт на указанный email после оплаты через UnitPay.</p>
    """
    return web.Response(
        text=_page("Заявка принята", body, active="/order"),
        content_type="text/html",
        charset="utf-8",
    )


def legal_links_for_bot() -> str:
    base = _base_url()
    if not base or "your_domain" in base.lower():
        return (
            "Сайт с офертой ещё не опубликован (нужен BILLING_PUBLIC_BASE_URL).\n"
            "После настройки домена будут ссылки: /offer /privacy /refund /contacts"
        )
    return (
        f"<b>Правовая информация</b>\n"
        f"• <a href=\"{base}/\">О сервисе и цены</a>\n"
        f"• <a href=\"{base}/offer\">Оферта</a>\n"
        f"• <a href=\"{base}/privacy\">Персональные данные</a>\n"
        f"• <a href=\"{base}/refund\">Возврат</a>\n"
        f"• <a href=\"{base}/payment\">Оплата</a>\n"
        f"• <a href=\"{base}/contacts\">Реквизиты</a>"
    )


def merchant_ready() -> bool:
    # Телефон необязателен — меньше палим личное; email и юр.идентификаторы нужны UnitPay.
    return all(
        [
            config.MERCHANT_LEGAL_NAME,
            config.MERCHANT_INN,
            config.MERCHANT_OGRNIP,
            config.MERCHANT_ADDRESS,
            config.MERCHANT_EMAIL,
            config.BOT_USERNAME,
            _base_url() and "your_domain" not in _base_url().lower(),
        ]
    )


def register_legal_routes(app: web.Application) -> None:
    app.router.add_get("/", page_home)
    app.router.add_get("/order", page_order_get)
    app.router.add_post("/order", page_order_post)
    app.router.add_get("/offer", page_offer)
    app.router.add_get("/privacy", page_privacy)
    app.router.add_get("/refund", page_refund)
    app.router.add_get("/security", page_security)
    app.router.add_get("/payment", page_payment)
    app.router.add_get("/contacts", page_contacts)
