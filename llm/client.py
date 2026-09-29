from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

from openai import OpenAI

from config import (
    IMAGE_MODEL,
    IMAGE_TIMEOUT,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_TIMEOUT,
)
from rag.pipeline import Chunk, Hit

log = logging.getLogger(__name__)

_client: OpenAI | None = None
_image_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if not LLM_API_KEY:
        raise RuntimeError(
            "Не задан LLM_API_KEY. Откройте .env и вставьте ключ AITunnel (sk-aitunnel-...)."
        )
    if _client is None:
        kwargs: dict[str, Any] = {
            "api_key": LLM_API_KEY,
            "timeout": LLM_TIMEOUT,
        }
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _client = OpenAI(**kwargs)
    return _client


def get_image_client() -> OpenAI:
    global _image_client
    if not LLM_API_KEY:
        raise RuntimeError(
            "Не задан LLM_API_KEY. Откройте .env и вставьте ключ AITunnel (sk-aitunnel-...)."
        )
    if _image_client is None:
        kwargs: dict[str, Any] = {
            "api_key": LLM_API_KEY,
            "timeout": IMAGE_TIMEOUT,
        }
        if LLM_BASE_URL:
            kwargs["base_url"] = LLM_BASE_URL
        _image_client = OpenAI(**kwargs)
    return _image_client


