"""
Чистая маршрутизация текста с учётом режимов сессии (Phase A).

Без Telegram и без LLM refine — только эвристики + card_mode priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edit.transforms import detect_transform
from llm.client import (
    classify_document_intent,
    looks_like_card,
    looks_like_chitchat,
    looks_like_edit,
    looks_like_fill_data,
    looks_like_reverse_words,
    looks_like_short_gap_value,
)

CANCEL_CARD = frozenset({"отмена", "cancel", "отменить", "стоп"})


@dataclass(frozen=True)
class TextRouteContext:
    card_mode: bool = False
    has_files: bool = False
    has_facts: bool = False
    awaiting_gap_fill: bool = False


@dataclass(frozen=True)
class TextRoute:
    action: str  # card | cancel_card | exit_card_menu | dispatch
    intent: str = ""
    mode: str = ""
    confidence: float = 0.0
    clear_card_mode: bool = False
    decision: dict[str, Any] | None = None


def _wants_doc_override(text: str) -> bool:
    return bool(
        looks_like_edit(text)
        or looks_like_fill_data(text)
        or detect_transform(text)
        or looks_like_reverse_words(text)
    )


def resolve_text_route(text: str, ctx: TextRouteContext) -> TextRoute:
    """Куда направить пользовательский текст при текущем контексте сессии."""
    low = (text or "").strip().lower()
    clear_from_card = False

    if ctx.card_mode:
        if low in CANCEL_CARD:
            return TextRoute(action="cancel_card", clear_card_mode=True)
        if (
            looks_like_chitchat(text)
            and not looks_like_card(text)
            and not _wants_doc_override(text)
        ):
            return TextRoute(action="exit_card_menu", clear_card_mode=True)
        # После «что не заполнено» значение поля важнее названия товара
        if (
            ctx.awaiting_gap_fill
            and looks_like_short_gap_value(text)
            and not _wants_doc_override(text)
        ):
            clear_from_card = True
        elif not _wants_doc_override(text):
            # on_text уже отсекает пустые строки; защита для прямых вызовов
            if not (text or "").strip():
                return TextRoute(action="exit_card_menu", clear_card_mode=True)
            return TextRoute(action="card")
        else:
            clear_from_card = True

    decision = classify_document_intent(
        text,
        has_files=ctx.has_files,
        has_facts=ctx.has_facts,
        awaiting_gap_fill=ctx.awaiting_gap_fill,
    )
    intent = str(decision.get("intent") or "none")
    mode = str(decision.get("mode") or "")
    conf = float(decision.get("confidence") or 0)
    return TextRoute(
        action="dispatch",
        intent=intent,
        mode=mode,
        confidence=conf,
        clear_card_mode=clear_from_card,
        decision=decision,
    )
