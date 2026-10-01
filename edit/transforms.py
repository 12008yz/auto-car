"""Каталог детерминированных правок DOCX (без LLM)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from edit.docx_patch import reverse_words_docx
from llm.client import looks_like_reverse_words


TransformFn = Callable[[Path, Path], dict]


def _upper_case_docx(src: Path, dest: Path) -> dict:
    from docx import Document
    from edit.docx_patch import _set_paragraph_text, _walk_paragraphs

    doc = Document(str(src))
    changed = 0
    for para in _walk_paragraphs(doc):
        original = para.text
        if not original.strip():
            continue
        updated = original.upper()
        if updated != original:
            _set_paragraph_text(para, updated)
            changed += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return {"path": dest, "changed": changed, "op": "upper_case"}


def _lower_case_docx(src: Path, dest: Path) -> dict:
    from docx import Document
    from edit.docx_patch import _set_paragraph_text, _walk_paragraphs

    doc = Document(str(src))
    changed = 0
    for para in _walk_paragraphs(doc):
        original = para.text
        if not original.strip():
            continue
        updated = original.lower()
        if updated != original:
            _set_paragraph_text(para, updated)
            changed += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return {"path": dest, "changed": changed, "op": "lower_case"}


def _reverse_words(src: Path, dest: Path) -> dict:
    result = reverse_words_docx(src, dest)
    result["op"] = "reverse_words"
    return result


_TRANSFORMS: dict[str, TransformFn] = {
    "reverse_words": _reverse_words,
    "upper_case": _upper_case_docx,
    "lower_case": _lower_case_docx,
}


def looks_like_upper_case(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        k in lowered
        for k in (
            "заглавн",
            "верхний регистр",
            "верхнем регистр",
            "все заглавн",
            "сделай капсом",
            "caps lock",
            "uppercase",
            "все буквы больш",
        )
    )


def looks_like_lower_case(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        k in lowered
        for k in (
            "нижний регистр",
            "нижнем регистр",
            "все строчн",
            "маленькими буквами",
            "lowercase",
            "все буквы маленьк",
        )
    )


def detect_transform(text: str) -> str | None:
    """Возвращает op_id или None. Порядок: более специфичные первыми."""
    if looks_like_reverse_words(text):
        return "reverse_words"
    if looks_like_upper_case(text):
        return "upper_case"
    if looks_like_lower_case(text):
        return "lower_case"
    return None


def apply_transform(op: str, src: Path, dest: Path) -> dict:
    fn = _TRANSFORMS.get(op)
    if fn is None:
        raise ValueError(f"Unknown transform: {op}")
    return fn(src, dest)


def known_transforms() -> tuple[str, ...]:
    return tuple(_TRANSFORMS.keys())
