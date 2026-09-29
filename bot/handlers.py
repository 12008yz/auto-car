from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from html import escape
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    TelegramObject,
    User,
)

from billing.pay import upsell_keyboard
from billing.service import ConsumeResult, consume, ensure_user, refund_consume
from billing.skus import Action
from bot.session import PendingEdit, UserSession, clear_user_workspace, get_session
from bot import ui
from card.render import render_product_card_pack
from config import ALLOWED_SUFFIXES, DATA_DIR, IMAGE_SUFFIXES, MAX_FILE_BYTES, TELEGRAM_MAX_LEN
from docs.loaders import load_document
from edit.docx_patch import apply_patches, write_rewrite_docx
from llm.client import (
    ask_document,
    generate_product_photo,
    looks_like_card,
    looks_like_edit,
    make_product_card,
    plan_edits,
    summarize_document,
)
from rag.pipeline import Hit, chunk_blocks

router = Router()


class _TrackChatMessagesMiddleware(BaseMiddleware):
    """Запоминает message_id входящих сообщений для последующей очистки ленты."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            get_session(event.from_user.id).remember_chat_message(event.message_id)
        elif isinstance(event, CallbackQuery) and event.from_user is not None and event.message:
            get_session(event.from_user.id).remember_chat_message(event.message.message_id)
        return await handler(event, data)


router.message.middleware(_TrackChatMessagesMiddleware())
router.callback_query.middleware(_TrackChatMessagesMiddleware())


async def _delete_chat_messages(
    bot,
    chat_id: int,
    *,
    known_ids: Iterable[int] = (),
    up_to_id: int | None = None,
    depth: int = 10_000,
) -> int:
    """
    Чистит переписку в личке: message_id от 1 до текущего.
    Telegram не удаляет сообщения старше ~48 часов — такие id просто пропускаются.
    """
    known = [int(x) for x in known_ids if int(x) > 0]
    end = int(up_to_id or 0)
    if known:
        end = max(end, max(known))
    if end <= 0:
        return 0

    start = 1
    if depth > 0 and end - start + 1 > depth:
        start = end - depth + 1

    ids = list(range(start, end + 1))
    deleted = 0

    async def _delete_batch(batch: list[int]) -> int:
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=batch)
            return len(batch)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
            try:
                await bot.delete_messages(chat_id=chat_id, message_ids=batch)
                return len(batch)
            except Exception:
                pass
        except Exception:
            pass

        ok = 0
        for mid in batch:
            try:
                await bot.delete_message(chat_id, mid)
                ok += 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                try:
                    await bot.delete_message(chat_id, mid)
                    ok += 1
                except Exception:
                    continue
            except Exception:
                continue
        return ok

    for i in range(0, len(ids), 100):
        deleted += await _delete_batch(ids[i : i + 100])
        if i and i % 500 == 0:
            await asyncio.sleep(0.2)
    return deleted


def _remember_bot_message(user_id: int | None, message: Message | None) -> None:
    if user_id is None or message is None:
        return
    get_session(user_id).remember_chat_message(message.message_id)


def _user_id(user: User | None) -> int | None:
    return user.id if user is not None else None


def _session_of(user: User | None) -> UserSession | None:
    uid = _user_id(user)
    if uid is None:
        return None
    return get_session(uid)


_WIN_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_filename(name: str) -> str:
    cleaned = str(name).replace("\\", "/").split("/")[-1].strip()
    cleaned = _WIN_BAD_CHARS.sub("_", cleaned).strip(" .")
    if not cleaned:
        return "file"
    stem = cleaned.rsplit(".", 1)[0] if "." in cleaned[1:] else cleaned
    if stem.lower() in _WIN_RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def _fit(text: str, limit: int = TELEGRAM_MAX_LEN) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _split_text(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["(пустой ответ)"]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return chunks


async def _reply_long(message: Message, text: str) -> None:
    for part in _split_text(text):
        await message.answer(part)


async def _finish_status(status: Message, message: Message, text: str) -> None:
    try:
        await status.delete()
    except Exception:
        try:
            parts = _split_text(text)
            await status.edit_text(parts[0])
            for part in parts[1:]:
                await message.answer(part)
            return
        except Exception:
            pass
    await _reply_long(message, text)


def _format_citations(citations: object) -> str:
    if not isinstance(citations, list):
        return ""
    lines = []
    for item in citations:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "").strip()
        location = str(item.get("location") or "").strip()
        quote = str(item.get("quote") or "").strip()
        bit = " / ".join(x for x in (file_name, location) if x)
        if quote:
            lines.append(f"— {bit}: «{quote}»" if bit else f"— «{quote}»")
        elif bit:
            lines.append(f"— {bit}")
    if not lines:
        return ""
    return "\n\nИсточники:\n" + "\n".join(lines[:8])


def _edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Применить", callback_data="edit:apply"),
                InlineKeyboardButton(text="Отмена", callback_data="edit:cancel"),
            ]
        ]
    )


def _describe_plan(pending: PendingEdit) -> str:
    lines = [pending.summary or "План правок готов."]
    if pending.kind == "patch" and pending.patches:
        lines.append("Замены:")
        for i, patch in enumerate(pending.patches[:12], start=1):
            find = patch.get("find") or ""
            replace = patch.get("replace") or ""
            if len(find) > 80:
                find = find[:77] + "..."
            if len(replace) > 80:
                replace = replace[:77] + "..."
            lines.append(f"{i}. «{find}» → «{replace}»")
        if len(pending.patches) > 12:
            lines.append(f"… и ещё {len(pending.patches) - 12}")
    elif pending.kind == "rewrite" and pending.rewrite_text:
        preview = pending.rewrite_text.strip()
        if len(preview) > 900:
            preview = preview[:900] + "…"
        lines.append("Новый текст (черновик):\n" + preview)
    lines.append("\nПрименить изменения?")
    return _fit("\n".join(lines))


def _ingest_path(session: UserSession, path: Path) -> int:
    blocks = load_document(path)
    chunks = chunk_blocks(blocks, source=path.name)
    session.index.add_file(path, chunks)
    session.active_path = path
    session.pending = None
    return len(chunks)


async def _require_files(message: Message, session: UserSession) -> bool:
    if session.index.files:
        return True
    await message.answer(
        "Сначала пришлите документ — или откройте раздел «Документы».",
        reply_markup=ui.docs_inline(),
    )
    return False


async def _require_quota(message: Message, action: Action) -> ConsumeResult | None:
    user = message.from_user
    if user is None:
        return None
    lang = user.language_code
    ensure_user(user.id, lang)
    result = consume(user.id, action, lang)
    if result.ok:
        return result
    await message.answer(
        result.message or "Лимит исчерпан. /pay",
        reply_markup=upsell_keyboard(user.id, lang),
    )
    return None


def _refund(message: Message, charge: ConsumeResult | None, action: Action) -> None:
    user = message.from_user
    if user is None or charge is None:
        return
    refund_consume(user.id, charge, action)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user:
        ensure_user(message.from_user.id, message.from_user.language_code)
    name = message.from_user.first_name if message.from_user else None
    await message.answer(
        ui.welcome_text(name),
        parse_mode="HTML",
        reply_markup=ui.main_reply_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        ui.help_text(),
        parse_mode="HTML",
        reply_markup=ui.back_home_inline(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(
        ui.welcome_text(
            message.from_user.first_name if message.from_user else None
        ),
        parse_mode="HTML",
        reply_markup=ui.main_reply_keyboard(),
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    sent = await message.answer(
        ui.clear_prompt_text(),
        parse_mode="HTML",
        reply_markup=ui.clear_confirm_inline(),
    )
    _remember_bot_message(_user_id(message.from_user), sent)


@router.message(Command("card"))
async def cmd_card(message: Message, command: CommandObject) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    session.card_mode = True
    notes = (command.args or "").strip()
    if notes:
        charge = await _require_quota(message, "card")
        if charge is None:
            return
        await _make_card(message, session, notes, None, charge)
        return
    await message.answer(
        ui.card_prompt_text(),
        parse_mode="HTML",
        reply_markup=ui.card_inline(),
    )


@router.message(F.text.in_(ui.REPLY_BUTTONS))
async def on_menu_button(message: Message) -> None:
    text = (message.text or "").strip()
    session = _session_of(message.from_user)
    if session is None:
        return
    if text == ui.BTN_HELP:
        await cmd_help(message)
        return
    if text == ui.BTN_CLEAR:
        await cmd_clear(message)
        return
    if text == ui.BTN_CARD:
        session.card_mode = True
        await message.answer(
            ui.card_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if text == ui.BTN_DOCS:
        await message.answer(
            ui.docs_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if text == ui.BTN_BALANCE:
        from billing.handlers import cmd_balance

        await cmd_balance(message)
        return
    if text == ui.BTN_PLANS:
        from billing.handlers import cmd_plans

        await cmd_plans(message)
        return


@router.callback_query(F.data.startswith("menu:"))
async def on_menu_callback(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data or query.message is None:
        await query.answer()
        return
    action = query.data.split(":", 1)[1]
    session = get_session(query.from_user.id)
    await query.answer()

    if action == "home":
        await query.message.answer(
            ui.welcome_text(query.from_user.first_name),
            parse_mode="HTML",
            reply_markup=ui.main_reply_keyboard(),
        )
        return
    if action == "clear_no":
        try:
            await query.message.edit_text("Очистка отменена.")
        except Exception:
            await query.message.answer("Очистка отменена.")
        return
    if action == "clear_yes":
        chat_id = query.message.chat.id
        up_to = query.message.message_id
        known = list(session.chat_message_ids)
        known.append(up_to)
        try:
            await query.message.edit_text("Очищаю переписку…")
        except Exception:
            pass
        clear_user_workspace(query.from_user.id, DATA_DIR)
        await _delete_chat_messages(
            query.bot,
            chat_id,
            known_ids=known,
            up_to_id=up_to,
            depth=10_000,
        )
        sent = await query.bot.send_message(
            chat_id,
            ui.welcome_text(query.from_user.first_name),
            parse_mode="HTML",
            reply_markup=ui.main_reply_keyboard(),
        )
        _remember_bot_message(query.from_user.id, sent)
        return
    if action == "help":
        await query.message.answer(
            ui.help_text(),
            parse_mode="HTML",
            reply_markup=ui.back_home_inline(),
        )
        return
    if action == "card":
        session.card_mode = True
        await query.message.answer(
            ui.card_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if action == "card_example":
        session.card_mode = True
        await query.message.answer(
            ui.card_example_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if action == "docs":
        await query.message.answer(
            ui.docs_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "files":
        await _send_files_list(query.message, session)
        return
    if action == "summary":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите документ.",
                reply_markup=ui.docs_inline(),
            )
            return
        status = await query.message.answer("Готовлю краткое содержание…")
        charge = consume(query.from_user.id, "summary", query.from_user.language_code)
        if not charge.ok:
            await status.edit_text(charge.message or "Лимит исчерпан. /pay")
            await query.message.answer(
                "Открыть тарифы: /pay",
                reply_markup=upsell_keyboard(
                    query.from_user.id, query.from_user.language_code
                ),
            )
            return
        try:
            async with session.lock:
                chunks = session.index.preview_chunks()
            if not chunks:
                refund_consume(query.from_user.id, charge, "summary")
                await status.edit_text("В активных файлах нет текста.")
                return
            text = await asyncio.to_thread(summarize_document, chunks)
        except Exception as exc:
            refund_consume(query.from_user.id, charge, "summary")
            await status.edit_text(_fit(f"Не удалось: {exc}"))
            return
        try:
            await status.delete()
        except Exception:
            pass
        await _reply_long(query.message, text)
        return
    if action == "balance":
        from billing.pay import balance_text

        ensure_user(query.from_user.id, query.from_user.language_code)
        await query.message.answer(
            balance_text(query.from_user.id, query.from_user.language_code),
            parse_mode="HTML",
            reply_markup=ui.back_home_inline(),
        )
        return
    if action == "plans":
        from billing.pay import pay_keyboard, plans_text
        from billing.service import get_rail

        lang = query.from_user.language_code
        ensure_user(query.from_user.id, lang)
        rail = get_rail(query.from_user.id, lang)
        ru = (lang or "").lower().startswith("ru") or rail == "unitpay"
        await query.message.answer(
            plans_text(ru),
            parse_mode="HTML",
            reply_markup=pay_keyboard(rail, ru),
        )
        return


@router.message(Command("files"))
async def cmd_files(message: Message) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    await _send_files_list(message, session)


async def _send_files_list(message: Message, session: UserSession) -> None:
    if not session.index.files:
        await message.answer(
            "Файлов пока нет.\nПришлите документ в чат.",
            reply_markup=ui.docs_inline(),
        )
        return
    lines = []
    for item in session.index.files:
        mark = " ✓" if session.active_path and item.path == session.active_path else ""
        lines.append(f"• {escape(item.name)}{mark}")
    await message.answer(
        "<b>Ваши файлы</b>\n" + "\n".join(lines),
        parse_mode="HTML",
        reply_markup=ui.docs_inline(),
    )


@router.message(Command("use"))
async def cmd_use(message: Message, command: CommandObject) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer("Укажите имя файла: /use договор.docx")
        return
    lowered = name.lower()
    match = next(
        (f for f in session.index.files if f.name.lower() == lowered),
        None,
    )
    if match is None:
        match = next(
            (f for f in session.index.files if lowered in f.name.lower()),
            None,
        )
    if match is None:
        await message.answer("Файл не найден. Смотрите /files.")
        return
    session.active_path = match.path
    async with session.lock:
        session.pending = None
    await message.answer(f"Активный файл: {match.name}")


@router.message(Command("summary"))
async def cmd_summary(message: Message) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    if not await _require_files(message, session):
        return
    charge = await _require_quota(message, "summary")
    if charge is None:
        return
    status = await message.answer("Готовлю краткое содержание…")
    try:
        async with session.lock:
            chunks = session.index.preview_chunks()
        if not chunks:
            _refund(message, charge, "summary")
            await status.edit_text("В активных файлах нет текста для содержания.")
            return
        text = await asyncio.to_thread(summarize_document, chunks)
    except Exception as exc:
        _refund(message, charge, "summary")
        await status.edit_text(_fit(f"Не удалось сделать содержание: {exc}"))
        return
    await _finish_status(status, message, text)


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    session = _session_of(message.from_user)
    if session is None or not message.photo:
        return
    caption = (message.caption or "").strip()
    card_cmd = re.match(r"^/card(?:@\w+)?(?:\s+(.*))?$", caption, flags=re.IGNORECASE | re.DOTALL)
    if card_cmd:
        session.card_mode = True
        caption = (card_cmd.group(1) or "").strip()
    if not session.card_mode and not looks_like_card(caption):
        await message.answer(
            "Чтобы собрать карточку, нажмите «Карточка» или /card — "
            "затем описание либо фото товара."
        )
        return
    charge = await _require_quota(message, "card")
    if charge is None:
        return
    dest = session.user_dir(DATA_DIR) / "product.jpg"
    try:
        await message.bot.download(message.photo[-1], destination=dest)
    except Exception as exc:
        _refund(message, charge, "card")
        await message.answer(_fit(f"Не удалось скачать фото: {exc}"))
        return
    await _make_card(message, session, caption, dest, charge)


@router.message(F.document)
async def on_document(message: Message) -> None:
    doc = message.document
    session = _session_of(message.from_user)
    if doc is None or session is None:
        return
    name = _safe_filename(doc.file_name or "file")
    suffix = Path(name).suffix.lower()
    caption = (message.caption or "").strip()
    if suffix in IMAGE_SUFFIXES:
        card_cmd = re.match(r"^/card(?:@\w+)?(?:\s+(.*))?$", caption, flags=re.IGNORECASE | re.DOTALL)
        if card_cmd:
            session.card_mode = True
            caption = (card_cmd.group(1) or "").strip()
        if not session.card_mode and not looks_like_card(caption):
            await message.answer(
                "Это картинка. Для карточки нажмите «Карточка» "
                "и пришлите фото товара или описание."
            )
            return
        charge = await _require_quota(message, "card")
        if charge is None:
            return
        dest = session.user_dir(DATA_DIR) / name
        try:
            await message.bot.download(doc, destination=dest)
        except Exception as exc:
            _refund(message, charge, "card")
            await message.answer(_fit(f"Не удалось скачать фото: {exc}"))
            return
        await _make_card(message, session, caption, dest, charge)
        return
    if suffix not in ALLOWED_SUFFIXES:
        await message.answer(
            "Этот формат не поддерживается. Нужны: "
            + ", ".join(sorted(ALLOWED_SUFFIXES))
        )
        return
    size = doc.file_size or 0
    if size > MAX_FILE_BYTES:
        await message.answer("Файл больше 20 МБ — Telegram так не отдаст боту.")
        return
    dest = session.user_dir(DATA_DIR) / name
    status = await message.answer("Сохраняю и индексирую файл…")
    try:
        await message.bot.download(doc, destination=dest)
        async with session.lock:
            n_chunks = await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        await status.edit_text(_fit(f"Не удалось прочитать файл: {exc}"))
        return
    if n_chunks == 0:
        await status.edit_text(
            f"Файл {name} сохранён, но текста из него не получилось. "
            "PDF-скан без текстового слоя так не читается."
        )
        return
    await status.edit_text(
        f"✅ <b>{escape(name)}</b> загружен\n"
        f"Фрагментов: {n_chunks}\n\n"
        "Задайте вопрос по тексту или попросите правку (.docx — Pro).",
        parse_mode="HTML",
    )
    caption = (message.caption or "").strip()
    if caption and not caption.startswith("/"):
        if looks_like_edit(caption):
            await _handle_edit(message, session, caption)
        else:
            await _handle_question(message, session, caption)


@router.callback_query(F.data == "edit:cancel")
async def on_edit_cancel(query: CallbackQuery) -> None:
    session = _session_of(query.from_user)
    if session is None:
        await query.answer()
        return
    async with session.lock:
        session.pending = None
    await query.answer("Отменено")
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text("Правки отменены.")
        except Exception:
            pass


@router.callback_query(F.data == "edit:apply")
async def on_edit_apply(query: CallbackQuery) -> None:
    session = _session_of(query.from_user)
    if session is None:
        await query.answer()
        return
    if not isinstance(query.message, Message):
        await query.answer("Нет сообщения для ответа", show_alert=True)
        return
    dest_dir = session.user_dir(DATA_DIR)
    note = "Готово."
    dest: Path | None = None
    try:
        async with session.lock:
            pending = session.pending
            if pending is None:
                await query.answer("Нет черновика правок", show_alert=True)
                return
            session.pending = None
            await query.answer()
            stem = pending.source.stem or "document"
            dest = dest_dir / f"{stem}_edited.docx"
            n = 1
            while dest.exists():
                dest = dest_dir / f"{stem}_edited_{n}.docx"
                n += 1
            if pending.kind == "patch":
                result = await asyncio.to_thread(
                    apply_patches, pending.source, dest, pending.patches
                )
                note = f"Применено замен: {len(result['applied'])}."
                if result["missing"]:
                    note += " Не найдено: " + "; ".join(result["missing"][:5])
            elif pending.kind == "rewrite":
                await asyncio.to_thread(write_rewrite_docx, dest, pending.rewrite_text)
                note = "Файл переписан новым текстом."
            else:
                await query.message.answer("Нечего применять.")
                return
            await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        session.pending = None
        await query.message.answer(_fit(f"Не удалось сохранить правки: {exc}"))
        return
    if dest is None:
        return
    try:
        await query.message.edit_text(_fit(note))
    except Exception:
        await query.message.answer(_fit(note))
    await query.message.answer_document(FSInputFile(dest), caption=dest.name)


@router.message(F.text)
async def on_text(message: Message) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    session = _session_of(message.from_user)
    if session is None:
        return
    if session.card_mode or looks_like_card(text):
        charge = await _require_quota(message, "card")
        if charge is None:
            return
        await _make_card(message, session, text, None, charge)
        return
    if not await _require_files(message, session):
        return
    if looks_like_edit(text):
        await _handle_edit(message, session, text)
        return
    await _handle_question(message, session, text)


async def _make_card(
    message: Message,
    session: UserSession,
    notes: str,
    photo_path: Path | None,
    charge: ConsumeResult,
) -> None:
    status = await message.answer(ui.status_card_copy())
    image_bytes = None
    if photo_path and photo_path.exists():
        image_bytes = photo_path.read_bytes()
        if len(image_bytes) > MAX_FILE_BYTES:
            _refund(message, charge, "card")
            await status.edit_text("Фото больше 20 МБ.")
            return
    paths: list[Path] = []
    try:
        data = await asyncio.to_thread(make_product_card, notes or "", image_bytes)
        dest_dir = session.user_dir(DATA_DIR)
        if photo_path is None or not photo_path.exists():
            await status.edit_text(ui.status_card_photo())
            gen_bytes = await asyncio.to_thread(
                generate_product_photo,
                notes or str(data.get("title") or "product"),
                str(data.get("title") or ""),
                str(data.get("label") or ""),
                str(data.get("image_prompt") or ""),
            )
            photo_path = dest_dir / "product_gen.png"
            photo_path.write_bytes(gen_bytes)
        await status.edit_text(ui.status_card_layout())
        stem = "card"
        n = 1
        while (dest_dir / f"{stem}_hero.png").exists():
            stem = f"card_{n}"
            n += 1
        paths = await asyncio.to_thread(
            render_product_card_pack, dest_dir, data, photo_path, stem
        )
    except Exception as exc:
        _refund(message, charge, "card")
        await status.edit_text(_fit(f"Не удалось собрать карточку: {exc}"))
        return
    session.card_mode = False
    try:
        await status.delete()
    except Exception:
        pass
    title = str(data.get("title") or "Карточка товара")
    media = [
        InputMediaPhoto(
            media=FSInputFile(paths[0]),
            caption=_fit(f"{title}\nСлайды 1/3 — обложка · 2/3 — детали · 3/3 — выгоды", 900),
        )
    ]
    for path in paths[1:]:
        media.append(InputMediaPhoto(media=FSInputFile(path)))
    await message.answer_media_group(media)
    parts = []
    if data.get("description"):
        parts.append(str(data["description"]))
    if data.get("keywords"):
        parts.append("Ключевые запросы:\n" + str(data["keywords"]))
    if parts:
        await _reply_long(message, "\n\n".join(parts))
    await message.answer(
        "Готово: 3 слайда для маркетплейса. Можно сделать ещё или открыть меню.",
        reply_markup=ui.card_inline(),
    )


async def _handle_question(message: Message, session: UserSession, text: str) -> None:
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer("Ищу в документах…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
        if not hits:
            _refund(message, charge, "ask")
            await status.edit_text("По этому запросу фрагментов не нашлось.")
            return
        data = await asyncio.to_thread(ask_document, text, hits)
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    answer = str(data.get("answer") or "").strip() or "Пустой ответ модели."
    answer += _format_citations(data.get("citations") or [])
    await _finish_status(status, message, answer)


async def _handle_edit(message: Message, session: UserSession, text: str) -> None:
    active = session.active_path
    if active is None:
        await message.answer("Нет активного файла. Отправьте документ или выберите его через /use.")
        return
    if active.suffix.lower() != ".docx":
        await message.answer(
            "Править с сохранением файла можно только .docx. "
            "Задайте вопрос по тексту или отправьте Word."
        )
        return
    charge = await _require_quota(message, "edit")
    if charge is None:
        return
    status = await message.answer("Готовлю план правок…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
            if not hits:
                hits = [Hit(chunk=c, score=1.0) for c in session.index.preview_chunks(12)]
        if not hits:
            _refund(message, charge, "edit")
            await status.edit_text("В файле нет текста для правок.")
            return
        data = await asyncio.to_thread(
            plan_edits,
            text,
            hits,
            active.name,
            True,
        )
    except Exception as exc:
        _refund(message, charge, "edit")
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    kind = data.get("kind") or "none"
    if kind == "none" or (
        kind == "patch" and not data.get("patches")
    ) or (
        kind == "rewrite" and not str(data.get("rewrite_text") or "").strip()
    ):
        _refund(message, charge, "edit")
        summary = str(data.get("summary") or "Правки не получилось спланировать.")
        await status.edit_text(_fit(summary))
        return
    pending = PendingEdit(
        kind=kind,
        source=active,
        patches=list(data.get("patches") or []),
        rewrite_text=str(data.get("rewrite_text") or ""),
        summary=str(data.get("summary") or ""),
    )
    async with session.lock:
        session.pending = pending
    plan = _describe_plan(pending)
    try:
        await status.delete()
    except Exception:
        try:
            await status.edit_text(plan, reply_markup=_edit_keyboard())
            return
        except Exception:
            pass
    await message.answer(plan, reply_markup=_edit_keyboard())
