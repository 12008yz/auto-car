from __future__ import annotations

import asyncio
import logging
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
from bot.media import extract_video_frame
from bot.session import PendingClarify, PendingEdit, UserSession, clear_user_workspace, get_session
from bot import ui
from card.prepare import prepare_fallback_cutout, prepare_studio_product
from card.render import render_product_card_pack
from card.styles import (
    VARIANTS,
    VARIANT_TITLES,
    bump_card_count,
    peek_variant,
    scene_for_product,
)
from config import ALLOWED_SUFFIXES, DATA_DIR, IMAGE_SUFFIXES, MAX_FILE_BYTES, TELEGRAM_MAX_LEN
from docs.loaders import load_document
from edit.docx_patch import (
    apply_patches,
    filter_valid_patches,
    suggest_filename,
    write_rewrite_docx,
    write_structured_docx,
)
from edit.transforms import apply_transform, detect_transform
from llm.client import (
    analyze_contract_risks,
    apply_document_routing_guards,
    ask_document,
    check_document_gaps,
    classify_document_intent,
    compare_documents,
    extract_key_facts,
    format_by_sample,
    generate_document,
    generate_product_photo,
    looks_like_apply_pending,
    looks_like_card,
    looks_like_chitchat,
    looks_like_edit,
    looks_like_fill_data,
    looks_like_reverse_words,
    looks_like_short_gap_value,
    looks_like_tone,
    make_product_card,
    plan_edits,
    summarize_document,
)
from rag.pipeline import Hit, chunk_blocks

router = Router()
log = logging.getLogger(__name__)


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


def _unique_edited_path(dest_dir: Path, source: Path) -> Path:
    stem = source.stem or "document"
    dest = dest_dir / f"{stem}_edited.docx"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{stem}_edited_{n}.docx"
        n += 1
    return dest


