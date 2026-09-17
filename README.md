# auto-car

Telegram-бот: работа с документами (чтение, вопросы, правки Word) и карточки товара для маркетплейсов.

## Запуск

1. Скопируйте `.env.example` в `.env`.
2. Вставьте `TELEGRAM_BOT_TOKEN` и `LLM_API_KEY`.
3. Для оплаты из РФ заполните UnitPay (`UNITPAY_*`) и `BILLING_PUBLIC_BASE_URL`
   (handler: `{BASE}/billing/unitpay`). Stars для EN работают без UnitPay.
4. Запустите `run_bot.cmd`.

Команды оплаты: `/plans`, `/balance`, `/pay`.

Не коммитьте файл `.env`.
