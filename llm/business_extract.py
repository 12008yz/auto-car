"""Extract and sanitize fields for business document drafts."""

from __future__ import annotations

import re
from typing import Any, Callable

from docs.business_types import apply_defaults, missing_fields

_SENSITIVE_KEYS = {
    "inn",
    "inn_a",
    "inn_b",
    "inn_seller",
    "inn_buyer",
    "kpp",
    "bik",
    "rs",
}


def merge_extracted_fields(
    base: dict[str, Any] | None, update: dict[str, Any] | None
) -> dict[str, Any]:
    out = dict(base or {})
    for key, value in (update or {}).items():
        if key.startswith("_") and value is None:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and len(value) == 0:
            continue
        if key == "bank_details" and isinstance(value, dict):
            prev = out.get("bank_details") if isinstance(out.get("bank_details"), dict) else {}
            merged_bank = dict(prev)
            for bk, bv in value.items():
                if bv is None or (isinstance(bv, str) and not str(bv).strip()):
                    continue
                merged_bank[bk] = bv
            out[key] = merged_bank
            continue
        out[key] = value
    return out


def _digits_in_prompt(prompt: str) -> set[str]:
    return {m.group(0) for m in re.finditer(r"\d{8,}", prompt or "")}


def _normalize_digits(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def sanitize_business_fields(
    prompt: str, kind: str, fields: dict[str, Any] | None
) -> dict[str, Any]:
    del kind  # kind reserved for future per-type rules
    data = dict(fields or {})
    allowed_digits = _digits_in_prompt(prompt)

    for key in list(data.keys()):
        if key in _SENSITIVE_KEYS or key.startswith("inn"):
            raw = str(data.get(key) or "").strip()
            digits = _normalize_digits(raw)
            if digits and digits not in allowed_digits:
                data.pop(key, None)

    bank = data.get("bank_details")
    if isinstance(bank, dict):
        cleaned_bank: dict[str, Any] = {}
        for bk, bv in bank.items():
            text = str(bv or "").strip()
            if not text:
                continue
            if bk in {"bik", "rs", "ks", "account"}:
                digits = _normalize_digits(text)
                if digits and digits not in allowed_digits and not any(
                    digits in d or d in digits for d in allowed_digits
                ):
                    # keep only if substring match in prompt digits
                    if digits not in (prompt or "").replace(" ", ""):
                        continue
            cleaned_bank[bk] = text
        if cleaned_bank:
            data["bank_details"] = cleaned_bank
        else:
            data.pop("bank_details", None)

    return data


def extract_business_fields(
    prompt: str,
    kind: str,
    *,
    llm_call: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Returns {fields, missing, confidence, notes}.
    llm_call(system, user) -> dict JSON. Default uses llm.client _chat/_extract_json.
    """
    kind = (kind or "").strip().lower()
    text = (prompt or "").strip()
    notes = ""
    confidence = 0.0
    raw_fields: dict[str, Any] = {}

    try:
        if llm_call is None:
            from llm.client import _chat, _extract_json

            def llm_call(system: str, user: str) -> dict[str, Any]:
                return _extract_json(_chat(system, user, temperature=0.1))

        system = _system_prompt(kind)
        user = (
            f"Тип документа: {kind}\n"
            f"Текст пользователя:\n{text}\n\n"
            "Верни только JSON с полями документа. Неизвестное — null."
        )
        data = llm_call(system, user)
        if isinstance(data, dict):
            raw_fields = {
                k: v
                for k, v in data.items()
                if k not in {"confidence", "notes"} and v is not None
            }
            try:
                confidence = float(data.get("confidence") or 0.7)
            except (TypeError, ValueError):
                confidence = 0.7
            notes = str(data.get("notes") or "").strip()
        else:
            confidence = 0.0
            notes = "Модель вернула неожиданный ответ"
    except Exception as exc:  # noqa: BLE001 — soft-fail to clarify loop
        confidence = 0.0
        notes = f"extract_failed: {exc}"
        raw_fields = {}

    fields = sanitize_business_fields(text, kind, raw_fields)
    fields = apply_defaults(kind, fields)
    missing = missing_fields(kind, fields)
    return {
        "fields": fields,
        "missing": missing,
        "confidence": confidence,
        "notes": notes,
    }


def _system_prompt(kind: str) -> str:
    schemas = {
        "contract": (
            '{"party_a":str|null,"party_b":str|null,"subject":str|null,'
            '"price":str|null,"term":str|null,"inn_a":str|null,"inn_b":str|null,'
            '"address_a":str|null,"address_b":str|null,"payment_order":str|null,'
            '"acceptance_order":str|null,"confidence":float,"notes":str}'
        ),
        "act": (
            '{"party_a":str|null,"party_b":str|null,"services":[str]|null,'
            '"amount":str|null,"period":str|null,"contract_ref":str|null,'
            '"inn_a":str|null,"inn_b":str|null,"no_claims_phrase":str|null,'
            '"confidence":float,"notes":str}'
        ),
        "invoice": (
            '{"seller":str|null,"buyer":str|null,'
            '"line_items":[{"name":str,"qty":number,"price":number}]|null,'
            '"service_name":str|null,"amount":str|null,"total":number|null,'
            '"bank_details":{"bank":str|null,"bik":str|null,"rs":str|null}|null,'
            '"inn":str|null,"kpp":str|null,"payment_due":str|null,'
            '"vat_mode":"with_vat"|"no_vat"|"unknown"|null,'
            '"confidence":float,"notes":str}'
        ),
        "claim": (
            '{"addressee":str|null,"violation_summary":str|null,"demand":str|null,'
            '"reply_deadline":str|null,"contract_ref":str|null,"debt_amount":str|null,'
            '"attachments_note":str|null,"sender_sign":str|null,'
            '"confidence":float,"notes":str}'
        ),
        "letter": (
            '{"addressee":str|null,"body_purpose":str|null,"sender_sign":str|null,'
            '"subject_line":str|null,"reply_deadline":str|null,'
            '"sender_requisites":str|null,"confidence":float,"notes":str}'
        ),
    }
    schema = schemas.get(kind, schemas["letter"])
    return (
        "Ты извлекаешь поля для черновика делового документа на русском. "
        f"Верни только JSON по схеме:\n{schema}\n\n"
        "Правила:\n"
        "• Бери данные только из текста пользователя.\n"
        "• Не выдумывай ИНН, БИК, расчётный счёт, паспорт, чужие ФИО.\n"
        "• Если значения нет — null.\n"
        "• «Я» / «мы» без имени не подставляй как полное наименование стороны.\n"
        "• confidence от 0 до 1."
    )
