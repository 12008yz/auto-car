"""Schemas and detection for IP/SMB business document drafts."""

from __future__ import annotations

import re
from typing import Any

BUSINESS_KINDS = ("contract", "act", "invoice", "claim", "letter")

KIND_LABELS_RU: dict[str, str] = {
    "contract": "Договор",
    "act": "Акт",
    "invoice": "Счёт",
    "claim": "Претензия",
    "letter": "Письмо",
}

_FIELD_LABELS_RU: dict[str, str] = {
    "party_a": "первая сторона (исполнитель)",
    "party_b": "вторая сторона (заказчик)",
    "subject": "предмет договора",
    "price": "цена / сумма",
    "term": "срок",
    "services": "перечень услуг",
    "amount": "сумма",
    "period": "период оказания услуг",
    "seller": "продавец / исполнитель",
    "buyer": "покупатель / заказчик",
    "line_items": "позиции счёта",
    "bank_details": "банковские реквизиты (р/с, БИК, банк)",
    "addressee": "адресат",
    "violation_summary": "в чём нарушение",
    "demand": "требование",
    "reply_deadline": "срок ответа",
    "body_purpose": "суть письма",
    "sender_sign": "подпись отправителя",
    "service_name": "наименование услуги",
}

_REQUIRED: dict[str, list[str]] = {
    "contract": ["party_a", "party_b", "subject", "price", "term"],
    "act": ["party_a", "party_b", "services", "amount", "period"],
    "invoice": ["seller", "buyer", "line_items"],
    "claim": ["addressee", "violation_summary", "demand"],
    "letter": ["addressee", "body_purpose"],
}

_SOFT_REQUIRED: dict[str, list[str]] = {
    "contract": [],
    "act": [],
    "invoice": ["bank_details"],
    "claim": ["reply_deadline"],
    "letter": ["sender_sign"],
}

_CLAIM_DEFAULT_DEADLINE = "в течение 10 календарных дней с даты получения"

_CREATE_RE = re.compile(
    r"(?i)(?:сделай|сделать|составь|составить|напиши|написать|оформи|оформить|"
    r"выстави|выставить|подготовь|подготовить|нужен|нужна|нужно|нужны|"
    r"хочу|создай|создать|сгенерируй)"
)

_ACADEMIC_RE = re.compile(r"(?i)реферат|эссе|доклад|сочинен")

# (kind, patterns) — patterns find mention positions
_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("invoice", re.compile(r"(?i)сч[её]т(?:а|у|ом|е)?\b|инвойс")),
    ("claim", re.compile(r"(?i)претензи")),
    ("act", re.compile(r"(?i)(?<![а-яё])акт(?:а|у|ом|е|ы)?(?![а-яё])")),
    ("contract", re.compile(r"(?i)договор")),
    ("letter", re.compile(r"(?i)(?:деловое\s+)?письм")),
]


def required_fields(kind: str) -> list[str]:
    return list(_REQUIRED.get(kind, []))


def soft_required_fields(kind: str) -> list[str]:
    return list(_SOFT_REQUIRED.get(kind, []))


def field_labels_ru() -> dict[str, str]:
    return dict(_FIELD_LABELS_RU)


def wants_waive_bank(text: str) -> bool:
    lowered = (text or "").lower()
    markers = (
        "нет реквизит",
        "реквизитов нет",
        "без реквизит",
        "пропусти реквизит",
        "реквизиты потом",
        "не укажу реквизит",
        "без банка",
    )
    return any(m in lowered for m in markers)


def is_vague_user_reply(text: str) -> bool:
    """Ответ не несёт данных — не подмешивать в поля документа."""
    raw = (text or "").strip().lower()
    if not raw or len(raw) > 40:
        return False
    vague = (
        "не знаю",
        "хз",
        "не помню",
        "не понял",
        "не понимаю",
        "???",
        "??",
        "что?",
        "что",
        "как?",
        "зачем",
        "потом",
        "сам",
        "любой",
        "неважно",
    )
    if raw in vague:
        return True
    return any(v in raw for v in ("не знаю", "не помню", "не понял", "не понимаю"))


def kind_from_short_hint(text: str) -> str | None:
    """«счёт», «договор» — когда ждём выбор типа кнопкой."""
    raw = (text or "").strip().lower()
    if not raw or len(raw) > 40:
        return None
    hints = {
        "contract": ("договор", "контракт"),
        "act": ("акт",),
        "invoice": ("счёт", "счет", "инвойс"),
        "claim": ("претенз",),
        "letter": ("письм",),
    }
    for kind, words in hints.items():
        if any(w in raw for w in words):
            return kind
    return None