def generate_product_photo(
    product_brief: str,
    title: str = "",
    label: str = "",
    extra_prompt: str = "",
) -> bytes:
    """Студийное фото товара для карточки маркетплейса (AITunnel images/generations)."""
    client = get_image_client()
    brief = (product_brief or title or "product").strip()
    label = (label or "").strip()
    title = (title or "").strip()
    extra = (extra_prompt or "").strip()

    # Пользовательское описание — главный якорь, чтобы модель не подменяла товар
    parts = [
        "Exact e-commerce product to photograph. Match this description precisely. "
        "Do NOT replace with a different product or category.",
        f"User request: {brief}",
    ]
    if label:
        parts.append(f"Product type (Russian label): {label}")
    if title:
        parts.append(f"Product title: {title}")
    if extra and extra.lower() not in brief.lower():
        parts.append(f"Visual details: {extra}")
    parts.append(
        "Photorealistic marketplace catalog photo of THIS exact item only. "
        "Complete recognizable product, upright and level (not tilted, not skewed, straight vertical axis), "
        "3/4 angle, soft studio softbox, subtle ground shadow, "
        "isolated on transparent or seamless light grey background, sharp details, centered in frame. "
        "Forbidden: wrong category, shoes, boots, sneakers, bags, unrelated objects, "
        "tilted or rotated product, dutch angle, people, hands, text, logos, watermarks, "
        "collage, multiple items, cropped beyond recognition."
    )
    prompt = "\n".join(parts)

    attempts: list[dict[str, Any]] = [
        {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "medium",
            "response_format": "b64_json",
            "background": "transparent",
            "output_format": "png",
        },
        {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "medium",
            "response_format": "b64_json",
        },
    ]
    last_error: Exception | None = None
    for i, kwargs in enumerate(attempts, start=1):
        try:
            log.info(
                "Image gen attempt %s model=%s brief=%r",
                i,
                kwargs.get("model"),
                brief[:80],
            )
            resp = client.images.generate(**kwargs)
            if resp.data and getattr(resp.data[0], "b64_json", None):
                return base64.b64decode(resp.data[0].b64_json)
            last_error = RuntimeError("Модель не вернула b64_json")
            log.warning("Image gen attempt %s: empty b64_json", i)
        except Exception as exc:
            last_error = exc
            log.warning("Image gen attempt %s failed: %s", i, exc)
    raise RuntimeError(
        f"Не удалось сгенерировать фото товара: {last_error}"
    ) from last_error


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
    if any(k in lowered for k in ("продаю", "продажа", "для продажи", "listing")):
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
        "Ты арт-директор карточек Wildberries/Ozon. Делаешь серию из 3 слайдов: "
        "обложка (CTR), детали с выносками, преимущества. "
        "Пиши по-русски коротко и ясно, без клише («премиум», «высокое качество»). "
        "Не выдумывай факты — только из текста/фото. "
        "Верни JSON: "
        '{"label": str, "title": str, "subtitle": str, "hook": str, "size": str, '
        '"callouts": [str], "bullets": [str], "description": str, "keywords": str, '
        '"image_prompt": str, "origin": str, "accent": str}. '
        "label — тип товара ЗАГЛАВНЫМИ (СТУЛ, КРЕСЛО, МЕДВЕЖОНОК), до 14 символов. "
        "title — полное название до 55 символов, 4–8 слов; всегда дописывай фразу целиком "
        "(например «Деревянный обеденный стол на 6 персон»), без многоточия и обрезки. "
        "Не заканчивай предлогом (для/на/с/и). "
        "subtitle — короткое имя/модель для второй строки обложки, до 24 символов "
        "(например «ЧИБА»), или пусто. "
        "hook — одна короткая выгода для бейджа, до 28 символов, в одну строку. "
        "size — габарит для бейджа на обложке: «28 CM», «180×90», «Ø40» или пусто если неизвестно. "
        "callouts — ровно 3 выноски (материал/деталь/конструкция), до 22 символов, "
        "короткие (1–4 слова), полный смысл без обрезки. "
        "bullets — ровно 4 преимущества, до 40 символов, полные фразы без «…». "
        "description — 350-550 символов для описания на маркетплейсе. "
        "keywords — 8-12 фраз через запятую. "
        "image_prompt — English ONLY for the photo. MUST start with the exact product "
        "from the user request (e.g. 'green velvet armchair with metal legs and brass tips'). "
        "Never invent another category (no shoes, no boots, no unrelated items). "
        "Studio catalog shot, isolated, no text. "
        "origin — например «Сделано в России» или пусто. "
        "accent — hex мягкого цвета под товар, например #E8A598."
    )
    notes = (notes or "").strip()
    user = notes if notes else "Собери карточку по фото товара."
    jpeg = None
    if image_bytes:
        user += "\nНа фото товар. Опиши честно и собери серию слайдов под маркетплейс."
        jpeg = _as_jpeg(image_bytes)
    raw = _chat(system, user, image_bytes=jpeg, temperature=0.45)
    try:
        data = _extract_json(raw)
    except Exception:
        data = {
            "label": "",
            "title": "Карточка товара",
            "subtitle": "",
            "hook": "",
            "size": "",
            "callouts": [],
            "bullets": [],
            "description": raw,
            "keywords": "",
            "image_prompt": notes,
            "origin": "",
            "accent": "",
        }

    def _clip(s: str, n: int) -> str:
        s = (s or "").strip()
        if len(s) <= n:
            return s
        cut = s[:n].rsplit(" ", 1)[0].rstrip(" .,;:—-")
        return cut or s[:n]

    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [_clip(str(x), 40) for x in bullets if str(x).strip()]
    callouts = data.get("callouts") or []
    if not isinstance(callouts, list):
        callouts = [str(callouts)]
    callouts = [_clip(str(x), 22) for x in callouts if str(x).strip()]
    title = _clip(str(data.get("title") or "Карточка товара"), 55)
    image_prompt = str(data.get("image_prompt") or notes or title).strip()
    accent = str(data.get("accent") or "").strip()
    if accent and not accent.startswith("#"):
        accent = "#" + accent
    hook = str(data.get("hook") or data.get("subtitle") or "").strip()
    size = str(data.get("size") or "").strip().upper()
    return {
        "label": str(data.get("label") or "").strip()[:16],
        "title": title,
        "subtitle": _clip(str(data.get("subtitle") or ""), 24),
        "hook": _clip(hook, 28),
        "size": _clip(size, 16),
        "callouts": callouts[:3],
        "bullets": bullets[:4],
        "description": str(data.get("description") or "").strip(),
        "keywords": str(data.get("keywords") or "").strip(),
        "image_prompt": image_prompt[:400],
        "origin": str(data.get("origin") or "").strip()[:40],
        "accent": accent[:7],
    }
