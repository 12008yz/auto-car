from __future__ import annotations

import asyncio
import shutil
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from rag.pipeline import DocumentIndex


@dataclass
class PendingEdit:
    kind: str
    source: Path
    patches: list[dict[str, str]] = field(default_factory=list)
    rewrite_text: str = ""
    summary: str = ""


@dataclass
class PendingClarify:
    """Уточнение намерения перед генерацией/действием."""
    prompt: str
    options: list[dict[str, str]] = field(default_factory=list)  # id, label
    question: str = ""


@dataclass
class DocTask:
    """
    Контекст текущей работы с документами в чате.
    kind: fill | edit | ask | write | check
    """
    kind: str = ""
    last_prompt: str = ""
    facts: str = ""  # накопленные ФИО/даты/адреса для заполнения
    filename: str = ""


@dataclass
class UserSession:
    user_id: int
    index: DocumentIndex = field(default_factory=DocumentIndex)
    active_path: Path | None = None
    pending: PendingEdit | None = None
    pending_clarify: PendingClarify | None = None
    doc_task: DocTask | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    card_mode: bool = False
    # Недавние message_id в этом чате — для очистки ленты
    chat_message_ids: deque[int] = field(default_factory=lambda: deque(maxlen=2000))

    @property
    def active_name(self) -> str | None:
        return self.active_path.name if self.active_path else None

    def user_dir(self, root: Path) -> Path:
        path = root / str(self.user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remember_chat_message(self, message_id: int | None) -> None:
        if message_id and message_id > 0:
            self.chat_message_ids.append(int(message_id))

    def remember_doc_task(
        self,
        kind: str,
        prompt: str = "",
        *,
        accumulate_facts: bool = False,
    ) -> DocTask:
        task = self.doc_task or DocTask()
        task.kind = kind or task.kind
        if prompt:
            task.last_prompt = prompt
            if accumulate_facts or kind == "fill":
                # копим данные для «добавь эти данные» / уточнений ФИО
                chunk = prompt.strip()
                if chunk and chunk not in (task.facts or ""):
                    task.facts = f"{task.facts}\n{chunk}".strip() if task.facts else chunk
        if self.active_path is not None:
            task.filename = self.active_path.name
        self.doc_task = task
        return task

    def fill_instruction(self, prompt: str) -> str:
        """Собирает инструкцию для правки с учётом накопленных данных."""
        prompt = (prompt or "").strip()
        task = self.doc_task
        facts = (task.facts if task else "") or ""
        low = prompt.lower()
        refers_prev = any(
            k in low
            for k in (
                "эти данн",
                "те же",
                "выше",
                "которые писал",
                "которые указал",
                "из прошлого",
                "добавь их",
                "добавить их",
                "вставь их",
            )
        )
        if facts and (refers_prev or kind_is_fill(low) or looks_short_fill_followup(low)):
            return (
                f"{prompt}\n\n"
                f"Данные от пользователя (использовать для заполнения полей бланка):\n"
                f"{facts}"
            )
        if facts and kind_is_fill(low) and prompt not in facts:
            return (
                f"{prompt}\n\n"
                f"Ранее указанные данные (дополни / уточни ими бланк):\n{facts}"
            )
        return prompt

    def reset_memory(self) -> None:
        self.index = DocumentIndex()
        self.active_path = None
        self.pending = None
        self.pending_clarify = None
        self.doc_task = None
        self.card_mode = False
        self.chat_message_ids.clear()


def kind_is_fill(lowered: str) -> bool:
    return any(
        k in lowered
        for k in (
            "добав",
            "заполни",
            "вставь данн",
            "встав данн",
            "подставь",
            "внеси данн",
            "фио",
            "мои данн",
            "эти данн",
        )
    )


def looks_short_fill_followup(lowered: str) -> bool:
    """Короткие уточнения вроде «Нужно добавить эти данные»."""
    if len(lowered) > 80:
        return False
    return any(
        k in lowered
        for k in ("добав", "вставь", "заполни", "эти данн", "фио", "подставь")
    )


_sessions: dict[int, UserSession] = {}


def get_session(user_id: int) -> UserSession:
    if user_id not in _sessions:
        _sessions[user_id] = UserSession(user_id=user_id)
    return _sessions[user_id]


def clear_user_workspace(user_id: int, data_root: Path) -> None:
    """Сброс сессии в памяти и удаление загруженных файлов пользователя."""
    session = get_session(user_id)
    session.reset_memory()
    user_path = data_root / str(user_id)
    if user_path.exists() and user_path.is_dir():
        shutil.rmtree(user_path, ignore_errors=True)
