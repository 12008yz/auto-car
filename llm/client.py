from __future__ import annotations

import base64
import json
import re
from typing import Any

from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from rag.pipeline import Chunk, Hit

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if not LLM_API_KEY:
        raise RuntimeError(
            "Не задан LLM_API_KEY. Откройте .env и вставьте ключ OpenAI API."
        )
    if _client is None:
        kwargs: dict[str, Any] = {"api_key": LLM_API_KEY}
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _client = OpenAI(**kwargs)
    return _client


def _format_hits(hits: list[Hit]) -> str:
    parts = []
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        parts.append(
            f"[{i}] файл={c.source}; место={c.location}; score={hit.score:.3f}\n{c.text}"
        )
    return "\n\n".join(parts)


def _format_chunks(chunks: list[Chunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        parts.append(f"[{i}] файл={c.source}; место={c.location}\n{c.text}")
    return "\n\n".join(parts)


def _chat(system: str, user: str, image_bytes: bytes | None = None, temperature: float = 0.2) -> str:
    client = get_client()
    user_content: Any
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        user_content = [
            {"type": "text", "text": user},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            },
        ]
    else:
        user_content = user
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Модель не вернула JSON")
    return json.loads(text[start : end + 1])


def ask_document(question: str, hits: list[Hit]) -> dict[str, Any]:
    system = (
        "Ты помощник по документам. Отвечай только на основе фрагментов. "
        "Если данных нет — так и скажи. Ответ на русском, кратко и по делу. "
        "Верни JSON: {\"answer\": str, \"citations\": [{\"file\": str, \"location\": str, \"quote\": str}]}. "
        "quote — короткая цитата из фрагмента (до 240 символов)."
    )
    user = f"Вопрос:\n{question}\n\nФрагменты:\n{_format_hits(hits)}"
    raw = _chat(system, user)
    try:
        data = _extract_json(raw)
    except Exception:
        return {"answer": raw, "citations": []}
    data.setdefault("answer", raw)
    data.setdefault("citations", [])
    return data


def summarize_document(chunks: list[Chunk]) -> str:
    system = (
        "Сделай связное краткое содержание документа на русском: тема, ключевые факты, "
        "даты/суммы/стороны если есть. Не выдумывай."
    )
    return _chat(system, _format_chunks(chunks))


def plan_edits(
    instruction: str,
    hits: list[Hit],
    filename: str,
    is_docx: bool,
) -> dict[str, Any]:
    system = (
        "Ты редактор документов. По инструкции и фрагментам реши, как править файл. "
        "Верни только JSON:\n"
        '{"kind":"patch"|"rewrite"|"none","summary":str,'
        '"patches":[{"find":str,"replace":str}],'
        '"rewrite_text":str}\n'
        "kind=patch — точечные замены точных подстрок из документа (find должен встречаться в тексте).\n"
        "kind=rewrite — переписать смысл, rewrite_text это полный новый текст документа простым языком.\n"
        "kind=none — правки невозможны или это не запрос на правку.\n"
        "Не предлагай макросы и код. Не меняй смысл, который пользователь не просил."
    )
    extra = (
        f"Активный файл: {filename}. Это DOCX: {is_docx}.\n"
        f"Инструкция пользователя:\n{instruction}\n\nФрагменты:\n{_format_hits(hits)}"
    )
    raw = _chat(system, extra)
    try:
        data = _extract_json(raw)
    except Exception:
        return {
            "kind": "none",
            "summary": raw,
            "patches": [],
            "rewrite_text": "",
        }
    kind = str(data.get("kind") or "none").lower()
    if kind not in {"patch", "rewrite", "none"}:
        kind = "none"
    patches = []
    for item in data.get("patches") or []:
        if not isinstance(item, dict):
            continue
        find = str(item.get("find") or "").strip()
        replace = str(item.get("replace") or "")
        if find:
            patches.append({"find": find, "replace": replace})
    if not is_docx and kind in {"patch", "rewrite"}:
        kind = "none"
        data["summary"] = (
            str(data.get("summary") or "")
            + " Правки с сохранением файла сейчас только для .docx."
        ).strip()
    return {
        "kind": kind,
        "summary": str(data.get("summary") or "").strip(),
        "patches": patches,
        "rewrite_text": str(data.get("rewrite_text") or "").strip(),
    }


def looks_like_edit(text: str) -> bool:
    lowered = text.lower()
    keys = (
        "замени",
        "заменить",
        "поменяй",
        "исправ",
        "удали",
        "перепиши",
        "сократи",
        "переведи",
        "вставь",
        "правк",
        "edit",
        "replace",
        "rewrite",
    )
    return any(k in lowered for k in keys)


def looks_like_card(text: str) -> bool:
    lowered = (text or "").lower()
    if "инфографик" in lowered:
        return True
    market = any(
        k in lowered
        for k in ("wildberries", "вайлдберр", "маркетплейс")
    ) or bool(re.search(r"(^|[^\w])(wb|ozon|озон)([^\w]|$)", lowered))
    product_card = any(
        k in lowered
        for k in ("карточка товара", "карточку товара", "карточки товара")
    )
    generate = any(
        k in lowered
        for k in ("сделай", "собери", "нарисуй", "сгенери", "создай")
    )
    if product_card and (generate or market):
        return True
    if "карточ" in lowered and market and generate:
        return True
    return False


def _as_jpeg(image_bytes: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    image.thumbnail((1280, 1280))
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def make_product_card(notes: str, image_bytes: bytes | None = None) -> dict[str, Any]:
    system = (
        "Ты копирайтер карточек товара для Wildberries и Ozon. "
        "Пиши по-русски, без воды и клише вроде «премиум качество». "
        "Не выдумывай факты, которых нет в тексте или на фото: материал, бренд, размеры — только если видно или сказано. "
        "Верни JSON: "
        '{"title": str, "subtitle": str, "bullets": [str], "description": str, "keywords": str}. '
        "title — до 60 символов, цепляющий. subtitle — одно короткое уточнение. "
        "bullets — 4-5 выгод, каждая до 70 символов. "
        "description — 400-700 символов, связный текст для описания. "
        "keywords — строка через запятую, 8-15 запросов для поиска."
    )
    notes = (notes or "").strip()
    user = notes if notes else "Собери карточку по фото товара."
    jpeg = None
    if image_bytes:
        user += "\nНа фото товар. Опиши его честно и собери карточку."
        jpeg = _as_jpeg(image_bytes)
    raw = _chat(system, user, image_bytes=jpeg, temperature=0.4)
    try:
        data = _extract_json(raw)
    except Exception:
        data = {
            "title": "Карточка товара",
            "subtitle": "",
            "bullets": [],
            "description": raw,
            "keywords": "",
        }
    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [str(x).strip() for x in bullets if str(x).strip()]
    return {
        "title": str(data.get("title") or "Карточка товара").strip()[:80],
        "subtitle": str(data.get("subtitle") or "").strip()[:120],
        "bullets": bullets[:6],
        "description": str(data.get("description") or "").strip(),
        "keywords": str(data.get("keywords") or "").strip(),
    }