async def _materialize_pending(session: UserSession) -> tuple[Path | None, str]:
    """Сохраняет черновик правок в новый .docx и делает его активным файлом."""
    dest_dir = session.user_dir(DATA_DIR)
    try:
        async with session.lock:
            pending = session.pending
            if pending is None:
                return None, "Нет черновика правок"
            op = session.last_op or pending.kind or "edit"
            session.pending = None
            dest = _unique_edited_path(dest_dir, pending.source)
            if pending.kind == "patch":
                result = await asyncio.to_thread(
                    apply_patches, pending.source, dest, pending.patches
                )
                note = f"Применено замен: {len(result['applied'])}."
                if result["missing"]:
                    note += " Не найдено: " + "; ".join(result["missing"][:5])
            elif pending.kind == "rewrite":
                await asyncio.to_thread(write_rewrite_docx, dest, pending.rewrite_text)
                note = "Готовый файл с новым текстом."
            else:
                session.flow = "idle"
                return None, "Нечего применять."
            await asyncio.to_thread(_ingest_path, session, dest)
            session.flow = "idle"
            session.remember_op(op)
            return dest, note
    except Exception as exc:
        session.clear_pending_flow()
        return None, f"Не удалось сохранить правки: {exc}"


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
        lines.append("")
        lines.append("Что изменится:")
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
        lines.append("")
        lines.append(
            "Проверьте соответствия (дата рождения — целиком ДД.ММ.ГГГГ, "
            "город — в поле адреса/города, не путаем с отчётным годом)."
        )
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
    # Новый/обновлённый файл сбрасывает незавершённый черновик правок
    session.pending = None
    if session.flow == "awaiting_confirm":
        session.flow = "idle"
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
        session.card_mode = False
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
            await query.message.edit_text("Ок, ничего не удаляю.")
        except Exception:
            await query.message.answer("Ок, ничего не удаляю.")
        return
    if action == "clear_yes":
        chat_id = query.message.chat.id
        up_to = query.message.message_id
        known = list(session.chat_message_ids)
        known.append(up_to)
        try:
            await query.message.edit_text("Очищаю чат…")

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
    if action == "docs_form_ex":
        session.card_mode = False
        await query.message.answer(
            ui.docs_form_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_text_ex":
        session.card_mode = False
        await query.message.answer(
            ui.docs_text_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_fill_ex":
        session.card_mode = False
        await query.message.answer(
            ui.docs_fill_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_more_ex":
        session.card_mode = False
        await query.message.answer(
            ui.docs_more_examples_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs":
        session.card_mode = False
        await query.message.answer(
            ui.docs_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "files":
        await _send_files_list(query.message, session)
        return
    if action == "gaps":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите документ.",
                reply_markup=ui.docs_inline(),
            )
            return
        await _handle_gaps(query.message, session)
        return
    if action == "risks":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите договор.",
                reply_markup=ui.docs_inline(),
            )
            return
        await _handle_risks(query.message, session)
        return
    if action == "extract":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите документ.",
                reply_markup=ui.docs_inline(),
            )
            return
        await _handle_extract(query.message, session)
        return
    if action == "compare":
        await _handle_compare(query.message, session)
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
                await status.edit_text("В загруженных файлах нет текста.")
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
        await message.answer("Файл не найден. Список: /files")
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
            await status.edit_text("В загруженных файлах нет текста.")
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
            "затем пришлите фото, видео или описание товара."
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


@router.message(F.video | F.animation | F.video_note)
async def on_video_media(message: Message) -> None:
    """Короткое видео товара → кадр для карточки (раньше видео просто игнорировалось)."""
    session = _session_of(message.from_user)
    if session is None:
        return
    caption = (message.caption or "").strip()
    card_cmd = re.match(r"^/card(?:@\w+)?(?:\s+(.*))?$", caption, flags=re.IGNORECASE | re.DOTALL)
    if card_cmd:
        session.card_mode = True
        caption = (card_cmd.group(1) or "").strip()
    if not session.card_mode and not looks_like_card(caption):
        await message.answer(
            "Чтобы сделать карточку из видео, нажмите «Карточка» или /card — "
            "затем пришлите короткое видео товара."
        )
        return

    media = message.video or message.animation or message.video_note
    if media is None:
        return
    size = int(getattr(media, "file_size", 0) or 0)
    if size > MAX_FILE_BYTES:
        await message.answer("Видео больше 20 МБ — Telegram не отдаст его боту.")
        return

    charge = await _require_quota(message, "card")
    if charge is None:
        return

    user_dir = session.user_dir(DATA_DIR)
    video_path = user_dir / "product_video.mp4"
    frame_path = user_dir / "product.jpg"
    status = await message.answer("Беру кадр из видео…")
    try:
        await message.bot.download(media, destination=video_path)
        await asyncio.to_thread(extract_video_frame, video_path, frame_path)
    except Exception as exc:
        # Telegram почти всегда отдаёт превью — запасной вариант
        thumb = getattr(media, "thumbnail", None)
        if thumb is not None:
            try:
                await message.bot.download(thumb, destination=frame_path)
            except Exception as exc2:
                _refund(message, charge, "card")
                try:
                    await status.edit_text(_fit(f"Не удалось взять кадр из видео: {exc2}"))
                except Exception:
                    await message.answer(_fit(f"Не удалось взять кадр из видео: {exc2}"))
                return
        else:
            _refund(message, charge, "card")
            try:
                await status.edit_text(_fit(f"Не удалось взять кадр из видео: {exc}"))
            except Exception:
                await message.answer(_fit(f"Не удалось взять кадр из видео: {exc}"))
            return
    try:
        await status.delete()
    except Exception:
        pass
    await _make_card(message, session, caption, frame_path, charge)


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
                "Это изображение. Для карточки нажмите «Карточка», "
                "затем пришлите фото/видео товара или описание."
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
        await message.answer("Файл больше 20 МБ — Telegram не отдаст его боту.")
        return
    dest = session.user_dir(DATA_DIR) / name
    status = await message.answer("Сохраняю файл…")
    try:
        await message.bot.download(doc, destination=dest)
        async with session.lock:
            n_chunks = await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        await status.edit_text(_fit(f"Не удалось прочитать файл: {exc}"))
        return
    if n_chunks == 0:
        await status.edit_text(
            f"Файл {name} сохранён, но текста из него не достал. "
            "Сканы PDF без текстового слоя так не читаются.",
            reply_markup=ui.docs_inline(),
        )
        return
    # Документ в чате — выходим из режима карточки, иначе следующий текст
    # уйдёт в генерацию фото товара.
    session.card_mode = False
    await status.edit_text(
        f"✅ <b>{escape(name)}</b> загружен\n"
        f"Фрагментов: {n_chunks}\n\n"
        "Можно задать вопрос или попросить правку (.docx).\n"
        "Или выберите действие ниже.",
        parse_mode="HTML",
        reply_markup=ui.docs_inline(),
    )
    caption = (message.caption or "").strip()
    if caption and not caption.startswith("/"):
        if detect_transform(caption):
            session.remember_doc_task("edit", caption)
            await _handle_edit(message, session, caption)
        elif looks_like_fill_data(caption):
            session.remember_doc_task("fill", caption, accumulate_facts=True)
            await _handle_edit(message, session, caption, mode="fill")
        elif looks_like_edit(caption):
            session.remember_doc_task("edit", caption)
            await _handle_edit(message, session, caption)
        else:
            session.remember_doc_task("ask", caption)
            await _handle_question(message, session, caption)


@router.callback_query(F.data == "edit:cancel")
async def on_edit_cancel(query: CallbackQuery) -> None:
    session = _session_of(query.from_user)
    if session is None:
        await query.answer()
        return
    async with session.lock:
        session.pending = None
    session.flow = "idle"
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
    dest, note = await _materialize_pending(session)
    if dest is None:
        await query.answer(note or "Нет черновика правок", show_alert=True)
        return
    await query.answer()
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

    # Привет / small-talk — не требуем файл
    if looks_like_chitchat(text) and not (
        looks_like_edit(text) or looks_like_fill_data(text) or detect_transform(text)
    ):
        await message.answer(
            "Привет! Могу разобрать документ, заполнить бланк или создать Word-файл.\n"
            "Нажмите «Документы» или пришлите файл сюда.",
            reply_markup=ui.docs_inline(),
        )
        return

    # Уже есть план правок (кнопки Применить/Отмена) — подтверждение или уточнение.
    if session.pending is not None or session.flow == "awaiting_confirm":
        session.card_mode = False
        session.pending_clarify = None
        low = text.lower()
        if low in {"отмена", "cancel", "отменить"}:
            session.clear_pending_flow()
            await message.answer("Правки отменены.")
            return
        transform_op = detect_transform(text)
        if transform_op:
            if not await _require_files(message, session):
                return
            await _handle_edit(message, session, text)
            return
        if session.pending is not None and looks_like_apply_pending(text):
            dest, note = await _materialize_pending(session)
            if dest is None:
                await message.answer(note or "Черновик правок уже неактуален.")
                return
            await message.answer(_fit(note))
            await message.answer_document(FSInputFile(dest), caption=dest.name)
            return
        if session.pending is None:
            session.flow = "idle"
        else:
            if not await _require_files(message, session):
                return
            await _handle_edit(message, session, text)
            return

    has_files = bool(session.index.files)

    # После «что не заполнено» короткое «Богородицк» = заполнение поля, не поиск
    if (
        has_files
        and session.awaiting_gap_fill
        and looks_like_short_gap_value(text)
        and not detect_transform(text)
    ):
        session.card_mode = False
        instruction = session.gap_fill_instruction(text)
        await _handle_edit(message, session, instruction, mode="fill")
        return

    # «Вставь их туда» при уже накопленных фактах / после gaps
    if (
        has_files
        and (
            looks_like_fill_data(text)
            or (
                session.doc_task
                and session.doc_task.facts
                and any(k in text.lower() for k in ("вставь", "добав", "подставь", "заполни"))
            )
        )
        and not detect_transform(text)
    ):
        # ниже обычный edit/fill роутинг подхватит; форсируем fill
        pass

    decision = classify_document_intent(text, has_files=has_files)
    decision = apply_document_routing_guards(decision, text=text, has_files=has_files)
    intent = str(decision.get("intent") or "none")
    edit_mode = str(decision.get("mode") or "")
    transform_op = detect_transform(text)
    fillish = (
        looks_like_edit(text)
        or looks_like_fill_data(text)
        or looks_like_reverse_words(text)
        or bool(transform_op)
    )

    # При файлах никогда не показываем clarify «бланк/текст» — это частый косяк
    if has_files and intent == "clarify" and str(decision.get("family") or "") == "write":
        intent = "ask"
        decision = {**decision, "intent": "ask", "family": "ask", "guard": "handler_block_clarify"}
    # Продолжение заполнения, если в сессии уже копили данные
    if (
        has_files
        and session.doc_task
        and session.doc_task.facts
        and any(
            k in text.lower()
            for k in (
                "добав",
                "эти данн",
                "вставь",
                "встав ",
                "заполни",
                "фио",
                "подставь",
                "туда",
            )
        )
        and not looks_like_reverse_words(text)
        and not detect_transform(text)
    ):
        fillish = True
        intent = "edit"
        edit_mode = "fill"

    if (
        looks_like_tone(text)
        and has_files
        and not looks_like_reverse_words(text)
        and not detect_transform(text)
    ):
        intent = "edit"
        edit_mode = "tone"

    if looks_like_reverse_words(text) and has_files:
        intent = "edit"
        edit_mode = "edit"
        fillish = True

    if transform_op and has_files:
        intent = "edit"
        edit_mode = "edit"
        fillish = True

    # Документная задача сильнее залипшего card_mode
    if intent in {
        "edit",
        "ask",
        "check",
        "risks",
        "extract",
        "compare",
        "format",
        "write_form",
        "write_text",
        "clarify",
    } or (has_files and fillish):
        session.card_mode = False

        if intent == "check":
            if not await _require_files(message, session):
                return
            await _handle_gaps(message, session)
            return

        if intent == "risks":
            if not await _require_files(message, session):
                return
            await _handle_risks(message, session)
            return

        if intent == "extract":
            if not await _require_files(message, session):
                return
            await _handle_extract(message, session)
            return

        if intent == "compare":
            await _handle_compare(message, session)
            return

        if intent == "format":
            await _handle_format_sample(message, session)
            return

        if intent == "clarify" and not (has_files and fillish):
            options = list(decision.get("options") or [])
            question = str(decision.get("question") or "Уточните, что сделать?")
            session.pending_clarify = PendingClarify(
                prompt=text,
                options=options,
                question=question,
            )
            session.set_clarifying()
            await message.answer(
                ui.clarify_prompt_text(escape(question)),
                parse_mode="HTML",
                reply_markup=ui.clarify_intent_keyboard(options),
            )
            return

        if intent in {"write_form", "write_text"} and not (has_files and fillish):
            mode = "form" if intent == "write_form" else "text"
            charge = await _require_quota(message, "write")
            if charge is None:
                return
            session.remember_doc_task("write", text)
            await _handle_write(message, session, text, charge, mode=mode)
            return

        if intent == "edit" or fillish:
            if not await _require_files(message, session):
                return
            mode = "auto"
            if transform_op or looks_like_reverse_words(text):
                mode = "auto"
            elif edit_mode == "fill" or looks_like_fill_data(text):
                mode = "fill"
            elif edit_mode == "tone" or looks_like_tone(text):
                mode = "tone"
            await _handle_edit(message, session, text, mode=mode)
            return

        # ask / вопрос по файлу
        if not await _require_files(message, session):
            return
        session.remember_doc_task("ask", text)
        await _handle_question(message, session, text)
        return

    # Карточка: кнопка «Карточка» или явный товарный запрос
    if session.card_mode or intent == "card" or looks_like_card(text):
        if len(text) < 12:
            await message.answer(
                "Для карточки нужно описание товара (хотя бы пару слов) "
                "или фото/видео.\n"
                "Пример: «Нужно продать керамическую кружку 300 мл»."
            )
            return
        charge = await _require_quota(message, "card")
        if charge is None:
            return
        await _make_card(message, session, text, None, charge)
        return

    if intent == "edit" or looks_like_edit(text):
        if not await _require_files(message, session):
            return
        await _handle_edit(message, session, text)
        return

    if not await _require_files(message, session):
        return
    await _handle_question(message, session, text)


@router.callback_query(F.data.startswith("docs:go:"))
async def on_docs_clarify(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data or query.message is None:
        await query.answer()
        return
    session = get_session(query.from_user.id)
    action = query.data.split(":", 2)[-1]
    await query.answer()

    if action == "cancel":
        session.pending_clarify = None
        session.flow = "idle"
        try:
            await query.message.edit_text("Ок, отменил.")
        except Exception:
            await query.message.answer("Ок, отменил.")
        return

    if action == "need_file":
        session.pending_clarify = None
        session.flow = "idle"
        await query.message.answer(
            "Пришлите .docx в чат, затем повторите правку.\n"
            "Пример: «Замени Иванова на Петрова».",
            reply_markup=ui.docs_inline(),
        )
        return

    pending = session.pending_clarify
    if pending is None or not pending.prompt:
        await query.message.answer(
            "Запрос устарел. Напишите задачу ещё раз.",
            reply_markup=ui.docs_inline(),
        )
        return

    prompt = pending.prompt
    session.pending_clarify = None
    session.flow = "idle"

    if action in {"write_form", "write_text"}:
        mode = "form" if action == "write_form" else "text"
        # quota через fake Message-like — используем query.message
        user = query.from_user
        ensure_user(user.id, user.language_code)
        charge = consume(user.id, "write", user.language_code)
        if not charge.ok:
            await query.message.answer(
                charge.message or "Лимит исчерпан. /pay",
                reply_markup=upsell_keyboard(user.id, user.language_code),
            )
            return
        try:
            await query.message.edit_text(
                "Принял: "
                + ("бланк / титульный лист" if mode == "form" else "связный текст")
            )
        except Exception:
            pass
        await _handle_write(query.message, session, prompt, charge, mode=mode)
        return

    await query.message.answer("Не понял выбор. Напишите задачу ещё раз.")


async def _handle_write(
    message: Message,
    session: UserSession,
    prompt: str,
    charge: ConsumeResult,
    mode: str = "auto",
) -> None:
    mode = (mode or "auto").strip().lower()
    status = await message.answer(ui.status_doc_write(mode if mode in {"form", "text"} else ""))
    dest_dir = session.user_dir(DATA_DIR)
    data: dict[str, Any] = {}
    dest: Path | None = None
    try:
        data = await asyncio.to_thread(generate_document, prompt, mode)
        used_mode = str(data.get("mode") or mode or "text")
        fname = suggest_filename(
            str(data.get("filename_stem") or data.get("title") or "document"),
            str(data.get("doc_type") or "document"),
        )
        dest = dest_dir / fname
        n = 1
        while dest.exists():
            dest = dest_dir / f"{Path(fname).stem}_{n}.docx"
            n += 1
        await asyncio.to_thread(
            write_structured_docx,
            dest,
            title=str(data.get("title") or "Документ"),
            sections=list(data.get("sections") or []),
            doc_type=str(data.get("doc_type") or ""),
            layout="form" if used_mode == "form" else "text",
        )
        async with session.lock:
            await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        _refund(message, charge, "write")
        await status.edit_text(_fit(f"Не удалось подготовить документ: {exc}"))
        return
    try:
        await status.delete()
    except Exception:
        pass
    assert dest is not None
    title = str(data.get("title") or dest.name)
    dtype = str(data.get("doc_type") or "документ")
    used_mode = str(data.get("mode") or mode or "text")
    if used_mode == "form":
        tip = (
            "Это шаблон бланка (.docx) — откройте в Word.\n"
            "Поля в квадратных скобках заполните сами. "
            "Не официальный бланк ФНС, типовой макет для удобства."
        )
    else:
        tip = (
            "Это Word (.docx) — откройте в Word / LibreOffice / Google Документах.\n"
            "Текст сгенерирован заново. Учебные работы лучше проверить антиплагиатом вуза."
        )
    await message.answer_document(
        FSInputFile(dest),
        caption=_fit(f"{title}\nТип: {dtype}\n{tip}"),
    )


async def _make_card(
    message: Message,
    session: UserSession,
    notes: str,
    photo_path: Path | None,
    charge: ConsumeResult,
) -> None:
    has_photo = bool(photo_path and photo_path.exists())
    if not has_photo and len((notes or "").strip()) < 12:
        _refund(message, charge, "card")
        await message.answer(
            "Недостаточно данных для карточки. Пришлите фото/видео товара "
            "или более подробное описание."
        )
        return
    status = await message.answer(ui.status_card_copy())
    image_bytes = None
    source_photo = photo_path if has_photo else None
    if has_photo and photo_path is not None:
        image_bytes = photo_path.read_bytes()
        if len(image_bytes) > MAX_FILE_BYTES:
            _refund(message, charge, "card")
            await status.edit_text("Фото больше 20 МБ — Telegram не отдаст его боту.")
            return
    paths: list[Path] = []
    data: dict[str, Any] = {}
    dest_dir = session.user_dir(DATA_DIR)
    variant = peek_variant(dest_dir)
    style_title = VARIANT_TITLES.get(variant, variant)
    try:
        data = await asyncio.to_thread(make_product_card, notes or "", image_bytes)
        scene = scene_for_product(
            str(data.get("label") or ""),
            str(data.get("title") or ""),
            str(data.get("scene") or ""),
        )
        await status.edit_text(
            ui.status_card_photo_lifestyle()
            if variant == "lifestyle"
            else ui.status_card_photo()
        )
        try:
            gen_bytes = await asyncio.to_thread(
                generate_product_photo,
                notes or str(data.get("title") or "product"),
                str(data.get("title") or ""),
                str(data.get("label") or ""),
                str(data.get("image_prompt") or ""),
                image_bytes,
                variant if variant in VARIANTS else "catalog",
                scene,
            )
            raw_studio = dest_dir / "product_studio_raw.png"
            raw_studio.write_bytes(gen_bytes)
            studio_path = dest_dir / "product_studio.png"
            photo_path = await asyncio.to_thread(
                prepare_studio_product,
                raw_studio,
                studio_path,
                force_rembg=False,
                skip_rembg=(variant == "lifestyle"),
            )
        except Exception as gen_exc:
            if source_photo is not None and source_photo.exists():
                log.warning("Studio gen failed (%s), fallback rembg cutout", gen_exc)
                await status.edit_text("Студия недоступна — вырезаю товар с вашего фото…")
                fallback = dest_dir / "product_studio.png"
                photo_path = await asyncio.to_thread(
                    prepare_fallback_cutout, source_photo, fallback
                )
                # lifestyle без сцены → каталожный layout
                if variant == "lifestyle":
                    variant = "catalog"
                    style_title = VARIANT_TITLES.get(variant, variant)
            else:
                raise
        await status.edit_text(ui.status_card_layout())
        if photo_path is None or not Path(photo_path).exists():
            raise RuntimeError("Нет готового фото товара для слайдов")
        stem = "card"
        n = 1
        while (dest_dir / f"{stem}_hero.png").exists():
            stem = f"card_{n}"
            n += 1
        paths = await asyncio.to_thread(
            render_product_card_pack, dest_dir, data, photo_path, stem, variant
        )
        if not paths:
            raise RuntimeError("Рендер не вернул слайды")
    except Exception as exc:
        _refund(message, charge, "card")
        await status.edit_text(_fit(f"Не удалось собрать карточку: {exc}"))
        return
    bump_card_count(dest_dir)
    session.card_mode = False
    try:
        await status.delete()
    except Exception:
        pass
    title = str(data.get("title") or "Карточка товара")
    media = [
        InputMediaPhoto(
            media=FSInputFile(paths[0]),
            caption=_fit(
                f"{title}\nСтиль: «{style_title}»\n"
                f"1/3 обложка · 2/3 детали · 3/3 выгоды",
                900,
            ),
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
    next_style = VARIANT_TITLES.get(peek_variant(dest_dir), "")
    await message.answer(
        f"Готово: 3 слайда (стиль «{style_title}»).\n"
        f"Следующая карточка будет в стиле «{next_style}».\n"
        "Можно сделать ещё или вернуться в меню.",
        reply_markup=ui.card_inline(),
    )


async def _handle_question(message: Message, session: UserSession, text: str) -> None:
    # Если это правка / transform — не отвечаем текстом, а правим файл
    if detect_transform(text) or looks_like_reverse_words(text) or looks_like_edit(text) or looks_like_fill_data(text):
        mode = "fill" if looks_like_fill_data(text) and not detect_transform(text) else "auto"
        await _handle_edit(message, session, text, mode=mode)
        return
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer("Ищу в документах…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
        if not hits:
            _refund(message, charge, "ask")
            await status.edit_text("По этому запросу в документах ничего нет.")
            return
        data = await asyncio.to_thread(ask_document, text, hits)
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    answer = str(data.get("answer") or "").strip() or "Пустой ответ модели."
    answer += _format_citations(data.get("citations") or [])
    await _finish_status(status, message, answer)


async def _handle_gaps(message: Message, session: UserSession) -> None:
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer("Смотрю, какие поля ещё пустые…")
    try:
        async with session.lock:
            chunks = session.index.preview_chunks(20)
        if not chunks:
            _refund(message, charge, "ask")
            await status.edit_text("В загруженных файлах нет текста.")
            return
        text = await asyncio.to_thread(check_document_gaps, chunks)
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Не удалось проверить поля: {exc}"))
        return
    session.remember_gaps(text)
    await _finish_status(status, message, text)


async def _handle_risks(message: Message, session: UserSession) -> None:
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer("Разбираю договор на риски…")
    try:
        async with session.lock:
            chunks = _chunks_for_active(session, limit=24)
        if not chunks:
            _refund(message, charge, "ask")
            await status.edit_text("В файле нет текста.")
            return
        text = await asyncio.to_thread(analyze_contract_risks, chunks)
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Не удалось разобрать договор: {exc}"))
        return
    session.remember_doc_task("ask", "риски договора")
    await _finish_status(status, message, text)


async def _handle_extract(message: Message, session: UserSession) -> None:
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer("Собираю стороны, даты и суммы…")
    try:
        async with session.lock:
            chunks = _chunks_for_active(session, limit=24)
        if not chunks:
            _refund(message, charge, "ask")
            await status.edit_text("В файле нет текста.")
            return
        text = await asyncio.to_thread(extract_key_facts, chunks)
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Не удалось извлечь факты: {exc}"))
        return
    session.remember_doc_task("ask", "факты")
    await _finish_status(status, message, text)


def _chunks_for_active(session: UserSession, limit: int = 20) -> list:
    if session.active_path is not None:
        for item in session.index.files:
            if item.path == session.active_path and item.chunks:
                step = max(1, len(item.chunks) // limit) if len(item.chunks) > limit else 1
                return item.chunks[::step][:limit]
    return session.index.preview_chunks(limit)


def _pick_two_files(session: UserSession) -> tuple | None:
    files = list(session.index.files)
    if len(files) < 2:
        return None
    if session.active_path is not None:
        a = next((f for f in files if f.path == session.active_path), None)
        b = next((f for f in reversed(files) if a is None or f.path != a.path), None)
        if a and b:
            return a, b
    return files[-2], files[-1]


async def _handle_compare(message: Message, session: UserSession) -> None:
    pair = _pick_two_files(session)
    if pair is None:
        await message.answer(
            "Для сравнения нужно минимум <b>2 файла</b> в чате.\n"
            "Пришлите второй документ, затем напишите «Сравни эти два файла».",
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    file_a, file_b = pair
    charge = await _require_quota(message, "ask")
    if charge is None:
        return
    status = await message.answer(
        f"Сравниваю:\n• {file_a.name}\n• {file_b.name}"
    )
    try:
        chunks_a = file_a.chunks[:: max(1, len(file_a.chunks) // 18)][:18] if file_a.chunks else []
        chunks_b = file_b.chunks[:: max(1, len(file_b.chunks) // 18)][:18] if file_b.chunks else []
        if not chunks_a or not chunks_b:
            _refund(message, charge, "ask")
            await status.edit_text("В одном из файлов нет текста.")
            return
        text = await asyncio.to_thread(
            compare_documents,
            chunks_a,
            chunks_b,
            file_a.name,
            file_b.name,
        )
    except Exception as exc:
        _refund(message, charge, "ask")
        await status.edit_text(_fit(f"Не удалось сравнить: {exc}"))
        return
    session.remember_doc_task("ask", "сравнение")
    await _finish_status(status, message, text)


async def _handle_format_sample(message: Message, session: UserSession) -> None:
    pair = _pick_two_files(session)
    if pair is None:
        await message.answer(
            "Нужны <b>2 файла</b>: содержание и образец оформления.\n"
            "Пришлите оба, затем: «Оформи по образцу».",
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    content_f, sample_f = pair
    # Активный = содержание, второй = образец (если active задан)
    if session.active_path is not None:
        for item in session.index.files:
            if item.path == session.active_path:
                content_f = item
                break
        sample_f = next(
            (f for f in session.index.files if f.path != content_f.path),
            sample_f,
        )
    charge = await _require_quota(message, "write")
    if charge is None:
        return
    status = await message.answer(
        f"Оформляю «{content_f.name}» по образцу «{sample_f.name}»…"
    )
    dest: Path | None = None
    data: dict[str, Any] = {}
    try:
        c_chunks = content_f.chunks[:: max(1, len(content_f.chunks) // 16)][:16]
        s_chunks = sample_f.chunks[:: max(1, len(sample_f.chunks) // 12)][:12]
        if not c_chunks:
            _refund(message, charge, "write")
            await status.edit_text("В файле содержания нет текста.")
            return
        data = await asyncio.to_thread(format_by_sample, c_chunks, s_chunks or c_chunks)
        dest_dir = session.user_dir(DATA_DIR)
        fname = suggest_filename(
            str(data.get("filename_stem") or data.get("title") or "document"),
            str(data.get("doc_type") or "document"),
        )
        dest = dest_dir / fname
        n = 1
        while dest.exists():
            dest = dest_dir / f"{Path(fname).stem}_{n}.docx"
            n += 1
        await asyncio.to_thread(
            write_structured_docx,
            dest,
            title=str(data.get("title") or "Документ"),
            sections=list(data.get("sections") or []),
            doc_type=str(data.get("doc_type") or ""),
            layout="text",
        )
        async with session.lock:
            await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        _refund(message, charge, "write")
        await status.edit_text(_fit(f"Не удалось оформить: {exc}"))
        return
    try:
        await status.delete()
    except Exception:
        pass
    assert dest is not None
    session.remember_doc_task("write", "оформление по образцу")
    await message.answer_document(
        FSInputFile(dest),
        caption=_fit(
            f"{data.get('title') or dest.name}\n"
            "Оформлено по образцу (.docx). Откройте в Word."
        ),
    )


async def _handle_edit(
    message: Message,
    session: UserSession,
    text: str,
    mode: str = "auto",
) -> None:
    active = session.active_path
    if active is None:
        await message.answer(
            "Нет активного файла. Отправьте документ или выберите его через /use."
        )
        return
    if active.suffix.lower() != ".docx":
        await message.answer(
            "Править с сохранением можно только .docx.\n"
            "Задайте вопрос по тексту или пришлите Word-файл."
        )
        return

    transform_op = detect_transform(text)
    if transform_op:
        charge = await _require_quota(message, "edit")
        if charge is None:
            return
        labels = {
            "reverse_words": "Переворачиваю слова в файле…",
            "upper_case": "Делаю текст заглавными…",
            "lower_case": "Делаю текст строчными…",
        }
        status = await message.answer(labels.get(transform_op, "Применяю преобразование…"))
        dest = _unique_edited_path(session.user_dir(DATA_DIR), active)
        try:
            result = await asyncio.to_thread(apply_transform, transform_op, active, dest)
            async with session.lock:
                session.pending = None
                await asyncio.to_thread(_ingest_path, session, dest)
            session.finish_op(transform_op)
        except Exception as exc:
            _refund(message, charge, "edit")
            await status.edit_text(_fit(f"Не удалось применить преобразование: {exc}"))
            return
        session.remember_doc_task("edit", text)
        try:
            await status.delete()
        except Exception:
            pass
        changed = int(result.get("changed") or 0)
        await message.answer(f"Готово: изменено абзацев — {changed}.")
        await message.answer_document(FSInputFile(dest), caption=dest.name)
        return

    fill_mode = (mode == "fill" or looks_like_fill_data(text)) and not detect_transform(text)
    tone_mode = mode == "tone" or looks_like_tone(text)
    if fill_mode:
        session.awaiting_gap_fill = False
        session.remember_doc_task("fill", text, accumulate_facts=True)
        # Если пришла готовая инструкция из gap_fill_instruction — не дублируем обёртку
        if "Подставь в пустые поля бланка" in text:
            instruction = text
        else:
            instruction = session.fill_instruction(text)
        status_msg = "Готовлю заполнение бланка…"
        edit_mode = "fill"
    elif tone_mode:
        session.remember_doc_task("edit", text)
        instruction = (
            f"{text}\n\n"
            "Перепиши/подправь текст документа в нужном тоне, "
            "сохраняя факты, даты, суммы и ФИО."
        )
        status_msg = "Готовлю смену тона / сокращение…"
        edit_mode = "tone"
    else:
        session.remember_doc_task("edit", text)
        instruction = text
        status_msg = "Готовлю план правок…"
        edit_mode = "edit"

    charge = await _require_quota(message, "edit")
    if charge is None:
        return
    status = await message.answer(status_msg)
    try:
        async with session.lock:
            # Для заполнения лучше широкий контекст бланка, не только поиск по ФИО
            if fill_mode or tone_mode:
                hits = [Hit(chunk=c, score=1.0) for c in session.index.preview_chunks(16)]
                more = await asyncio.to_thread(session.index.search, instruction)
                seen = {h.chunk.text[:80] for h in hits}
                for h in more:
                    key = h.chunk.text[:80]
                    if key not in seen:
                        hits.append(h)
                        seen.add(key)
            else:
                hits = await asyncio.to_thread(session.index.search, instruction)
                if not hits:
                    hits = [
                        Hit(chunk=c, score=1.0)
                        for c in session.index.preview_chunks(12)
                    ]
        if not hits:
            _refund(message, charge, "edit")
            await status.edit_text("В файле нет текста для правок.")
            return
        data = await asyncio.to_thread(
            plan_edits,
            instruction,
            hits,
            active.name,
            True,
            edit_mode,
        )
    except Exception as exc:
        _refund(message, charge, "edit")
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    kind = data.get("kind") or "none"
    patches = list(data.get("patches") or [])
    if kind == "patch":
        patches = filter_valid_patches(active, patches)
        data["patches"] = patches
        if not patches:
            kind = "none"
            data["summary"] = (
                str(data.get("summary") or "")
                + " Точные фрагменты для безопасной замены в файле не нашлись — "
                "ничего не меняю, чтобы не испортить документ."
            ).strip()
    if kind == "none" or (
        kind == "patch" and not patches
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
        patches=patches,
        rewrite_text=str(data.get("rewrite_text") or ""),
        summary=str(data.get("summary") or ""),
    )
    async with session.lock:
        session.pending = pending
        session.set_awaiting_confirm(edit_mode if edit_mode != "edit" else str(kind))
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
