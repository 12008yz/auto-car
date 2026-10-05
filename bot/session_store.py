"""Persist dialog session (pending, facts, flow, message ids) in SQLite."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from billing.db import connect
from bot.session import BusinessDraft, DocTask, PendingClarify, PendingEdit, UserSession

_MAX_STORED_MESSAGE_IDS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dialog_schema() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialog_sessions (
                telegram_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def session_to_payload(session: UserSession) -> dict[str, Any]:
    pending = None
    if session.pending is not None:
        pending = {
            "kind": session.pending.kind,
            "source": str(session.pending.source),
            "patches": list(session.pending.patches),
            "rewrite_text": session.pending.rewrite_text,
            "summary": session.pending.summary,
        }
    clarify = None
    if session.pending_clarify is not None:
        clarify = {
            "prompt": session.pending_clarify.prompt,
            "options": list(session.pending_clarify.options),
            "question": session.pending_clarify.question,
        }
    doc_task = None
    if session.doc_task is not None:
        doc_task = {
            "kind": session.doc_task.kind,
            "last_prompt": session.doc_task.last_prompt,
            "facts": session.doc_task.facts,
            "filename": session.doc_task.filename,
            "gap_fields": list(session.doc_task.gap_fields),
        }
    business = None
    if session.business_draft is not None:
        d = session.business_draft
        business = {
            "kind": d.kind,
            "fields": dict(d.fields),
            "missing": list(d.missing),
            "base_prompt": d.base_prompt,
            "bank_asked": bool(d.bank_asked),
            "questions_asked": int(d.questions_asked),
        }
    ids = list(session.chat_message_ids)[-_MAX_STORED_MESSAGE_IDS:]
    return {
        "flow": session.flow,
        "last_op": session.last_op,
        "card_mode": bool(session.card_mode),
        "awaiting_gap_fill": bool(session.awaiting_gap_fill),
        "active_path": str(session.active_path) if session.active_path else None,
        "pending": pending,
        "pending_clarify": clarify,
        "doc_task": doc_task,
        "business_draft": business,
        "chat_message_ids": ids,
    }


def apply_payload(session: UserSession, payload: dict[str, Any]) -> None:
    session.flow = str(payload.get("flow") or "idle")
    session.last_op = str(payload.get("last_op") or "")
    session.card_mode = bool(payload.get("card_mode"))
    session.awaiting_gap_fill = bool(payload.get("awaiting_gap_fill"))

    active = payload.get("active_path")
    if active:
        path = Path(str(active))
        session.active_path = path if path.exists() else None
    else:
        session.active_path = None

    pending_raw = payload.get("pending")
    if isinstance(pending_raw, dict) and pending_raw.get("source"):
        src = Path(str(pending_raw["source"]))
        session.pending = PendingEdit(
            kind=str(pending_raw.get("kind") or "edit"),
            source=src,
            patches=list(pending_raw.get("patches") or []),
            rewrite_text=str(pending_raw.get("rewrite_text") or ""),
            summary=str(pending_raw.get("summary") or ""),
        )
        if not src.exists():
            # Черновик без файла на диске бесполезен
            session.pending = None
            if session.flow == "awaiting_confirm":
                session.flow = "idle"
    else:
        session.pending = None

    clarify_raw = payload.get("pending_clarify")
    if isinstance(clarify_raw, dict):
        session.pending_clarify = PendingClarify(
            prompt=str(clarify_raw.get("prompt") or ""),
            options=list(clarify_raw.get("options") or []),
            question=str(clarify_raw.get("question") or ""),
        )
    else:
        session.pending_clarify = None

    task_raw = payload.get("doc_task")
    if isinstance(task_raw, dict):
        session.doc_task = DocTask(
            kind=str(task_raw.get("kind") or ""),
            last_prompt=str(task_raw.get("last_prompt") or ""),
            facts=str(task_raw.get("facts") or ""),
            filename=str(task_raw.get("filename") or ""),
            gap_fields=list(task_raw.get("gap_fields") or []),
        )
    else:
        session.doc_task = None

    biz_raw = payload.get("business_draft")
    if isinstance(biz_raw, dict) and str(biz_raw.get("kind") or "").strip():
        session.business_draft = BusinessDraft(
            kind=str(biz_raw.get("kind") or ""),
            fields=dict(biz_raw.get("fields") or {}),
            missing=list(biz_raw.get("missing") or []),
            base_prompt=str(biz_raw.get("base_prompt") or ""),
            bank_asked=bool(biz_raw.get("bank_asked")),
            questions_asked=int(biz_raw.get("questions_asked") or 0),
        )
    else:
        session.business_draft = None

    session.chat_message_ids.clear()
    for mid in payload.get("chat_message_ids") or []:
        try:
            n = int(mid)
        except (TypeError, ValueError):
            continue
        if n > 0:
            session.chat_message_ids.append(n)


def save_dialog_session(session: UserSession) -> None:
    ensure_dialog_schema()
    payload = session_to_payload(session)
    blob = json.dumps(payload, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO dialog_sessions (telegram_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (session.user_id, blob, _now_iso()),
        )
        conn.commit()


def load_dialog_payload(user_id: int) -> dict[str, Any] | None:
    ensure_dialog_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM dialog_sessions WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def delete_dialog_session(user_id: int) -> None:
    ensure_dialog_schema()
    with connect() as conn:
        conn.execute(
            "DELETE FROM dialog_sessions WHERE telegram_id = ?",
            (user_id,),
        )
        conn.commit()


def rebuild_index_from_disk(session: UserSession, data_root: Path) -> None:
    """Пересобрать RAG-индекс из файлов пользователя (после рестарта)."""
    from config import ALLOWED_SUFFIXES
    from docs.loaders import load_document
    from rag.pipeline import chunk_blocks

    user_dir = data_root / str(session.user_id)
    if not user_dir.is_dir():
        return

    paths: list[Path] = []
    for path in sorted(user_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES:
            paths.append(path)

    active = session.active_path
    if active is not None and active.exists() and active not in paths:
        paths.insert(0, active)

    for path in paths:
        try:
            blocks = load_document(path)
            chunks = chunk_blocks(blocks, path.name)
            if chunks:
                session.index.add_file(path, chunks)
        except Exception:
            continue

    if session.active_path is not None and not session.active_path.exists():
        session.active_path = None
