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
class UserSession:
    user_id: int
    index: DocumentIndex = field(default_factory=DocumentIndex)
    active_path: Path | None = None
    pending: PendingEdit | None = None
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

    def reset_memory(self) -> None:
        self.index = DocumentIndex()
        self.active_path = None
        self.pending = None
        self.card_mode = False
        self.chat_message_ids.clear()


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
