from __future__ import annotations

import asyncio
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

    @property
    def active_name(self) -> str | None:
        return self.active_path.name if self.active_path else None

    def user_dir(self, root: Path) -> Path:
        path = root / str(self.user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


_sessions: dict[int, UserSession] = {}


def get_session(user_id: int) -> UserSession:
    if user_id not in _sessions:
        _sessions[user_id] = UserSession(user_id=user_id)
    return _sessions[user_id]
