from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from bot.handlers import router
from config import LLM_API_KEY, TELEGRAM_BOT_TOKEN
from rag.pipeline import get_embedder

_PLACEHOLDERS = {"", "123456:replace_me", "sk-replace_me", "replace_me"}


def _is_missing(value: str) -> bool:
    v = (value or "").strip()
    return v in _PLACEHOLDERS or v.endswith("replace_me")


def require_secrets() -> None:
    missing: list[str] = []
    if _is_missing(TELEGRAM_BOT_TOKEN):
        missing.append("TELEGRAM_BOT_TOKEN")
    if _is_missing(LLM_API_KEY):
        missing.append("LLM_API_KEY")
    if not missing:
        return
    names = ", ".join(missing)
    raise SystemExit(
        f"Не заданы ключи: {names}.\n"
        "Откройте файл .env в папке проекта и вставьте:\n"
        "  TELEGRAM_BOT_TOKEN — токен от @BotFather\n"
        "  LLM_API_KEY — ключ OpenAI API (не подписка ChatGPT Plus)\n"
        "Затем снова запустите run_bot.cmd"
    )


async def _run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger(__name__)
    log.info("Загружаю модель поиска по документам…")
    get_embedder()
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Бот запущен, ожидает сообщения в Telegram.")
    await dp.start_polling(bot)


def main() -> None:
    require_secrets()
    asyncio.run(_run())