def missing_question(missing: list[str]) -> str:
    labels = [_FIELD_LABELS_RU.get(k, k) for k in missing[:3]]
    if not labels:
        return "Уточните недостающие данные."
    if len(labels) == 1:
        return f"Укажите, пожалуйста: {labels[0]}."
    return "Укажите, пожалуйста: " + "; ".join(labels) + "."


def listed_business_kinds(text: str) -> list[str]:
    """Kinds mentioned in text, in first-mention order (deduped)."""
    raw = text or ""
    hits: list[tuple[int, str]] = []
    for kind, pattern in _KIND_PATTERNS:
        m = pattern.search(raw)
        if m:
            hits.append((m.start(), kind))
    hits.sort(key=lambda x: x[0])
    seen: set[str] = set()
    ordered: list[str] = []
    for _, kind in hits:
        if kind not in seen:
            seen.add(kind)
            ordered.append(kind)
    return ordered


def detect_business_kind(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if _ACADEMIC_RE.search(raw):
        return None
    kinds = listed_business_kinds(raw)
    if not kinds:
        return None
    # Need create/need signal unless the text is clearly a doc request with kind noun
    if not _CREATE_RE.search(raw):
        # allow «претензия поставщику…» style? Plan: questions without create → None
        return None
    return kinds[0]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _invoice_has_lines(fields: dict[str, Any]) -> bool:
    items = fields.get("line_items")
    if isinstance(items, list) and any(
        isinstance(x, dict) and str(x.get("name") or "").strip() for x in items
    ):
        return True
    name = str(fields.get("service_name") or "").strip()
    amount = fields.get("amount")
    return bool(name and not _is_empty(amount))


def missing_fields(kind: str, fields: dict[str, Any] | None) -> list[str]:
    data = fields or {}
    missing: list[str] = []
    for key in required_fields(kind):
        if kind == "invoice" and key == "line_items":
            if not _invoice_has_lines(data):
                missing.append("line_items")
            continue
        if key == "services":
            services = data.get("services")
            if isinstance(services, list):
                if not any(str(x).strip() for x in services):
                    missing.append(key)
            elif _is_empty(services):
                missing.append(key)
            continue
        if _is_empty(data.get(key)):
            missing.append(key)
    for key in soft_required_fields(kind):
        waive = bool(data.get(f"_waive_{key}"))
        if waive:
            continue
        if kind == "claim" and key == "reply_deadline":
            if _is_empty(data.get(key)):
                missing.append(key)
            continue
        if kind == "letter" and key == "sender_sign":
            if _is_empty(data.get(key)):
                missing.append(key)
            continue
        if kind == "invoice" and key == "bank_details":
            bank = data.get("bank_details")
            if isinstance(bank, dict):
                if not any(str(bank.get(k) or "").strip() for k in ("rs", "bik", "bank")):
                    missing.append(key)
            elif _is_empty(bank):
                missing.append(key)
            continue
        if _is_empty(data.get(key)):
            missing.append(key)
    return missing


def apply_defaults(kind: str, fields: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(fields or {})
    if kind == "claim" and _is_empty(out.get("reply_deadline")):
        out["reply_deadline"] = _CLAIM_DEFAULT_DEADLINE
    if kind == "letter" and _is_empty(out.get("sender_sign")):
        # leave empty so soft-required can ask once; no invented name
        pass
    if kind == "invoice":
        items = out.get("line_items")
        if not items and out.get("service_name") and not _is_empty(out.get("amount")):
            try:
                price = float(str(out.get("amount")).replace(" ", "").replace(",", "."))
            except ValueError:
                price = out.get("amount")
            out["line_items"] = [
                {"name": str(out["service_name"]).strip(), "qty": 1, "price": price}
            ]
        if _is_empty(out.get("total")) and isinstance(out.get("line_items"), list):
            total = 0.0
            ok = True
            for row in out["line_items"]:
                if not isinstance(row, dict):
                    ok = False
                    break
                try:
                    qty = float(row.get("qty") or 1)
                    price = float(
                        str(row.get("price") or "0").replace(" ", "").replace(",", ".")
                    )
                    total += qty * price
                except ValueError:
                    ok = False
                    break
            if ok and total:
                out["total"] = total
    return out
