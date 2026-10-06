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
from bot.session import (
    BusinessDraft,
    PendingClarify,
    PendingEdit,
    UserSession,
    clear_user_workspace,
    get_session,
)
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
from docs.business_types import (
    KIND_LABELS_RU,
    apply_defaults,
    detect_business_kind,
    is_vague_user_reply,
    kind_from_short_hint,
    listed_business_kinds,
    missing_fields,
    missing_question,
    wants_waive_bank,
)
from docs.official_forms import (
    catalog_summary_ru,
    download_official_file,
    format_form_caption,
    get_form,
    match_official_forms,
)
from docs.ip_npd_guide import diy_text, first_step, step_by_id
from edit.business_docx import render_business_docx, suggest_business_filename
from edit.docx_patch import (
    apply_patches,
    filter_valid_patches,
    suggest_filename,
    write_rewrite_docx,
    write_structured_docx,
)
from edit.transforms import apply_transform, detect_transform
from llm.business_extract import extract_business_fields, merge_extracted_fields
from llm.client import (
    analyze_contract_risks,
    ask_document,
    check_document_gaps,
    classify_document_intent,
    compare_documents,
    extract_key_facts,
    format_by_sample,
    generate_document,
    generate_product_photo,
    has_strong_create,
    looks_like_apply_pending,
    looks_like_card,
    looks_like_chitchat,
    looks_like_edit,
    looks_like_fill_data,
    looks_like_reverse_words,
    looks_like_tone,
    make_product_card,
    parse_document_volume,
    plan_edits,
    refine_document_intent_llm,
    summarize_document,
)
from bot.session_store import save_dialog_session
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


