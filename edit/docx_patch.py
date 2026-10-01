from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt
from docx.text.paragraph import Paragraph


def _replace_in_paragraph(paragraph: Paragraph, find: str, replace: str) -> int:
    """
    Точечная замена с максимальным сохранением runs/стиля.
    Если find целиком в одном run — правим только его.
    Иначе аккуратно собираем текст, оставляя стиль первого run.
    """
    full = paragraph.text
    if not find or find not in full:
        return 0
    count = full.count(find)

    # Лучший путь: замена внутри одного run
    for run in paragraph.runs:
        if find in (run.text or ""):
            run.text = (run.text or "").replace(find, replace)
            return count

    # Cross-run: не трогаем XML-структуру абзаца сильнее необходимого
    new_text = full.replace(find, replace)
    if not paragraph.runs:
        paragraph.add_run(new_text)
        return count

    # Сохраняем форматирование первого непустого run, остальные очищаем
    first = None
    for run in paragraph.runs:
        if first is None:
            first = run
        run.text = ""
    assert first is not None
    first.text = new_text
    return count


def _walk_paragraphs(doc: Document):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for section in doc.sections:
        yield from section.header.paragraphs
        yield from section.footer.paragraphs


_WORD_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")


def reverse_words_in_text(text: str) -> str:
    """Переворачивает каждое слово задом наперёд, пунктуацию не трогает."""
    return _WORD_TOKEN.sub(lambda m: m.group(0)[::-1], text or "")


def _set_paragraph_text(paragraph: Paragraph, text: str) -> None:
    if not paragraph.runs:
        paragraph.add_run(text)
        return
    first = paragraph.runs[0]
    for run in paragraph.runs:
        run.text = ""
    first.text = text


def reverse_words_docx(src: Path, dest: Path) -> dict:
    """Копия DOCX, где каждое слово в абзацах/таблицах развёрнуто задом наперёд."""
    doc = Document(str(src))
    changed = 0
    for para in _walk_paragraphs(doc):
        original = para.text
        if not original.strip():
            continue
        updated = reverse_words_in_text(original)
        if updated != original:
            _set_paragraph_text(para, updated)
            changed += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return {"path": dest, "changed": changed}


def document_plain_text(src: Path) -> str:
    doc = Document(str(src))
    return "\n".join(p.text for p in _walk_paragraphs(doc))


def filter_valid_patches(src: Path, patches: list[dict[str, str]]) -> list[dict[str, str]]:
    """Оставляет только патчи, чей find реально есть в документе."""
    blob = document_plain_text(src)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for patch in patches or []:
        find = (patch.get("find") or "").strip()
        if not find or find in seen:
            continue
        if find not in blob:
            continue
        seen.add(find)
        out.append({"find": find, "replace": str(patch.get("replace") or "")})
    return out


def apply_patches(src: Path, dest: Path, patches: list[dict[str, str]]) -> dict:
    """
    Применяет точечные замены к копии DOCX.
    Не создаёт новые абзацы и не перестраивает документ целиком.
    """
    doc = Document(str(src))
    applied: list[dict] = []
    missing: list[str] = []
    seen: set[str] = set()
    for patch in patches:
        find = (patch.get("find") or "").strip()
        replace = patch.get("replace")
        if replace is None:
            replace = ""
        replace = str(replace)
        if not find or find in seen:
            continue
        seen.add(find)
        # Защита: слишком короткий find легко портит случайные места
        if len(find) < 3:
            missing.append(find)
            continue
        total = 0
        for para in _walk_paragraphs(doc):
            total += _replace_in_paragraph(para, find, replace)
        if total:
            applied.append({"find": find, "replace": replace, "count": total})
        else:
            missing.append(find)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return {"applied": applied, "missing": missing, "path": dest}


def write_rewrite_docx(dest: Path, text: str) -> Path:
    """Простой документ из plain-text (абзацы по строкам)."""
    doc = Document()
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Times New Roman"
    font.size = Pt(14)

    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    if not paragraphs:
        doc.add_paragraph("")
    for p in paragraphs:
        doc.add_paragraph(p)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest


def _safe_stem(name: str, fallback: str = "document") -> str:
    cleaned = re.sub(r"[^\w\- а-яА-ЯёЁ]+", "", (name or "").strip(), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return (cleaned[:60] or fallback)


def write_structured_docx(
    dest: Path,
    *,
    title: str,
    sections: list[dict],
    doc_type: str = "document",
    layout: str = "text",
) -> Path:
    """
    Word-документ: title + секции.
    layout: text (реферат/письмо) | form (бланк/титульный лист).
    sections: [{"heading": str|None, "paragraphs": [str, ...]}, ...]
    """
    is_form = (layout or "text").strip().lower() == "form"
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12 if is_form else 14)

    title = (title or "Документ").strip()
    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    head.paragraph_format.space_after = Pt(6)
    run = head.add_run(title)
    run.bold = True
    run.font.name = "Times New Roman"
    run.font.size = Pt(16 if is_form else 16)

    subtype = (doc_type or "").strip().lower()
    if not is_form and subtype in {
        "реферат",
        "referat",
        "эссе",
        "essay",
        "доклад",
        "report",
    }:
        tip = doc.add_paragraph()
        tip.alignment = WD_ALIGN_PARAGRAPH.CENTER
        tr = tip.add_run(subtype.capitalize() if subtype.isascii() else subtype)
        tr.italic = True
        tr.font.size = Pt(12)
        tr.font.name = "Times New Roman"

    if is_form:
        note = doc.add_paragraph()
        note.alignment = WD_ALIGN_PARAGRAPH.CENTER
        nr = note.add_run(
            "Типовой шаблон для заполнения · не официальный бланк"
        )
        nr.italic = True
        nr.font.size = Pt(10)
        nr.font.name = "Times New Roman"
        note.paragraph_format.space_after = Pt(12)

    doc.add_paragraph("")

    for section in sections or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        paragraphs = section.get("paragraphs") or []
        if not isinstance(paragraphs, list):
            paragraphs = [str(paragraphs)]
        if heading:
            if is_form:
                h = doc.add_paragraph()
                h.paragraph_format.space_before = Pt(10)
                h.paragraph_format.space_after = Pt(6)
                hr = h.add_run(heading)
                hr.bold = True
                hr.font.name = "Times New Roman"
                hr.font.size = Pt(12)
            else:
                h = doc.add_heading(heading, level=1)
                for run in h.runs:
                    run.font.name = "Times New Roman"
        for raw in paragraphs:
            text = str(raw or "").strip()
            if not text:
                continue
            # Не дублируем общий дисклеймер, если модель уже вставила похожий
            if is_form and "не официальный" in text.lower() and "шаблон" in text.lower():
                continue
            p = doc.add_paragraph()
            if is_form:
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.space_before = Pt(0)
                pr = p.add_run(text)
                pr.font.name = "Times New Roman"
                pr.font.size = Pt(12)
            else:
                p.add_run(text)
                p.paragraph_format.first_line_indent = Pt(24)
                p.paragraph_format.space_after = Pt(8)
                for run in p.runs:
                    run.font.name = "Times New Roman"
                    run.font.size = Pt(14)

    dest = Path(dest)
    if dest.suffix.lower() != ".docx":
        dest = dest.with_suffix(".docx")
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest


def suggest_filename(title: str, doc_type: str = "document") -> str:
    kind = _safe_stem(doc_type, "document")
    topic = _safe_stem(title, "tema")
    return f"{kind}_{topic}.docx"
