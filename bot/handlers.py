from __future__ import annotations

import asyncio
import re
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from bot.session import PendingEdit, UserSession, get_session
from card.render import render_product_card
from config import ALLOWED_SUFFIXES, DATA_DIR, IMAGE_SUFFIXES, MAX_FILE_BYTES, TELEGRAM_MAX_LEN
from docs.loaders import load_document
from edit.docx_patch import apply_patches, write_rewrite_docx
from llm.client import (
    ask_document,
    looks_like_card,
    looks_like_edit,
    make_product_card,
    plan_edits,
    summarize_document,
)
from rag.pipeline import Hit, chunk_blocks

router = Router()

HELP_TEXT = (
    "Документы: отправьте Word, PDF, Excel, PowerPoint, TXT, MD, CSV — "
    "потом вопрос по тексту или правку Word.\n\n"
    "Карточка товара: /card и фото (можно с подписью: название, материал, для кого).\n\n"
    "/card — режим карточки товара\n"
    "/summary — краткое содержание активного файла\n"
    "/files — список загруженных файлов\n"
    "/use имя_файла — сделать файл активным\n"
    "/help — эта подсказка\n\n"
    "Точечные правки с сохранением файла сейчас только для .docx."
)


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
    await message.answer("Сначала отправьте документ.")
    return False


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Бот готов.\n\n" + HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("card"))
async def cmd_card(message: Message, command: CommandObject) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    session.card_mode = True
    notes = (command.args or "").strip()
    if notes:
        await _make_card(message, session, notes, None)
        return
    await message.answer(
        "Пришлите фото товара. В подписи можно указать название, материал, "
        "для кого, размеры и плюсы.\n"
        "Можно и без фото — просто напишите, что продаёте."
    )


@router.message(Command("files"))
async def cmd_files(message: Message) -> None:
    session = _session_of(message.from_user)
    if session is None:
        return
    if not session.index.files:
        await message.answer("Файлов пока нет. Отправьте документ.")
        return
    lines = []
    for item in session.index.files:
        mark = " (активный)" if session.active_path and item.path == session.active_path else ""
        lines.append(f"• {item.name}{mark}")
    await _reply_long(message, "Загружено:\n" + "\n".join(lines))


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
    status = await message.answer("Готовлю краткое содержание…")
    try:
        async with session.lock:
            chunks = session.index.preview_chunks()
        if not chunks:
            await status.edit_text("В активных файлах нет текста для содержания.")
            return
        text = await asyncio.to_thread(summarize_document, chunks)
    except Exception as exc:
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
        await message.answer("Чтобы собрать карточку товара, напишите /card и пришлите фото.")
        return
    dest = session.user_dir(DATA_DIR) / "product.jpg"
    try:
        await message.bot.download(message.photo[-1], destination=dest)
    except Exception as exc:
        await message.answer(_fit(f"Не удалось скачать фото: {exc}"))
        return
    await _make_card(message, session, caption, dest)


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
            await message.answer("Это картинка. Для карточки товара напишите /card и пришлите фото.")
            return
        dest = session.user_dir(DATA_DIR) / name
        try:
            await message.bot.download(doc, destination=dest)
        except Exception as exc:
            await message.answer(_fit(f"Не удалось скачать фото: {exc}"))
            return
        await _make_card(message, session, caption, dest)
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
        f"Готово: {name}. Фрагментов: {n_chunks}. "
        "Можно спрашивать по тексту или просить правки (для .docx)."
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
        await _make_card(message, session, text, None)
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
) -> None:
    status = await message.answer("Собираю карточку товара…")
    image_bytes = None
    if photo_path and photo_path.exists():
        image_bytes = photo_path.read_bytes()
        if len(image_bytes) > MAX_FILE_BYTES:
            await status.edit_text("Фото больше 20 МБ.")
            return
    try:
        data = await asyncio.to_thread(make_product_card, notes or "", image_bytes)
        dest_dir = session.user_dir(DATA_DIR)
        png = dest_dir / "card.png"
        n = 1
        while png.exists():
            png = dest_dir / f"card_{n}.png"
            n += 1
        await asyncio.to_thread(render_product_card, png, data, photo_path)
    except Exception as exc:
        await status.edit_text(_fit(f"Не удалось собрать карточку: {exc}"))
        return
    session.card_mode = False
    try:
        await status.delete()
    except Exception:
        pass
    await message.answer_photo(FSInputFile(png))
    parts = [str(data.get("title") or "Карточка товара")]
    if data.get("description"):
        parts.append(str(data["description"]))
    if data.get("keywords"):
        parts.append("Ключевые запросы:\n" + str(data["keywords"]))
    await _reply_long(message, "\n\n".join(parts))


async def _handle_question(message: Message, session: UserSession, text: str) -> None:
    status = await message.answer("Ищу в документах…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
        if not hits:
            await status.edit_text("По этому запросу фрагментов не нашлось.")
            return
        data = await asyncio.to_thread(ask_document, text, hits)
    except Exception as exc:
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
    status = await message.answer("Готовлю план правок…")
    try:
        async with session.lock:
            hits = await asyncio.to_thread(session.index.search, text)
            if not hits:
                hits = [Hit(chunk=c, score=1.0) for c in session.index.preview_chunks(12)]
        if not hits:
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
        await status.edit_text(_fit(f"Ошибка модели: {exc}"))
        return
    kind = data.get("kind") or "none"
    if kind == "none" or (
        kind == "patch" and not data.get("patches")
    ) or (
        kind == "rewrite" and not str(data.get("rewrite_text") or "").strip()
    ):
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