class _PersistSessionMiddleware(BaseMiddleware):
    """Пишет dialog session в SQLite после каждого апдейта."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            user = None
            if isinstance(event, Message):
                user = event.from_user
            elif isinstance(event, CallbackQuery):
                user = event.from_user
            if user is not None:
                try:
                    save_dialog_session(get_session(user.id))
                except Exception:
                    log.exception("Failed to persist dialog session for %s", user.id)


router.message.middleware(_TrackChatMessagesMiddleware())
router.callback_query.middleware(_TrackChatMessagesMiddleware())
router.message.middleware(_PersistSessionMiddleware())
router.callback_query.middleware(_PersistSessionMiddleware())


def _unique_positive_ids(known_ids: Iterable[int]) -> list[int]:
    return sorted({int(x) for x in known_ids if int(x) > 0})


async def _delete_chat_messages(
    bot,
    chat_id: int,
    *,
    known_ids: Iterable[int] = (),
) -> int:
    """Удаляет только известные message_id (без перебора 1..N)."""
    ids = _unique_positive_ids(known_ids)
    if not ids:
        return 0

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
    """Короткая человекочитаемая подпись к источникам (не технический лог)."""
    if not isinstance(citations, list):
        return ""
    files: list[str] = []
    seen: set[str] = set()
    for item in citations:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "").strip()
        if not file_name or file_name in seen:
            continue
        seen.add(file_name)
        # Укорачиваем длинные имена
        short = file_name if len(file_name) <= 42 else file_name[:39] + "…"
        files.append(short)
        if len(files) >= 3:
            break
    if not files:
        return ""
    if len(files) == 1:
        return f"\n\nПо файлу: {files[0]}"
    return "\n\nПо файлам: " + "; ".join(files)


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
            session.pending = None
            await asyncio.to_thread(_ingest_path, session, dest)
            session.flow = "idle"
            session.remember_op(op)
            return dest, note
    except Exception as exc:
        # Черновик оставляем — пользователь может нажать «Применить» снова
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
    await _soft_landing(
        message,
        session,
        hint="Сначала нужен документ в чате.",
        remember_prompt=(message.text or "").strip(),
    )
    return False


async def _soft_landing(
    message: Message,
    session: UserSession,
    *,
    hint: str = "",
    remember_prompt: str = "",
) -> None:
    """Мягкий выход без тупика: объяснение + кнопки выбора."""
    has_files = bool(session.index.files)
    prompt = (remember_prompt or message.text or "").strip()
    if prompt:
        session.pending_clarify = PendingClarify(
            prompt=prompt,
            options=[],
            question=hint or "Уточните задачу",
        )
        session.set_clarifying()
    await message.answer(
        ui.soft_landing_text(has_files=has_files, hint=hint),
        reply_markup=ui.soft_landing_keyboard(has_files=has_files),
    )


def _interpretation_for_edit(text: str, mode: str, transform_op: str | None = None) -> str:
    if transform_op == "reverse_words":
        return "Сейчас переверну слова в файле и пришлю готовый Word"
    if transform_op == "upper_case":
        return "Сейчас сделаю текст заглавными и пришлю файл"
    if transform_op == "lower_case":
        return "Сейчас сделаю текст строчными и пришлю файл"
    if mode == "fill":
        return "Сейчас заполню поля в бланке по вашим данным"
    if mode == "tone":
        return "Сейчас подправлю тон текста в файле"
    return "Сейчас подготовлю правку файла"


async def _require_quota(
    message: Message,
    action: Action,
    *,
    user: User | None = None,
) -> ConsumeResult | None:
    """Списать квоту. user — кто кликнул (для callback нельзя брать message.from_user = бот)."""
    actor = user or message.from_user
    if actor is None:
        return None
    lang = actor.language_code
    ensure_user(actor.id, lang)
    result = consume(actor.id, action, lang)
    if result.ok:
        return result
    await message.answer(
        result.message or "Лимит на сегодня исчерпан. Можно продолжить через /pay",
        reply_markup=upsell_keyboard(actor.id, lang),
    )
    return None


def _refund(
    message: Message,
    charge: ConsumeResult | None,
    action: Action,
    *,
    user: User | None = None,
) -> None:
    actor = user or message.from_user
    if actor is None or charge is None:
        return
    refund_consume(actor.id, charge, action)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    session = _session_of(message.from_user)
    if session is not None:
        session.clear_dialog_modes()
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
    session = _session_of(message.from_user)
    if session is not None:
        session.clear_dialog_modes()
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
    session.clear_dialog_modes(keep_card_mode=True)
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
        session.clear_dialog_modes(keep_pending=True)
        await cmd_help(message)
        return
    if text == ui.BTN_CLEAR:
        await cmd_clear(message)
        return
    if text == ui.BTN_CARD:
        session.clear_dialog_modes(keep_card_mode=True)
        session.card_mode = True
        await message.answer(
            ui.card_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if text == ui.BTN_DOCS:
        session.clear_dialog_modes()
        await message.answer(
            ui.docs_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if text == ui.BTN_IP_NPD:
        session.clear_dialog_modes()
        await _send_ip_npd_step(message, "why")
        return
    if text == ui.BTN_BALANCE:
        from billing.handlers import cmd_balance

        session.clear_dialog_modes(keep_pending=True)
        await cmd_balance(message)
        return
    if text == ui.BTN_PLANS:
        from billing.handlers import cmd_plans

        session.clear_dialog_modes(keep_pending=True)
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
        session.clear_dialog_modes()
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
        known = list(session.chat_message_ids)
        known.append(query.message.message_id)
        try:
            await query.message.edit_text("Очищаю чат…")
        except Exception:
            pass
        clear_user_workspace(query.from_user.id, DATA_DIR)
        await _delete_chat_messages(
            query.bot,
            chat_id,
            known_ids=known,
        )
        sent = await query.bot.send_message(
            chat_id,
            ui.welcome_text(query.from_user.first_name),
            parse_mode="HTML",
            reply_markup=ui.main_reply_keyboard(),
        )
        _remember_bot_message(query.from_user.id, sent)
        try:
            save_dialog_session(get_session(query.from_user.id))
        except Exception:
            pass
        return
    if action == "help":
        session.clear_dialog_modes(keep_pending=True)
        await query.message.answer(
            ui.help_text(),
            parse_mode="HTML",
            reply_markup=ui.back_home_inline(),
        )
        return
    if action == "ip_npd":
        session.clear_dialog_modes()
        await _send_ip_npd_step(query.message, "why")
        return
    if action == "card":
        session.clear_dialog_modes(keep_card_mode=True)
        session.card_mode = True
        await query.message.answer(
            ui.card_prompt_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if action == "card_example":
        session.clear_dialog_modes(keep_card_mode=True)
        session.card_mode = True
        await query.message.answer(
            ui.card_example_text(),
            parse_mode="HTML",
            reply_markup=ui.card_inline(),
        )
        return
    if action == "docs_form_ex":
        session.clear_dialog_modes()
        await query.message.answer(
            ui.docs_form_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_text_ex":
        session.clear_dialog_modes()
        await query.message.answer(
            ui.docs_text_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_fill_ex":
        session.clear_dialog_modes()
        await query.message.answer(
            ui.docs_fill_example_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs_more_ex":
        session.clear_dialog_modes()
        await query.message.answer(
            ui.docs_more_examples_text(),
            parse_mode="HTML",
            reply_markup=ui.docs_inline(),
        )
        return
    if action == "docs":
        session.clear_dialog_modes()
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
        await _handle_gaps(query.message, session, user=query.from_user)
        return
    if action == "risks":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите договор.",
                reply_markup=ui.docs_inline(),
            )
            return
        await _handle_risks(query.message, session, user=query.from_user)
        return
    if action == "extract":
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите документ.",
                reply_markup=ui.docs_inline(),
            )
            return
        await _handle_extract(query.message, session, user=query.from_user)
        return
    if action == "compare":
        await _handle_compare(query.message, session, user=query.from_user)
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
            await status.edit_text(
                charge.message or "Лимит на сегодня исчерпан. Можно продолжить через /pay"
            )
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

        session.clear_dialog_modes(keep_pending=True)
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

        session.clear_dialog_modes(keep_pending=True)
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
    # Документ в чате — выходим из режима карточки и залипших сценариев.
    session.clear_dialog_modes(keep_pending=True)
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

    low = text.lower()

    # Ждём тему после кнопки «Создать документ»
    if session.flow == "awaiting_write":
        if low in {"отмена", "cancel", "отменить", "стоп"}:
            session.clear_dialog_modes()
            await message.answer(
                "Ок, создание документа отменил.",
                reply_markup=ui.docs_inline(),
            )
            return
        if looks_like_chitchat(text) and not (
            looks_like_edit(text) or looks_like_fill_data(text) or detect_transform(text)
        ):
            session.clear_dialog_modes()
            await message.answer(
                "Привет! Могу разобрать документ, заполнить бланк или создать Word-файл.\n"
                "Нажмите «Документы» или пришлите файл сюда.",
                reply_markup=ui.docs_inline(),
            )
            return
        peek = classify_document_intent(
            text,
            has_files=bool(session.index.files),
            has_facts=bool(session.doc_task and session.doc_task.facts),
            awaiting_gap_fill=False,
        )
        peek_intent = str(peek.get("intent") or "none")
        if peek_intent in {
            "ask",
            "edit",
            "check",
            "risks",
            "extract",
            "compare",
            "format",
            "card",
            "gap_fill",
        } or (peek_intent == "clarify" and peek.get("family") != "write"):
            session.flow = "idle"
            session.last_op = ""
            session.business_draft = None
        else:
            session.card_mode = False
            session.pending_clarify = None
            session.flow = "idle"
            if session.business_draft and session.business_draft.kind in KIND_LABELS_RU:
                kind = session.business_draft.kind
                session.business_draft = None
                await _handle_business_write(message, session, text, kind=kind)
                return
            mode = "form" if session.last_op == "write_form" else "text"
            charge = await _require_quota(message, "write")
            if charge is None:
                return
            session.remember_doc_task("write", text)
            await _handle_write(message, session, text, charge, mode=mode)
            return

    # Уточнение полей делового черновика
    if session.business_draft is not None and session.flow == "clarifying":
        if low in {"отмена", "cancel", "отменить", "стоп"}:
            session.business_draft = None
            session.flow = "idle"
            await message.answer("Ок, создание документа отменил.", reply_markup=ui.docs_inline())
            return
        peek = classify_document_intent(
            text,
            has_files=bool(session.index.files),
            has_facts=bool(session.doc_task and session.doc_task.facts),
            awaiting_gap_fill=False,
        )
        peek_intent = str(peek.get("intent") or "none")
        if peek_intent in {
            "ask",
            "edit",
            "check",
            "risks",
            "extract",
            "compare",
            "format",
            "card",
            "gap_fill",
        }:
            session.business_draft = None
            session.flow = "idle"
        elif looks_like_chitchat(text) and not looks_like_edit(text):
            session.business_draft = None
            session.flow = "idle"
            await message.answer(
                "Ок, создание документа отменил.",
                reply_markup=ui.docs_inline(),
            )
            return
        else:
            await _continue_business_draft(message, session, text)
            return

    # Выбрали «какой документ» кнопкой, но описали текстом
    if (
        session.flow == "clarifying"
        and session.pending_clarify is not None
        and session.business_draft is None
    ):
        opts = list(session.pending_clarify.options or [])
        if any(str(o.get("id") or "").startswith("biz:") for o in opts):
            base = (session.pending_clarify.prompt or "").strip()
            combined = f"{base}\n{text}".strip() if base else text
            kind = detect_business_kind(combined) or kind_from_short_hint(text)
            if kind:
                session.pending_clarify = None
                session.flow = "idle"
                await _handle_business_write(message, session, combined, kind=kind)
                return
            await message.answer(
                "Выберите тип кнопкой или напишите задачу целиком, "
                "например: «Составь договор услуг с ООО Ромашка на 80 000 руб.»",
                reply_markup=ui.business_kind_keyboard(),
            )
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
            has_files_pending = bool(session.index.files)
            decision_pending = classify_document_intent(
                text,
                has_files=has_files_pending,
                has_facts=bool(session.doc_task and session.doc_task.facts),
                awaiting_gap_fill=False,
            )
            intent_pending = str(decision_pending.get("intent") or "none")
            if intent_pending in {"ask", "check", "risks", "extract", "compare"} and has_files_pending:
                if intent_pending == "check":
                    await _handle_gaps(message, session)
                elif intent_pending == "risks":
                    await _handle_risks(message, session)
                elif intent_pending == "extract":
                    await _handle_extract(message, session)
                elif intent_pending == "compare":
                    await _handle_compare(message, session)
                else:
                    session.remember_doc_task("ask", text)
                    await _handle_question(message, session, text)
                return
            if not await _require_files(message, session):
                return
            await _handle_edit(message, session, text)
            return

    has_files = bool(session.index.files)
    has_facts = bool(session.doc_task and session.doc_task.facts)
    decision = classify_document_intent(
        text,
        has_files=has_files,
        has_facts=has_facts,
        awaiting_gap_fill=session.awaiting_gap_fill,
    )
    intent = str(decision.get("intent") or "none")
    edit_mode = str(decision.get("mode") or "")
    transform_op = detect_transform(text)
    conf = float(decision.get("confidence") or 0)

    # Сомнительная эвристика — уточняем у модели (без ломки явных паттернов)
    if intent in {"none", ""} or (conf < 0.5 and intent not in {"gap_fill", "clarify"}):
        try:
            decision = await asyncio.to_thread(
                refine_document_intent_llm,
                text,
                has_files=has_files,
                heuristic=decision,
            )
            intent = str(decision.get("intent") or "none")
            edit_mode = str(decision.get("mode") or "")
            conf = float(decision.get("confidence") or 0)
        except Exception:
            pass

    if intent == "gap_fill":
        session.card_mode = False
        session.pending_clarify = None
        instruction = session.gap_fill_instruction(text)
        await _handle_edit(message, session, instruction, mode="fill")
        return

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
        "business_write",
        "official_form",
        "clarify",
    }:
        session.card_mode = False
        if intent != "clarify":
            session.pending_clarify = None

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

        if intent == "clarify":
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

        if intent == "official_form":
            await _handle_official_form(message, session, text)
            return

        if intent in {"write_form", "write_text"}:
            mode = "form" if intent == "write_form" else "text"
            # Мягкие / неуверенные запросы — короткое «Понял так» перед генерацией
            if (not has_strong_create(text)) or conf < 0.75:
                label = "бланк / форму" if mode == "form" else "документ"
                brief = (text[:120] + "…") if len(text) > 120 else text
                session.pending_clarify = PendingClarify(
                    prompt=text,
                    options=[{"id": intent, "label": "Да, делай"}],
                    question=f"создать {label}",
                )
                session.set_clarifying()
                await message.answer(
                    ui.interpret_confirm_text(f"создам {label}: «{escape(brief)}»")
                    + "\nНажмите «Да, делай» или перефразируйте.",
                    parse_mode="HTML",
                    reply_markup=ui.understand_confirm_keyboard(intent),
                )
                return
            charge = await _require_quota(message, "write")
            if charge is None:
                return
            session.remember_doc_task("write", text)
            await _handle_write(message, session, text, charge, mode=mode)
            return

        if intent == "business_write":
            kind = str(decision.get("doc_kind") or decision.get("mode") or "").strip()
            session.pending_clarify = None
            await _handle_business_write(message, session, text, kind=kind or None)
            return

        if intent == "edit":
            if not await _require_files(message, session):
                return
            mode = "auto"
            if transform_op or looks_like_reverse_words(text):
                mode = "auto"
            elif edit_mode == "fill" or looks_like_fill_data(text):
                mode = "fill"
            elif edit_mode == "tone":
                mode = "tone"
            await _handle_edit(message, session, text, mode=mode)
            return

        # ask
        if intent == "ask" and has_files:
            if conf < 0.55:
                await _soft_landing(
                    message,
                    session,
                    hint=ui.interpret_confirm_text(
                        f"возможно, вопрос по файлу: «{(text[:80] + '…') if len(text) > 80 else text}»"
                    ),
                    remember_prompt=text,
                )
                return
            if not await _require_files(message, session):
                return
            session.remember_doc_task("ask", text)
            await _handle_question(message, session, text)
            return

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
                "Пример: «Нужно продать керамическую кружку 300 мл».",
                reply_markup=ui.card_inline(),
            )
            return
        charge = await _require_quota(message, "card")
        if charge is None:
            return
        await _make_card(message, session, text, None, charge)
        return

    # Любой непонятный запрос — soft landing, не тупик
    await _soft_landing(
        message,
        session,
        hint="Пока не отнёс фразу ни к документам, ни к карточке.",
        remember_prompt=text,
    )


async def _send_ip_npd_step(message: Message, step_id: str) -> None:
    step = step_by_id(step_id) or first_step()
    await message.answer(
        step.body,
        parse_mode="HTML",
        reply_markup=ui.ip_npd_keyboard(step.id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("ipnpd:"))
async def on_ip_npd_guide(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data or query.message is None:
        await query.answer()
        return
    await query.answer()
    session = get_session(query.from_user.id)
    session.clear_dialog_modes()
    action = query.data.split(":", 1)[-1]
    if action == "diy":
        await query.message.answer(
            diy_text(),
            parse_mode="HTML",
            reply_markup=ui.ip_npd_keyboard("diy"),
            disable_web_page_preview=True,
        )
        return
    if action.startswith("step:"):
        step_id = action.split(":", 1)[-1].strip() or "why"
        await _send_ip_npd_step(query.message, step_id)
        return
    await _send_ip_npd_step(query.message, "why")


@router.callback_query(F.data.startswith("docs:go:"))
async def on_docs_clarify(query: CallbackQuery) -> None:
    if query.from_user is None or not query.data or query.message is None:
        await query.answer()
        return
    session = get_session(query.from_user.id)
    action = query.data.split(":", 2)[-1]
    await query.answer()

    if action == "cancel":
        session.clear_dialog_modes(keep_pending=True)
        try:
            await query.message.edit_text("Ок, отменил.")
        except Exception:
            await query.message.answer("Ок, отменил.")
        return

    if action.startswith("official:"):
        form_id = action.split(":", 1)[-1].strip()
        session.pending_clarify = None
        session.flow = "idle"
        form = get_form(form_id)
        if form is None:
            await query.message.answer(
                "Не нашёл эту форму в каталоге.\n" + catalog_summary_ru(),
                reply_markup=ui.docs_inline(),
            )
            return
        await _send_official_form(query.message, form)
        return

    if action.startswith("biz:"):
        kind = action.split(":", 1)[-1].strip()
        pending = session.pending_clarify
        prompt = (pending.prompt if pending else "") or ""
        session.pending_clarify = None
        session.flow = "idle"
        if kind not in KIND_LABELS_RU:
            await query.message.answer(
                "Выберите тип документа:",
                reply_markup=ui.business_kind_keyboard(),
            )
            return
        if not prompt.strip():
            session.flow = "awaiting_write"
            session.last_op = "business_write"
            session.business_draft = BusinessDraft(kind=kind, base_prompt="")
            await query.message.answer(
                f"Ок, подготовлю {KIND_LABELS_RU[kind].lower()}. "
                "Напишите задачу одним сообщением: стороны, сумма, сроки.",
                reply_markup=ui.docs_inline(),
            )
            return
        await query.message.answer(
            ui.interpret_confirm_text(f"создам {KIND_LABELS_RU[kind].lower()}")
        )
        await _handle_business_write(
            query.message, session, prompt, kind=kind, user=query.from_user
        )
        return

    if action == "need_file":
        session.clear_dialog_modes(keep_pending=True)
        await query.message.answer(
            "Пришлите файл в чат (.docx для правок), затем повторите задачу.\n"
            "Пример: «Замени Иванова на Петрова» или «Что не заполнено?».",
            reply_markup=ui.docs_inline(),
        )
        return

    if action in {"ask", "fill", "edit"}:
        pending = session.pending_clarify
        prompt = (pending.prompt if pending else "") or ""
        session.pending_clarify = None
        session.flow = "idle"
        if not session.index.files:
            await query.message.answer(
                "Сначала пришлите документ.",
                reply_markup=ui.soft_landing_keyboard(has_files=False),
            )
            return
        if action == "ask":
            if not prompt:
                await query.message.answer(
                    "Напишите вопрос по файлу, например: «Что в договоре про срок?»",
                    reply_markup=ui.docs_inline(),
                )
                return
            await query.message.answer("Сейчас посмотрю в файле…")
            await _handle_question(query.message, session, prompt, announce=False, user=query.from_user)
            return
        if action == "fill":
            tip = (
                f"Ок, заполняю по вашей фразе.\n«{prompt}»"
                if prompt
                else "Напишите данные для полей, например: «Богородицк» или «ФИО: …»."
            )
            if prompt:
                await query.message.answer(ui.interpret_confirm_text("заполнить поля бланка"))
                await _handle_edit(
                    query.message, session, prompt, mode="fill", announce=False, user=query.from_user
                )
            else:
                await query.message.answer(tip, reply_markup=ui.docs_inline())
            return
        # edit
        if prompt:
            await query.message.answer(ui.interpret_confirm_text("правка файла"))
            await _handle_edit(
                query.message, session, prompt, mode="auto", announce=False, user=query.from_user
            )
        else:
            await query.message.answer(
                "Напишите правку, например: «Замени Иванова на Петрова».",
                reply_markup=ui.docs_inline(),
            )
        return

    pending = session.pending_clarify
    prompt = (pending.prompt if pending else "") or ""
    # Soft landing кладёт prompt без options; clarify — с кнопками write_form/write_text.
    from_write_clarify = bool(pending and pending.options)
    session.pending_clarify = None
    session.flow = "idle"

    if action in {"write_form", "write_text"}:
        mode = "form" if action == "write_form" else "text"
        # «Создать документ» / пример без темы — ждём следующее сообщение (тогда и счётчик).
        if not prompt.strip() or not from_write_clarify:
            session.flow = "awaiting_write"
            session.last_op = action
            await query.message.answer(
                "Ок, создам документ. Напишите тему или задачу одним сообщением.\n"
                "Пример: «Реферат на тему ИИ в медицине, 3–4 страницы».",
                reply_markup=ui.docs_inline(),
            )
            return
        user = query.from_user
        ensure_user(user.id, user.language_code)
        charge = consume(user.id, "write", user.language_code)
        if not charge.ok:
            await query.message.answer(
                charge.message or "Лимит на сегодня исчерпан. Можно продолжить через /pay",
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
        await _handle_write(
            query.message, session, prompt, charge, mode=mode, user=query.from_user
        )
        return

    await query.message.answer("Не понял выбор. Напишите задачу ещё раз.")


async def _send_official_form(message: Message, form) -> None:
    path: Path | None = None
    try:
        if form.file_url:
            path = await asyncio.to_thread(download_official_file, form.file_url)
        caption = format_form_caption(form, with_file=path is not None)
        if path is not None:
            safe_name = f"{form.id}{path.suffix or '.pdf'}"
            await message.answer_document(
                FSInputFile(path, filename=safe_name),
                caption=_fit(caption),
                parse_mode="HTML",
                reply_markup=ui.docs_inline(),
            )
        else:
            await message.answer(
                caption,
                parse_mode="HTML",
                reply_markup=ui.docs_inline(),
                disable_web_page_preview=False,
            )
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


async def _handle_official_form(
    message: Message,
    session: UserSession,
    prompt: str,
) -> None:
    text = (prompt or "").strip()
    hits = match_official_forms(text, limit=5, min_score=3)
    if not hits:
        await message.answer(
            "Пока не нашёл такую официальную форму в каталоге.\n\n"
            f"{catalog_summary_ru()}\n\n"
            "Напишите код или название, например: «официальный бланк Р21001» "
            "или «декларация УСН с сайта ФНС».\n"
            "Если нужен просто макет Word (не ФНС) — так и напишите: "
            "«сделай титульный лист…».",
            reply_markup=ui.docs_inline(),
        )
        return
    best_form, best_score = hits[0]
    # One clear winner
    if len(hits) == 1 or best_score >= hits[1][1] + 4:
        await message.answer(ui.interpret_confirm_text(f"официальный бланк: {best_form.title}"))
        await _send_official_form(message, best_form)
        return
    # Ambiguous — let user pick
    ids = [f.id for f, _ in hits[:4]]
    session.pending_clarify = PendingClarify(
        prompt=text,
        options=[{"id": f"official:{fid}", "label": get_form(fid).title if get_form(fid) else fid} for fid in ids],
        question="Какой официальный бланк нужен?",
    )
    session.set_clarifying()
    await message.answer(
        "Нашёл несколько похожих бланков ФНС. Выберите:",
        reply_markup=ui.official_forms_keyboard(ids),
    )


async def _handle_business_write(
    message: Message,
    session: UserSession,
    prompt: str,
    *,
    kind: str | None = None,
    user: User | None = None,
) -> None:
    text = (prompt or "").strip()
    doc_kind = (kind or "").strip().lower()
    if doc_kind not in KIND_LABELS_RU:
        session.pending_clarify = PendingClarify(
            prompt=text,
            options=[
                {"id": f"biz:{k}", "label": label}
                for k, label in KIND_LABELS_RU.items()
            ],
            question="Какой документ подготовить?",
        )
        session.set_clarifying()
        await message.answer(
            "Какой документ подготовить?",
            reply_markup=ui.business_kind_keyboard(),
        )
        return

    status = await message.answer("Собираю данные для черновика…")
    try:
        extracted = await asyncio.to_thread(extract_business_fields, text, doc_kind)
    except Exception as exc:
        try:
            await status.edit_text(_fit(f"Не удалось разобрать запрос: {exc}"))
        except Exception:
            await message.answer(_fit(f"Не удалось разобрать запрос: {exc}"))
        return

    if not text.strip():
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(
            "Опишите задачу одним сообщением: кто стороны, сумма, срок, предмет услуги."
        )
        return

    fields = apply_defaults(doc_kind, dict(extracted.get("fields") or {}))
    missing = missing_fields(doc_kind, fields)
    draft = BusinessDraft(
        kind=doc_kind,
        fields=fields,
        missing=missing,
        base_prompt=text,
        bank_asked=False,
        questions_asked=0,
    )
    session.business_draft = draft

    need_ask = _business_fields_to_ask(draft)
    if need_ask:
        draft.missing = need_ask
        draft.questions_asked = 1
        if "bank_details" in need_ask:
            draft.bank_asked = True
        session.set_clarifying()
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(missing_question(need_ask))
        return

    try:
        await status.delete()
    except Exception:
        pass
    await _render_and_send_business(message, session, user=user)


async def _continue_business_draft(
    message: Message, session: UserSession, text: str
) -> None:
    draft = session.business_draft
    if draft is None or draft.kind not in KIND_LABELS_RU:
        session.business_draft = None
        session.flow = "idle"
        await message.answer("Черновик уже неактуален. Напишите задачу заново.")
        return

    if is_vague_user_reply(text):
        need = _business_fields_to_ask(draft)
        if need:
            await message.answer(
                missing_question(need)
                + "\n\nНапишите конкретные данные (название, сумма, срок) "
                "или «отмена», чтобы выйти."
            )
            return

    status = await message.answer("Принял, дособираю данные…")
    if wants_waive_bank(text):
        draft.fields["_waive_bank_details"] = True

    merged_prompt = f"{draft.base_prompt}\nУточнение: {text}".strip()
    extracted = await asyncio.to_thread(
        extract_business_fields, merged_prompt, draft.kind
    )
    draft.fields = merge_extracted_fields(
        draft.fields, dict(extracted.get("fields") or {})
    )
    if wants_waive_bank(text):
        draft.fields["_waive_bank_details"] = True
    # Soft letter signature: after an explicit answer turn, allow placeholder
    if (
        draft.kind == "letter"
        and "sender_sign" in (draft.missing or [])
        and not str(draft.fields.get("sender_sign") or "").strip()
    ):
        draft.fields["sender_sign"] = text.strip()[:120] or "[ФИО / должность]"

    draft.fields = apply_defaults(draft.kind, draft.fields)
    draft.base_prompt = merged_prompt
    draft.missing = missing_fields(draft.kind, draft.fields)
    draft.questions_asked = int(draft.questions_asked or 0) + 1

    need_ask = _business_fields_to_ask(draft)
    try:
        await status.delete()
    except Exception:
        pass
    if need_ask and draft.questions_asked < 3:
        draft.missing = need_ask
        if "bank_details" in need_ask:
            draft.bank_asked = True
        session.set_clarifying()
        await message.answer(missing_question(need_ask))
        return

    await _render_and_send_business(message, session)


def _business_fields_to_ask(draft: BusinessDraft) -> list[str]:
    missing = list(draft.missing or missing_fields(draft.kind, draft.fields))
    # Soft bank: ask at most once
    if "bank_details" in missing and draft.bank_asked:
        draft.fields["_waive_bank_details"] = True
        missing = [m for m in missing if m != "bank_details"]
    # After defaults, claim deadline should be gone; filter empty
    return [m for m in missing if m]


async def _render_and_send_business(
    message: Message,
    session: UserSession,
    *,
    user: User | None = None,
) -> None:
    draft = session.business_draft
    if draft is None or draft.kind not in KIND_LABELS_RU:
        await message.answer("Не удалось собрать документ — напишите задачу заново.")
        return

    charge = await _require_quota(message, "write", user=user)
    if charge is None:
        return

    fields = apply_defaults(draft.kind, draft.fields)
    dest_dir = session.user_dir(DATA_DIR)
    fname = suggest_business_filename(draft.kind, fields)
    dest = dest_dir / fname
    n = 1
    while dest.exists():
        dest = dest_dir / f"{Path(fname).stem}_{n}.docx"
        n += 1

    status = await message.answer(ui.status_doc_write("text"))
    try:
        await asyncio.to_thread(render_business_docx, draft.kind, fields, dest)
        async with session.lock:
            await asyncio.to_thread(_ingest_path, session, dest)
    except Exception as exc:
        _refund(message, charge, "write", user=user)
        session.business_draft = None
        session.flow = "idle"
        try:
            await status.edit_text(_fit(f"Не удалось подготовить документ: {exc}"))
        except Exception:
            await message.answer(_fit(f"Не удалось подготовить документ: {exc}"))
        return

    try:
        await status.delete()
    except Exception:
        pass

    kinds_mentioned = listed_business_kinds(draft.base_prompt)
    caption = (
        f"{KIND_LABELS_RU.get(draft.kind, 'Документ')}\n"
        "Черновик — проверьте реквизиты и суммы перед отправкой."
    )
    if len(kinds_mentioned) >= 2:
        caption += "\nДругие документы из запроса сделайте отдельным сообщением."

    session.business_draft = None
    session.flow = "idle"
    session.remember_op("business_write")
    session.remember_doc_task("write", draft.base_prompt)
    await message.answer_document(FSInputFile(dest), caption=_fit(caption))


async def _handle_write(
    message: Message,
    session: UserSession,
    prompt: str,
    charge: ConsumeResult,
    mode: str = "auto",
    *,
    user: User | None = None,
) -> None:
    mode = (mode or "auto").strip().lower()
    prompt = _merge_write_prompt(session, prompt)
    volume = parse_document_volume(prompt)
    long_job = (volume.get("pages") or 0) >= 4 and mode != "form"
    status = await message.answer(
        "Готовлю развёрнутый документ по разделам — это займёт пару минут…"
        if long_job
        else ui.status_doc_write(mode if mode in {"form", "text"} else "")
    )
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
        _refund(message, charge, "write", user=user)
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
    session.remember_doc_task("write", prompt)
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
        approx = int(data.get("approx_chars") or 0)
        if approx:
            tip += f"\nОриентировочный объём: ~{approx} знаков"
            if approx >= 1500:
                tip += f" (~{max(1, approx // 1800)} стр.)"
        note = str(data.get("volume_note") or "").strip()
        if note:
            tip += f"\n{note}"
        if data.get("want_pdf"):
            tip += (
                "\nPDF пока не отдаю автоматически — скачайте .docx "
                "или «Сохранить как PDF» в Word/Google Документах."
            )
    await message.answer_document(
        FSInputFile(dest),
        caption=_fit(f"{title}\nТип: {dtype}\n{tip}"),
    )


def _merge_write_prompt(session: UserSession, prompt: str) -> str:
    """Если новое сообщение — только объём/формат, подмешиваем прошлую тему."""
    text = (prompt or "").strip()
    if not text:
        return text
    task = session.doc_task
    prev = (task.last_prompt if task and task.kind == "write" else "") or ""
    if not prev or prev.strip().lower() == text.lower():
        return text
    low = text.lower()
    volume_only = bool(
        re.search(r"\d+\s*(?:страниц\w*|стр\.?|знак)", low)
    ) and not any(
        k in low
        for k in (
            "тем",
            "про ",
            "о влиян",
            "на тему",
            "по теме",
            "психолог",
            "договор",
            "претенз",
            "письм",
        )
    )
    short_followup = len(text) < 80 and any(
        k in low for k in ("подлиннее", "длиннее", "больше объём", "побольше", "pdf", "пдф")
    )
    if volume_only or short_followup:
        return f"{prev.strip()}\n\nУточнение объёма/формата: {text}"
    return text


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


async def _handle_question(
    message: Message,
    session: UserSession,
    text: str,
    *,
    announce: bool = True,
    user: User | None = None,
) -> None:
    # Если это правка / transform — не отвечаем текстом, а правим файл
    if detect_transform(text) or looks_like_reverse_words(text) or looks_like_edit(text) or looks_like_fill_data(text):
        mode = "fill" if looks_like_fill_data(text) and not detect_transform(text) else "auto"
        await _handle_edit(message, session, text, mode=mode, announce=announce, user=user)
        return
    charge = await _require_quota(message, "ask", user=user)
    if charge is None:
        return
    if announce:
        await message.answer("Сейчас посмотрю в файле…")
    status = await message.answer("Читаю…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
        if not hits:
            _refund(message, charge, "ask", user=user)
            try:
                await status.delete()
            except Exception:
                pass
            await _soft_landing(
                message,
                session,
                hint="В загруженных файлах по этому запросу ничего не нашёл.",
                remember_prompt=text,
            )
            return
        data = await asyncio.to_thread(ask_document, text, hits)
    except Exception as exc:
        _refund(message, charge, "ask", user=user)
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    answer = str(data.get("answer") or "").strip() or "Пустой ответ модели."
    # Типичный тупик «данных нет» / «это задача на правку» — предлагаем действия
    low = answer.lower()
    if any(
        k in low
        for k in (
            "отсутств",
            "не найден",
            "нет в предоставлен",
            "ничего не",
            "не удалось найти",
            "данных нет",
        )
    ):
        _refund(message, charge, "ask", user=user)
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer(_fit(answer))
        await _soft_landing(
            message,
            session,
            hint="Возможно, нужно не искать, а вставить/поправить в файле.",
            remember_prompt=text,
        )
        return
    answer += _format_citations(data.get("citations") or [])
    await _finish_status(status, message, answer)


async def _handle_gaps(
    message: Message, session: UserSession, *, user: User | None = None
) -> None:
    charge = await _require_quota(message, "ask", user=user)
    if charge is None:
        return
    status = await message.answer("Смотрю, какие поля ещё пустые…")
    try:
        async with session.lock:
            chunks = session.index.preview_chunks(20)
        if not chunks:
            _refund(message, charge, "ask", user=user)
            await status.edit_text("В загруженных файлах нет текста.")
            return
        text = await asyncio.to_thread(check_document_gaps, chunks)
    except Exception as exc:
        _refund(message, charge, "ask", user=user)
        await status.edit_text(_fit(f"Не удалось проверить поля: {exc}"))
        return
    session.remember_gaps(text)
    await _finish_status(status, message, text)


async def _handle_risks(
    message: Message, session: UserSession, *, user: User | None = None
) -> None:
    charge = await _require_quota(message, "ask", user=user)
    if charge is None:
        return
    status = await message.answer("Разбираю договор на риски…")
    try:
        async with session.lock:
            chunks = _chunks_for_active(session, limit=24)
        if not chunks:
            _refund(message, charge, "ask", user=user)
            await status.edit_text("В файле нет текста.")
            return
        text = await asyncio.to_thread(analyze_contract_risks, chunks)
    except Exception as exc:
        _refund(message, charge, "ask", user=user)
        await status.edit_text(_fit(f"Не удалось разобрать договор: {exc}"))
        return
    session.remember_doc_task("ask", "риски договора")
    await _finish_status(status, message, text)


async def _handle_extract(
    message: Message, session: UserSession, *, user: User | None = None
) -> None:
    charge = await _require_quota(message, "ask", user=user)
    if charge is None:
        return
    status = await message.answer("Собираю стороны, даты и суммы…")
    try:
        async with session.lock:
            chunks = _chunks_for_active(session, limit=24)
        if not chunks:
            _refund(message, charge, "ask", user=user)
            await status.edit_text("В файле нет текста.")
            return
        text = await asyncio.to_thread(extract_key_facts, chunks)
    except Exception as exc:
        _refund(message, charge, "ask", user=user)
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


async def _handle_compare(
    message: Message, session: UserSession, *, user: User | None = None
) -> None:
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
    charge = await _require_quota(message, "ask", user=user)
    if charge is None:
        return
    status = await message.answer(
        f"Сравниваю:\n• {file_a.name}\n• {file_b.name}"
    )
    try:
        chunks_a = file_a.chunks[:: max(1, len(file_a.chunks) // 18)][:18] if file_a.chunks else []
        chunks_b = file_b.chunks[:: max(1, len(file_b.chunks) // 18)][:18] if file_b.chunks else []
        if not chunks_a or not chunks_b:
            _refund(message, charge, "ask", user=user)
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
        _refund(message, charge, "ask", user=user)
        await status.edit_text(_fit(f"Не удалось сравнить: {exc}"))
        return
    session.remember_doc_task("ask", "сравнение")
    await _finish_status(status, message, text)


async def _handle_format_sample(
    message: Message, session: UserSession, *, user: User | None = None
) -> None:
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
    charge = await _require_quota(message, "write", user=user)
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
            _refund(message, charge, "write", user=user)
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
        _refund(message, charge, "write", user=user)
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
    *,
    announce: bool = True,
    user: User | None = None,
) -> None:
    active = session.active_path
    if active is None:
        await message.answer(
            "Нет активного файла. Отправьте документ или выберите его через /use.",
            reply_markup=ui.soft_landing_keyboard(has_files=False),
        )
        return
    if active.suffix.lower() != ".docx":
        await message.answer(
            "Править с сохранением можно только .docx.\n"
            "Задайте вопрос по тексту или пришлите Word-файл.",
            reply_markup=ui.docs_inline(),
        )
        return

    transform_op = detect_transform(text)
    if transform_op:
        charge = await _require_quota(message, "edit", user=user)
        if charge is None:
            return
        if announce:
            await message.answer(
                ui.interpret_confirm_text(
                    _interpretation_for_edit(text, "auto", transform_op)
                )
            )
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
            _refund(message, charge, "edit", user=user)
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

    charge = await _require_quota(message, "edit", user=user)
    if charge is None:
        return
    if announce:
        await message.answer(
            ui.interpret_confirm_text(_interpretation_for_edit(text, edit_mode, None))
        )
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
            _refund(message, charge, "edit", user=user)
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
        _refund(message, charge, "edit", user=user)
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
        _refund(message, charge, "edit", user=user)
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
