"""Curated catalog of official IP-related government forms (FNS)."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_HOSTS = frozenset(
    {
        "nalog.gov.ru",
        "www.nalog.gov.ru",
    }
)

DISCLAIMER_RU = (
    "Ссылка с официального сайта ФНС. Перед подачей проверьте актуальность "
    "формы на странице ведомства. Это не юридическая консультация и не заполненная декларация."
)

_MAX_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class OfficialForm:
    id: str
    title: str
    aliases: tuple[str, ...]
    page_url: str
    file_url: str | None
    source_name: str = "ФНС России"
    notes: str = ""


# Links verified 2026-10-05 against nalog.gov.ru (HEAD 200, application/pdf where file_url set).
OFFICIAL_FORMS: tuple[OfficialForm, ...] = (
    OfficialForm(
        id="r21001",
        title="Форма № Р21001 — заявление о регистрации ИП",
        aliases=(
            "р21001",
            "r21001",
            "21001",
            "регистрация ип",
            "зарегистрировать ип",
            "открыть ип",
            "стать ип",
        ),
        page_url=(
            "https://www.nalog.gov.ru/rn77/related_activities/"
            "registration_ip_yl/registration_ip/order/4162994/"
        ),
        file_url="https://www.nalog.gov.ru/cdn/form/4162994.pdf",
        notes="Заявление о государственной регистрации физлица в качестве ИП.",
    ),
    OfficialForm(
        id="r24001",
        title="Форма № Р24001 — изменения в сведениях об ИП (ЕГРИП)",
        aliases=(
            "р24001",
            "r24001",
            "24001",
            "изменить оквэд",
            "изменения в егрип",
            "сменить оквэд",
            "внести изменения ип",
        ),
        page_url=(
            "https://www.nalog.gov.ru/rn77/related_activities/"
            "registration_ip_yl/registration_ip/alteration/4163166/"
        ),
        file_url="https://www.nalog.gov.ru/cdn/form/4163166.pdf",
        notes="Заявление о внесении изменений в сведения об ИП в ЕГРИП.",
    ),
    OfficialForm(
        id="r26001",
        title="Форма № Р26001 — прекращение деятельности ИП",
        aliases=(
            "р26001",
            "r26001",
            "26001",
            "закрыть ип",
            "прекращение ип",
            "ликвидация ип",
            "снять ип",
        ),
        page_url=(
            "https://www.nalog.gov.ru/rn77/related_activities/"
            "registration_ip_yl/registration_ip/termination_activities/4163194/"
        ),
        file_url="https://www.nalog.gov.ru/cdn/form/4163194.pdf",
        notes="Заявление о прекращении деятельности в качестве ИП.",
    ),
    OfficialForm(
        id="usn_notify",
        title="Уведомление (сообщение) по УСН — КНД 1154004",
        aliases=(
            "усн уведомлен",
            "переход на усн",
            "уведомление усн",
            "26.2-1",
            "26.2.1",
            "кнд 1154004",
            "1154004",
            "перейти на упрощёнк",
            "перейти на упрощенк",
        ),
        page_url="https://www.nalog.gov.ru/rn77/about_fts/docs/16640269/",
        file_url=(
            "https://www.nalog.gov.ru/html/sites/www.new.nalog.ru/"
            "files/about_fts/docs/16640269_1.pdf"
        ),
        notes=(
            "Актуальная форма уведомления/сообщения по УСН "
            "(приказ ФНС от 30.04.2026 № ЕД-1-3/285@). "
            "Раньше отдельно существовала форма 26.2-1."
        ),
    ),
    OfficialForm(
        id="usn_decl",
        title="Декларация по УСН — КНД 1152017",
        aliases=(
            "декларация усн",
            "декларацию усн",
            "налоговая декларация усн",
            "кнд 1152017",
            "1152017",
            "отчёт усн",
            "отчет усн",
        ),
        page_url="https://www.nalog.gov.ru/rn77/about_fts/docs/16598752/",
        file_url=(
            "https://www.nalog.gov.ru/html/sites/www.new.nalog.ru/"
            "files/about_fts/docs/16598752_1.pdf"
        ),
        notes=(
            "Форма декларации по УСН (приказ ФНС от 26.11.2025 № ЕД-7-3/1017@), "
            "для отчётности за 2025 год и далее."
        ),
    ),
    OfficialForm(
        id="patent",
        title="Заявление на получение патента (ПСН)",
        aliases=(
            "патент",
            "псн",
            "26.5-1",
            "26.5.1",
            "заявление на патент",
            "получение патента",
        ),
        page_url="https://www.nalog.gov.ru/rn77/taxation/taxes/vibor_sn/go_to_psn/",
        file_url=(
            "https://www.nalog.gov.ru/html/sites/www.new.nalog.ru/"
            "files/about_fts/docs/pril1_16600834.pdf"
        ),
        notes=(
            "Заявление на патент (приказ ФНС от 18.12.2025 № ЕД-7-3/1226@)."
        ),
    ),
)


def get_form(form_id: str) -> OfficialForm | None:
    for form in OFFICIAL_FORMS:
        if form.id == form_id:
            return form
    return None


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = t.replace("№", " ").replace("form", " ")
    t = re.sub(r"[^\w\s./-]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def looks_like_official_form_request(text: str, *, has_files: bool = False) -> bool:
    """User wants an official agency blank, not a Word mockup."""
    lowered = _norm(text)
    if not lowered:
        return False
    # Word mockup / academic blank — not FNS download
    if any(
        k in lowered
        for k in (
            "титульн",
            "поля пуст",
            "пустым",
            "пустыми",
            "макет",
            "для заполнения",
            "типовой шаблон",
        )
    ):
        return False
    # With an uploaded file, prefer ask/edit/check over catalog download.
    if has_files and any(
        k in lowered
        for k in (
            "проверь",
            "провери",
            "что в",
            "риск",
            "заполни",
            "замени",
            "поправ",
            "сравни",
            "выпиши",
            "вытащи",
        )
    ):
        return False
    if get_form_by_code(lowered) is not None:
        return True
    official_cue = any(
        k in lowered
        for k in (
            "официальн",
            "фнс",
            "nalog",
            "с сайта фнс",
            "с nalog",
            "с сайта налогов",
        )
    )
    blank_cue = any(
        k in lowered
        for k in (
            "бланк",
            "форма",
            "форму",
            "заявлен",
            "уведомлен",
            "декларац",
            "скача",
            "пришли",
        )
    )
    if official_cue and blank_cue:
        return True
    if official_cue and any(
        k in lowered for k in ("р21001", "р24001", "р26001", "усн", "патент", "ип")
    ):
        return True
    # Strong / unique catalog hit (e.g. «декларация УСН», «патент»)
    hits = match_official_forms(text, limit=2, min_score=6)
    if not hits:
        return False
    if len(hits) == 1:
        return True
    return hits[0][1] >= hits[1][1] + 3


def get_form_by_code(lowered: str) -> OfficialForm | None:
    codes = {
        "р21001": "r21001",
        "r21001": "r21001",
        "21001": "r21001",
        "р24001": "r24001",
        "r24001": "r24001",
        "24001": "r24001",
        "р26001": "r26001",
        "r26001": "r26001",
        "26001": "r26001",
        "1154004": "usn_notify",
        "1152017": "usn_decl",
        "26.2-1": "usn_notify",
        "26.2.1": "usn_notify",
        "26.5-1": "patent",
        "26.5.1": "patent",
    }
    for code, form_id in codes.items():
        if re.search(rf"(?<!\d){re.escape(code)}(?!\d)", lowered):
            return get_form(form_id)
    return None


def match_official_forms(
    text: str,
    *,
    limit: int = 5,
    min_score: int = 3,
) -> list[tuple[OfficialForm, int]]:
    lowered = _norm(text)
    if not lowered:
        return []
    by_code = get_form_by_code(lowered)
    scored: list[tuple[OfficialForm, int]] = []
    for form in OFFICIAL_FORMS:
        score = 0
        if by_code is not None and form.id == by_code.id:
            score += 20
        title_n = _norm(form.title)
        if title_n and title_n in lowered:
            score += 10
        for alias in form.aliases:
            a = _norm(alias)
            if not a:
                continue
            if a in lowered:
                score += 6 if len(a) >= 5 else 4
            else:
                # token overlap for multi-word aliases
                parts = [p for p in a.split() if len(p) > 2]
                if parts and all(p in lowered for p in parts):
                    score += 5
        if score >= min_score:
            scored.append((form, score))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    return scored[:limit]


def catalog_summary_ru() -> str:
    lines = ["Сейчас в каталоге официальных бланков ФНС:"]
    for form in OFFICIAL_FORMS:
        lines.append(f"• {form.title}")
    return "\n".join(lines)


def format_form_caption(form: OfficialForm, *, with_file: bool) -> str:
    lines = [
        f"<b>{form.title}</b>",
        f"Источник: {form.source_name}",
        f"Страница: {form.page_url}",
    ]
    if form.notes:
        lines.append(form.notes)
    if with_file:
        lines.append("Файл с официального сайта — во вложении.")
    else:
        lines.append(
            "Прямой файл сейчас не отправил — откройте страницу и скачайте там."
        )
    lines.append("")
    lines.append(DISCLAIMER_RU)
    return "\n".join(lines)


def is_allowed_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in ALLOWED_HOSTS


def download_official_file(url: str) -> Path | None:
    """Download a file from an allowlisted official host into a temp path."""
    if not url or not is_allowed_url(url):
        return None
    req = Request(
        url,
        headers={
            "User-Agent": "pyBot/1.0 (+official-forms catalog; educational helper)",
            "Accept": "application/pdf,*/*",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as resp:  # noqa: S310 — host allowlisted
            ctype = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read(_MAX_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    if len(data) > _MAX_BYTES or not data:
        return None
    # Prefer PDF; also accept octet-stream from FNS CDN
    if "html" in ctype and not data.startswith(b"%PDF"):
        return None
    suffix = ".pdf"
    path_part = urlparse(url).path.lower()
    if path_part.endswith(".docx"):
        suffix = ".docx"
    elif path_part.endswith(".doc"):
        suffix = ".doc"
    elif path_part.endswith(".xlsx"):
        suffix = ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()
    return Path(tmp.name)
