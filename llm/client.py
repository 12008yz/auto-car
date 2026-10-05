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
    reference_image: bytes | None = None,
    style: str = "catalog",
    scene: str = "",
) -> bytes:
    """
    Студийное / lifestyle фото товара (AITunnel images).

    style=catalog|editorial — изолированный каталожный кадр.
    style=lifestyle — товар в типичной сцене (подоконник, стол и т.д.).
    """
    client = get_image_client()
    brief = (product_brief or title or "product").strip()
    label = (label or "").strip()
    title = (title or "").strip()
    extra = (extra_prompt or "").strip()
    style = (style or "catalog").strip().lower()
    scene = (scene or "").strip()
    has_ref = bool(reference_image)
    lifestyle = style == "lifestyle"

    if lifestyle:
        scene_line = scene or "in a realistic everyday setting that fits this product"
        if has_ref:
            parts = [
                "Recreate THIS exact product from the reference photo inside a realistic lifestyle scene. "
                "Keep the same product category, shape, colors, materials and surface artwork/print.",
                f"User request: {brief}",
                f"Place the product naturally: {scene_line}.",
            ]
        else:
            parts = [
                "Photorealistic lifestyle product photo for an e-commerce marketplace. "
                "Match the product description precisely. Do NOT replace with another category.",
                f"User request: {brief}",
                f"Place the product naturally: {scene_line}.",
            ]
        if label:
            parts.append(f"Product category hint (do NOT print as caption): {label}")
        if title:
            parts.append(f"Product identity hint (do NOT print as caption): {title}")
        if extra and extra.lower() not in brief.lower():
            parts.append(f"Visual details: {extra}")
        parts.append(
            "Show the COMPLETE product fully in frame — top to bottom, whole silhouette visible, "
            "not a tight crop, not a macro close-up of a fragment. "
            "Leave breathing room around the item; product should fill about 45–60% of the frame height. "
            "Camera pulled back enough to see the entire object and its supporting surface. "
            "Believable environment, soft natural light. "
            "Keep product-surface artwork that belongs to the item. "
            "Do NOT add marketing captions, badges, watermarks, UI, posters or logos around the product. "
            "No people, no hands holding the product, no collage, no multiple unrelated items. "
            "Forbidden: extreme close-up, cropped rim/base/handle, wrong category."
        )
    else:
        if has_ref:
            parts = [
                "Recreate THIS exact product from the reference photo as a clean e-commerce "
                "studio catalog shot. Keep the same product category, shape, colors, materials, "
                "and any artwork/print that is already on the product surface "
                "(including decorative lettering that is part of the design).",
                f"User request: {brief}",
            ]
        else:
            parts = [
                "Exact e-commerce product to photograph. Match this description precisely. "
                "Do NOT replace with a different product or category.",
                f"User request: {brief}",
            ]
        if label:
            parts.append(f"Product category hint (do NOT print this as a caption): {label}")
        if title:
            parts.append(f"Product identity hint (do NOT print this as a caption): {title}")
        if extra and extra.lower() not in brief.lower():
            parts.append(f"Visual details: {extra}")
        parts.append(
            "Photorealistic marketplace catalog photo of THIS exact item only. "
            "Complete recognizable product, upright and level (not tilted, not skewed, straight vertical axis), "
            "3/4 angle, soft studio softbox, subtle ground shadow, "
            "isolated on transparent or seamless light grey / white background, sharp details, centered in frame. "
            "Remove room, table, walls, clutter, people, hands. "
            "Keep product-surface artwork that belongs to the item. "
            "Do NOT add new titles, badges, watermarks, UI, posters, or marketing captions around the product. "
            "If packaging/book cover has no real branding in the request: blank plain surface. "
            "Forbidden: wrong category, shoes, boots, sneakers, bags, unrelated objects, "
            "tilted product, dutch angle, collage, multiple items, cropped beyond recognition."
        )
    prompt = "\n".join(parts)

    last_error: Exception | None = None

    # 1) Image-to-image edit when we have a user reference
    if has_ref and reference_image is not None:
        try:
            from io import BytesIO

            png = _as_png(reference_image)
            edit_attempts: list[dict[str, Any]] = [
                {
                    "model": IMAGE_MODEL,
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                    "quality": "medium",
                    "response_format": "b64_json",
                    "background": "opaque" if lifestyle else "transparent",
                    "output_format": "png",
                    "input_fidelity": "high",
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
            for i, kwargs in enumerate(edit_attempts, start=1):
                try:
                    buf = BytesIO(png)
                    buf.name = "reference.png"
                    call = dict(kwargs)
                    call["image"] = buf
                    log.info(
                        "Image edit attempt %s model=%s style=%s brief=%r",
                        i,
                        call.get("model"),
                        style,
                        brief[:80],
                    )
                    resp = client.images.edit(**call)
                    if resp.data and getattr(resp.data[0], "b64_json", None):
                        return base64.b64decode(resp.data[0].b64_json)
                    last_error = RuntimeError("Модель не вернула b64_json (edit)")
                    log.warning("Image edit attempt %s: empty b64_json", i)
                except Exception as exc:
                    last_error = exc
                    log.warning("Image edit attempt %s failed: %s", i, exc)
            log.warning("Image edit unavailable, falling back to text generate")
        except Exception as exc:
            last_error = exc
            log.warning("Image edit prep failed: %s", exc)

    # 2) Text-only generate (no photo, or edit failed)
    attempts: list[dict[str, Any]] = [
        {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "quality": "medium",
            "response_format": "b64_json",
            "background": "opaque" if lifestyle else "transparent",
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
    for i, kwargs in enumerate(attempts, start=1):
        try:
            log.info(
                "Image gen attempt %s model=%s style=%s brief=%r ref=%s",
                i,
                kwargs.get("model"),
                style,
                brief[:80],
                has_ref,
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


def _as_png(image_bytes: bytes) -> bytes:
    from io import BytesIO

    from PIL import Image

    image = Image.open(BytesIO(image_bytes))
    if image.mode not in {"RGBA", "RGB"}:
        image = image.convert("RGBA")
    elif image.mode == "RGB":
        image = image.convert("RGBA")
    image.thumbnail((1536, 1536))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


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
        "Ты помощник по документам для обычного пользователя в Telegram.\n"
        "Отвечай ТОЛЬКО на основе фрагментов. Если данных нет — скажи просто и честно.\n"
        "Стиль ответа:\n"
        "• простой русский язык, без канцелярита;\n"
        "• 2–5 коротких предложений или короткий список;\n"
        "• НЕ пиши «фрагмент 1/2/3», «абзац N», «предоставленные фрагменты»;\n"
        "• говори о содержании файла по-человечески (что совпадает, что отличается, что важно);\n"
        "• если файлов несколько — назови их коротко по имени, без путей.\n"
        "Верни JSON: {\"answer\": str, \"citations\": [{\"file\": str, \"location\": str, \"quote\": str}]}.\n"
        "citations — до 3 штук, quote до 120 символов; это внутренняя подсказка, "
        "в answer их не повторяй."
    )
    user = f"Вопрос пользователя:\n{question}\n\nФрагменты из файлов:\n{_format_hits(hits)}"
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
        "даты/суммы/стороны если есть. Не выдумывай. "
        "Если это бланк/декларация — кратко перечисли основные блоки и пустые поля."
    )
    return _chat(system, _format_chunks(chunks))


def check_document_gaps(chunks: list[Chunk]) -> str:
    """Что не заполнено / какие плейсхолдеры остались."""
    system = (
        "Просмотри фрагменты бланка/документа. Перечисли на русском:\n"
        "1) пустые поля и плейсхолдеры вроде [ФИО], [ИНН], [ГГГГ], ____ ;\n"
        "2) что уже заполнено (кратко);\n"
        "3) что логично попросить у пользователя следующим сообщением.\n"
        "Не выдумывай полей, которых нет во фрагментах. Кратко, списком."
    )
    return _chat(system, _format_chunks(chunks), temperature=0.2)


def analyze_contract_risks(chunks: list[Chunk]) -> str:
    """Типовая проверка договора: риски и спорные места."""
    system = (
        "Ты помощник по разбору договоров (не юрист, не консультация). "
        "По фрагментам составь на русском структурированный разбор:\n"
        "1) Стороны и предмет (если видно)\n"
        "2) Сроки и этапы\n"
        "3) Оплата / аванс / НДС\n"
        "4) Приёмка / акты\n"
        "5) Ответственность / штрафы / неустойка\n"
        "6) Расторжение / односторонний отказ\n"
        "7) Подсудность / претензионный порядок\n"
        "8) Что опасно или неясн о — список рисков с цитатой/отсылкой к фрагменту\n"
        "9) Чего не хватает в тексте (если заметно)\n"
        "Не выдумывай пунктов, которых нет во фрагментах. "
        "В конце одной строкой: «Это черновик разбора, не юридическая консультация»."
    )
    return _chat(system, _format_chunks(chunks), temperature=0.25)


def extract_key_facts(chunks: list[Chunk]) -> str:
    """Выжимка фактов: стороны, даты, суммы, обязательства."""
    system = (
        "Извлеки из фрагментов ключевые факты на русском в виде компактных списков:\n"
        "• Стороны / ФИО / организации\n"
        "• Даты и сроки\n"
        "• Суммы / валюта / порядок оплаты\n"
        "• Адреса / реквизиты (если есть)\n"
        "• Главные обязательства\n"
        "Формат: маркированные списки. Если данных нет — «не указано». Не выдумывай."
    )
    return _chat(system, _format_chunks(chunks), temperature=0.2)


def compare_documents(
    chunks_a: list[Chunk],
    chunks_b: list[Chunk],
    name_a: str,
    name_b: str,
) -> str:
    """Сравнение двух документов."""
    system = (
        "Сравни два документа простым русским языком для обычного пользователя.\n"
        "Структура ответа:\n"
        "1) В двух словах — о чём оба файла\n"
        "2) Чем похожи\n"
        "3) Чем отличаются (сроки, суммы, стороны, обязанности, штрафы) — коротко\n"
        "4) На что обратить внимание\n"
        "Не пиши «фрагмент N», «абзац N». Называй файлы по имени. "
        "Не выдумывай. Если данных мало — скажи, что сравнение частичное."
    )
    user = (
        f"=== Документ A: {name_a} ===\n{_format_chunks(chunks_a)}\n\n"
        f"=== Документ B: {name_b} ===\n{_format_chunks(chunks_b)}"
    )
    return _chat(system, user, temperature=0.25)


def format_by_sample(content_chunks: list[Chunk], sample_chunks: list[Chunk]) -> dict[str, Any]:
    """
    Оформить содержание «как в образце» — новый структурированный документ.
    """
    system = (
        "Нужно оформить СОДЕРЖАНИЕ первого документа в стиле/структуре ОБРАЗЦА "
        "(второй). Верни JSON:\n"
        '{"title":str,"doc_type":str,"filename_stem":str,'
        '"sections":[{"heading":str,"paragraphs":[str]}]}\n'
        "Сохраняй факты и смысл из содержания, не копируй чужие ФИО/реквизиты из образца "
        "если их нет в содержании. Структура (заголовки, блоки) — ближе к образцу. "
        "Пиши по-русски."
    )
    user = (
        f"=== СОДЕРЖАНИЕ ===\n{_format_chunks(content_chunks)}\n\n"
        f"=== ОБРАЗЕЦ ОФОРМЛЕНИЯ ===\n{_format_chunks(sample_chunks)}"
    )
    raw = _chat(system, user, temperature=0.35)
    try:
        data = _extract_json(raw)
    except Exception as exc:
        raise RuntimeError(f"Не разобрать ответ модели: {exc}") from exc
    title = str(data.get("title") or "Документ по образцу").strip()[:200]
    sections_raw = data.get("sections") or []
    sections: list[dict[str, Any]] = []
    if isinstance(sections_raw, list):
        for item in sections_raw:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading") or "").strip()[:120]
            paras_in = item.get("paragraphs") or []
            if isinstance(paras_in, str):
                paras_in = [paras_in]
            paragraphs = [str(p).strip() for p in paras_in if str(p).strip()]
            if heading or paragraphs:
                sections.append({"heading": heading, "paragraphs": paragraphs})
    if not sections:
        raise RuntimeError("Модель вернула пустой документ")
    return {
        "title": title,
        "doc_type": str(data.get("doc_type") or "документ").strip()[:40],
        "filename_stem": str(data.get("filename_stem") or title).strip()[:80],
        "sections": sections,
        "mode": "text",
    }


def _extract_person_facts(instruction: str) -> dict[str, str]:
    """Достаёт ФИО / дату / год / город из свободной фразы пользователя."""
    text = (instruction or "").strip()
    lowered = text.lower()
    facts: dict[str, str] = {}

    date_m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text)
    if date_m:
        d, m, y = date_m.group(1), date_m.group(2), date_m.group(3)
        if len(y) == 2:
            y = "19" + y if int(y) > 30 else "20" + y
        facts["birth_date"] = f"{int(d):02d}.{int(m):02d}.{y}"
        facts["birth_year"] = y

    # ФИО: после маркеров или три слова подряд с заглавной / просто три кириллических
    fio_m = re.search(
        r"(?:фио|данные|про|я)\s*[:\-]?\s*"
        r"([А-ЯЁа-яё]{2,}\s+[А-ЯЁа-яё]{2,}(?:\s+[А-ЯЁа-яё]{2,})?)",
        text,
        flags=re.IGNORECASE,
    )
    if not fio_m:
        fio_m = re.search(
            r"\b([А-ЯЁа-яё]{2,}\s+[А-ЯЁа-яё]{2,}\s+[А-ЯЁа-яё]{2,})\b",
            text,
        )
    if fio_m:
        facts["fio"] = " ".join(fio_m.group(1).split())

    city_m = re.search(
        r"(?<![А-ЯЁа-яё])(?:город|г\.)\s*([А-ЯЁа-яё\-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if city_m:
        facts["city"] = city_m.group(1).strip()
    else:
        # хвост после даты: «… 27.11.1999 года Богородицк»
        tail = re.search(
            r"\d{1,2}[./]\d{1,2}[./]\d{2,4}\s*(?:года(?:\s+рождения)?)?\s*"
            r"([А-ЯЁа-яё]{3,})\s*$",
            text,
            flags=re.IGNORECASE,
        )
        if tail:
            cand = tail.group(1).strip()
            if cand.lower() not in {"года", "город", "рождения", "др"}:
                facts["city"] = cand

    # Одно слово-город: «Богородицк» или «город/адрес: Богородицк»
    if "city" not in facts:
        labeled = re.search(
            r"(?:город/адрес|город|адрес|значение пользователя)\s*:\s*"
            r"([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{2,40})",
            text,
            flags=re.IGNORECASE,
        )
        if labeled:
            facts["city"] = labeled.group(1).strip()
    if "city" not in facts:
        bare = text.strip()
        if re.fullmatch(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z\-]{2,40}", bare):
            if bare.lower() not in {
                "да",
                "нет",
                "ок",
                "окей",
                "привет",
                "здравствуйте",
                "отмена",
            }:
                facts["city"] = bare

    if "birth_date" in facts and "года" in lowered and "рожден" in lowered:
        facts["date_role"] = "birth"
    elif "birth_date" in facts:
        # по умолчанию ДД.ММ.ГГГГ в личных данных = дата рождения, не отчётный год
        facts["date_role"] = "birth"

    return facts


def _sanitize_fill_patches(
    patches: list[dict[str, str]],
    facts: dict[str, str],
    hits_text: str,
) -> list[dict[str, str]]:
    """Убирает типичные ошибки: год рождения → «Год:/[ГГГГ]» отчётности."""
    if not patches:
        return patches
    birth_year = facts.get("birth_year") or ""
    birth_date = facts.get("birth_date") or ""
    city = facts.get("city") or ""
    fio = facts.get("fio") or ""
    hits_l = (hits_text or "").lower()

    cleaned: list[dict[str, str]] = []
    for p in patches:
        find = str(p.get("find") or "")
        replace = str(p.get("replace") or "")
        find_l = find.lower()
        # Не подставлять год рождения в поле отчётного/налогового года
        if birth_year and replace.strip() == birth_year:
            looks_report_year = any(
                k in find_l
                for k in (
                    "[гггг]",
                    "отчётн",
                    "отчетн",
                    "налогов",
                    "период",
                    "код периода",
                )
            ) or (
                find_l.strip() in {"год:", "год", "год: [гггг]", "год:[гггг]"}
                or find_l.startswith("год:")
            )
            looks_birth = any(
                k in find_l for k in ("рожден", "дата рожд", "др", "birth")
            )
            if looks_report_year and not looks_birth and facts.get("date_role") == "birth":
                continue
        cleaned.append({"find": find, "replace": replace})

    # Если есть полная дата рождения и в тексте есть подходящий плейсхолдер — добавим патч
    def _has_replace(val: str) -> bool:
        return any(str(x.get("replace") or "").strip() == val for x in cleaned)

    if birth_date and not _has_replace(birth_date):
        for needle in (
            "[ДД.ММ.ГГГГ]",
            "[дд.мм.гггг]",
            "Дата рождения: [ДД.ММ.ГГГГ]",
            "Дата рождения: [дд.мм.гггг]",
            "Дата рождения:",
        ):
            if needle.lower() in hits_l or needle in (hits_text or ""):
                # ищем точное вхождение в hits_text
                for raw_line in (hits_text or "").splitlines():
                    if "рожден" in raw_line.lower() or "[дд.мм.гггг]" in raw_line.lower():
                        # предпочитаем плейсхолдер в строке
                        m = re.search(r"\[ДД\.ММ\.ГГГГ\]|\[дд\.мм\.гггг\]", raw_line)
                        if m:
                            cleaned.append(
                                {"find": m.group(0), "replace": birth_date}
                            )
                            break
                        if ":" in raw_line and len(raw_line.strip()) < 80:
                            cleaned.append(
                                {
                                    "find": raw_line.strip()[:120],
                                    "replace": f"Дата рождения: {birth_date}",
                                }
                            )
                            break
                break

    if city and not _has_replace(city):
        for token in ("[адрес]", "[город]", "город:", "адрес:"):
            if token in hits_l:
                # найдём точный кусок
                for raw_line in (hits_text or "").splitlines():
                    rl = raw_line.lower()
                    if token.strip(":") in rl and ("[" in raw_line or raw_line.strip().endswith(":")):
                        if "[адрес]" in raw_line:
                            cleaned.append({"find": "[адрес]", "replace": city})
                        elif "[город]" in raw_line:
                            cleaned.append({"find": "[город]", "replace": city})
                        elif "город" in rl and ":" in raw_line:
                            cleaned.append(
                                {
                                    "find": raw_line.strip()[:120],
                                    "replace": f"Город: {city}",
                                }
                            )
                        break
                break

    if fio and not any(fio in str(x.get("replace") or "") for x in cleaned):
        if "[фио]" in hits_l or "[ФИО]" in (hits_text or ""):
            cleaned.append({"find": "[ФИО]", "replace": fio})

    # дедуп по find
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for p in cleaned:
        key = str(p.get("find") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def plan_edits(
    instruction: str,
    hits: list[Hit],
    filename: str,
    is_docx: bool,
    mode: str = "auto",
) -> dict[str, Any]:
    mode = (mode or "auto").strip().lower()
    instr_l = (instruction or "").lower()
    is_transform = looks_like_reverse_words(instr_l)
    is_fill = (not is_transform) and (
        mode == "fill"
        or any(
            k in instr_l
            for k in (
                "добав",
                "заполни",
                "вставь данн",
                "подставь",
                "внеси данн",
                "фио",
                "мои данн",
                "эти данн",
                "данные про",
                "в файле нужно",
            )
        )
    )
    is_tone = mode == "tone" or looks_like_tone(instr_l)
    facts = _extract_person_facts(instruction) if is_fill else {}
    system = (
        "Ты аккуратный редактор Word-документов. Задача — выполнить инструкцию "
        "пользователя, НЕ портя структуру и смысл файла.\n"
        "Верни только JSON:\n"
        '{"kind":"patch"|"rewrite"|"none","summary":str,'
        '"patches":[{"find":str,"replace":str}],'
        '"rewrite_text":str}\n\n'
        "Правила выбора kind:\n"
        "• patch — почти всегда, если можно точечно заменить/вставить/удалить фрагменты.\n"
        "• rewrite — ТОЛЬКО если пользователь явно просит переписать весь документ "
        "или точечные замены невозможны.\n"
        "• none — это не правка, данных мало, или риск испортить файл.\n\n"
        "Правила для patch (критично):\n"
        "1) find — ТОЧНАЯ непрерывная подстрока из фрагментов документа (копируй как есть).\n"
        "2) find ≥ 6 символов, уникальный кусок; не используй одиночные слова вроде «и», «в».\n"
        "3) Меняй только то, что просили. Не перефразируй соседний текст.\n"
        "4) Не ломай нумерацию, даты, ФИО, суммы, если их не просили менять.\n"
        "5) Лучше 1–12 точных патчей, чем переписывание всего файла.\n"
        "6) replace может быть пустым, если нужно удалить фрагмент.\n"
        "7) Не предлагай макросы, код, HTML.\n"
        "rewrite_text — полный новый текст простым языком, абзацы через \\n, "
        "только при kind=rewrite."
    )
    if is_fill:
        system += (
            "\n\nРЕЖИМ ЗАПОЛНЕНИЯ БЛАНКА (сейчас активен):\n"
            "Пользователь даёт ЛИЧНЫЕ данные, чтобы подставить в поля бланка.\n"
            "КРИТИЧНО — различай поля:\n"
            "• Дата рождения (27.11.1999) → только поля «дата рождения», [ДД.ММ.ГГГГ], "
            "«др». Подставляй ПОЛНУЮ дату 27.11.1999, НЕ одно «1999».\n"
            "• Отчётный / налоговый год [ГГГГ], «Год:», «налоговый период» — "
            "НЕ заполняй годом рождения. Туда год рождения НЕ ставить, "
            "если пользователь не сказал явно «отчётный год …».\n"
            "• ФИО → [ФИО] / строка «ФИО:» целиком (Фамилия Имя Отчество).\n"
            "• Город / адрес → [город], [адрес], «Город:», «Адрес:».\n"
            "Сделай отдельный патч на каждое данное из разобранных фактов, "
            "если в фрагментах есть подходящее поле.\n"
            "find = точный текст/плейсхолдер из фрагментов; replace = значение.\n"
            "summary: перечисли «поле → значение»."
        )
    if is_tone:
        system += (
            "\n\nРЕЖИМ СМЕНЫ ТОНА / СОКРАЩЕНИЯ:\n"
            "Пользователь просит изменить стиль текста файла (короче / официальнее / проще).\n"
            "• Предпочти kind=rewrite с полным новым текстом того же смысла, "
            "если правок много; иначе точечные patch по абзацам.\n"
            "• Не меняй факты, даты, суммы, ФИО, если не просили.\n"
            "• Сохрани деловой смысл документа."
        )
    facts_block = ""
    if facts:
        facts_block = (
            "\nРазобранные факты (обязательно учесть корректно):\n"
            + "\n".join(f"- {k}: {v}" for k, v in facts.items())
            + "\n"
        )
    hits_blob = _format_hits(hits)
    extra = (
        f"Активный файл: {filename}. Это DOCX: {is_docx}. "
        f"Режим: {'заполнение' if is_fill else 'тон' if is_tone else 'правка'}.\n"
        f"Инструкция пользователя:\n{instruction}\n"
        f"{facts_block}\n"
        f"Фрагменты:\n{hits_blob}"
    )
    raw = _chat(system, extra, temperature=0.15)
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
        if find and len(find) >= 3:
            patches.append({"find": find, "replace": replace})
    if is_fill and patches:
        patches = _sanitize_fill_patches(patches, facts, hits_blob)
    wants_full = is_transform or any(
        k in instr_l
        for k in (
            "перепиши весь",
            "переписать весь",
            "заново",
            "с нуля",
            "полный текст",
            "все слова",
            "каждое слово",
        )
    )
    if is_transform:
        kind = "rewrite"
    elif kind == "rewrite" and patches and not wants_full:
        kind = "patch"
    if kind == "patch" and not patches:
        kind = "none"
        data["summary"] = (
            str(data.get("summary") or "")
            + " Не удалось найти точные фрагменты для безопасной замены."
        ).strip()
    if not is_docx and kind in {"patch", "rewrite"}:
        kind = "none"
        data["summary"] = (
            str(data.get("summary") or "")
            + " Правки с сохранением файла сейчас только для .docx."
        ).strip()

    # Если модель молчала, а факты есть — соберём summary сами
    summary = str(data.get("summary") or "").strip()
    if is_fill and patches and facts:
        mapped = "; ".join(f"«{p['find']}» → «{p['replace']}»" for p in patches[:6])
        summary = (summary + "\n" if summary else "") + f"Подстановки: {mapped}"
        summary = summary.strip()

    return {
        "kind": kind,
        "summary": summary,
        "patches": patches[:12],
        "rewrite_text": str(data.get("rewrite_text") or "").strip(),
        "mode": "fill" if is_fill else "tone" if is_tone else "edit",
        "facts": facts,
    }


def looks_like_edit(text: str) -> bool:
    lowered = text.lower()
    keys = (
        "замени",
        "заменить",
        "поменяй",
        "исправ",
        "удали ",
        "удалить",
        "перепиши",
        "переписать",
        "переверн",
        "переверт",
        "задом наперёд",
        "задом наперед",
        "наоборот",
        "сократи",
        "переведи",
        "вставь",
        "встав ",  # опечатка «Встав туда»
        "добав",  # добавить / добавь данные в файл
        "отредактируй",
        "заполни",
        "подставь",
        "внеси ",
        "впиши",
        "пропиши",
        "поставь фио",
        "укажи фио",
        "сделай фио",
        "смени фио",
        "edit",
        "replace",
        "rewrite",
    )
    if any(k in lowered for k in keys):
        return True
    # «правка/правки», но не «справка»
    return bool(re.search(r"(?<![а-яё])правк", lowered))


def looks_like_reverse_words(text: str) -> bool:
    """«Переверни все слова задом наперёд» — правка файла, не заполнение бланка."""
    lowered = (text or "").lower()
    if any(
        k in lowered
        for k in (
            "задом наперёд",
            "задом наперед",
            "задом-наперёд",
            "задом-наперед",
            "слова наоборот",
            "наоборот слова",
            "перевёрнут",
            "перевернут",
        )
    ):
        return True
    if any(k in lowered for k in ("переверн", "переверт")) and any(
        k in lowered for k in ("слов", "текст", "файл", "документ", "всё", "все")
    ):
        return True
    return False


def looks_like_apply_pending(text: str) -> bool:
    """Пользователь просит отдать уже подготовленный файл, а не новую правку."""
    # Не путать с transform «переверни и скинь» — это новая операция
    if looks_like_reverse_words(text):
        return False
    lowered = re.sub(r"[.!?…]+$", "", (text or "").strip().lower()).strip()
    if not lowered:
        return False
    if lowered in {
        "да",
        "давай",
        "ок",
        "окей",
        "ok",
        "okay",
        "yes",
        "ага",
        "угу",
        "примени",
        "применить",
        "скинь",
        "отправь",
        "пришли",
        "вышли",
        "дай",
        "давай файл",
        "дай файл",
        "скинь файл",
        "пришли файл",
    }:
        return True
    if looks_like_fill_data(lowered):
        return False
    # «год должен быть» / точечная новая правка — не подтверждение
    if any(
        k in lowered
        for k in (
            "замени",
            "добав",
            "заполни",
            "год должен",
            "исправ",
            "убери",
            "удали",
            "переверн",
            "переверт",
            "задом",
            "заглавн",
            "регистр",
        )
    ):
        return False
    return any(
        k in lowered
        for k in (
            "дай файл",
            "файл дай",
            "мне файл",
            "файл мне",
            "скинь файл",
            "скинь его",
            "скинь мне",
            "скинуть",
            "пришли файл",
            "отправь файл",
            "вышли файл",
            "дай его",
            "примени изменен",
            "готовый файл",
        )
    )


def looks_like_fill_data(text: str) -> bool:
    """Заполнение бланка своими ФИО/датой/городом — не карточка и не «поиск по файлу»."""
    lowered = (text or "").lower()
    if any(
        k in lowered
        for k in (
            "вставь данн",
            "встав данн",
            "добав данн",
            "добавить данн",
            "добавь данн",
            "нужно добав",
            "надо добав",
            "заполни данн",
            "подставь данн",
            "внеси данн",
            "мои данные",
            "эти данные",
            "заполни бланк",
            "заполни форм",
            "заполни поле",
            "вставь туда",
            "встав туда",
            "вставь их",
            "встав их",
            "их туда",
            "подставь туда",
            "заполни туда",
            "добавь туда",
            "добавить в файл",
            "добавь в файл",
            "в файле нужно",
            "в файле надо",
            "сделай фио",
            "поставь фио",
            "укажи фио",
            "смени фио",
            "фио:",
            "ф.и.о",
        )
    ):
        return True

    has_add = any(
        k in lowered
        for k in (
            "добав",
            "вставь",
            "встав ",
            "заполни",
            "подставь",
            "внеси",
            "впиши",
            "пропиши",
        )
    )
    has_data_word = any(k in lowered for k in ("данн", "фио", "ф.и.о", "реквизит"))
    if has_add and has_data_word:
        return True
    if ("в файле" in lowered or "в документ" in lowered) and has_add:
        return True

    # «фио Чикасов Денис…» — не «верно ли ФИО?» / «проверить ФИО»
    if re.search(r"\bфио\b", lowered) and re.search(
        r"[а-яё]{2,}\s+[а-яё]{2,}", lowered
    ):
        questionish = "?" in (text or "") or any(
            k in lowered
            for k in (
                "верно ли",
                "правильн ли",
                "проверь",
                "проверить",
                "можно провер",
                "какой",
                "какое",
                "какие",
            )
        )
        if not questionish:
            return True

    has_date = bool(re.search(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", lowered))
    name_triple = bool(re.search(r"[а-яё]{3,}\s+[а-яё]{3,}\s+[а-яё]{3,}", lowered))
    fio_hint = bool(re.search(r"\bя\s+[а-яё]{2,}\s+[а-яё]{2,}", lowered))
    place_hint = any(k in lowered for k in ("город", "г. ", " прожива"))
    birth_hint = any(k in lowered for k in ("года", "г.р", "родил", "дата рожд"))
    if fio_hint and (has_date or birth_hint or place_hint):
        return True
    # «…про Чикасова Дениса Владимировича 27.11.1999 …»
    if has_add and name_triple and (has_date or place_hint or birth_hint):
        return True
    if has_data_word and name_triple and has_date:
        return True
    return False


def looks_like_chitchat(text: str) -> bool:
    """Приветствия / small-talk без документной задачи."""
    raw = (text or "").strip()
    if not raw or len(raw) > 80:
        return False
    lowered = raw.lower()
    if any(
        k in lowered
        for k in (
            "замени",
            "добав",
            "заполни",
            "вставь",
            "напиши",
            "создай",
            "реферат",
            "договор",
            "файл",
            "бланк",
            "карточк",
            "проверь",
            "сравни",
        )
    ):
        return False
    greetings = (
        "привет",
        "здравствуй",
        "добрый день",
        "доброе утро",
        "добрый вечер",
        "хай",
        "hello",
        "hi ",
        "hi!",
        "yo ",
        "браток",
        "здарова",
        "салют",
        "как дела",
        "что умеешь",
        "кто ты",
    )
    if any(g in lowered for g in greetings):
        return True
    if lowered in {"привет", "хай", "hello", "hi", "здарова", "салют"}:
        return True
    return False


def looks_like_short_gap_value(text: str) -> bool:
    """Короткий ответ на «укажите адрес/город» после проверки пустых полей."""
    raw = (text or "").strip()
    if not raw or len(raw) > 70:
        return False
    lowered = raw.lower()
    if looks_like_chitchat(raw) or looks_like_apply_pending(raw):
        return False
    if any(
        k in lowered
        for k in (
            "?",
            "что ",
            "как ",
            "почему",
            "замени",
            "переверн",
            "напиши",
            "создай",
        )
    ):
        return False
    if looks_like_edit(lowered) and not any(
        k in lowered for k in ("вставь", "добав", "заполни", "подставь")
    ):
        # чистое значение без глагола правки
        pass
    words = raw.split()
    if len(words) > 6:
        return False
    return bool(re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-\s.,/]{0,68}", raw))


def looks_like_tone(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        k in lowered
        for k in (
            "сделай короче",
            "сделать короче",
            "сократи текст",
            "сократи документ",
            "официальнее",
            "более официальн",
            "проще язык",
            "упрости язык",
            "упрости текст",
            "дружелюбнее",
            "смени тон",
            "измени тон",
            "перепиши официальн",
            "перепиши коротк",
            "тон письма",
            "сделай проще",
            "сделать проще",
        )
    )


def looks_like_write(text: str) -> bool:
    """Создание документа без уточнения (бланк или текст)."""
    intent = classify_document_intent(text, has_files=False)
    return intent["intent"] in {"write_form", "write_text"}


_STRONG_CREATE = (
    "напиши",
    "написать",
    "написал",
    "создай",
    "создать",
    "создал",
    "составь",
    "составить",
    "составил",
    "сгенерируй",
    "подготовь",
    "сформируй",
    "оформи документ",
    "оформи файл",
    "сделай бланк",
    "сделай файл",
    "сделай документ",
    "сделай реферат",
    "сделай письмо",
    "сделай претензи",
    "новый документ",
    "новый файл",
)

_SOFT_NEED = ("нужно", "нужна", "нужен", "надо", "хочу", "можно", "помоги", "пожалуйста")

_DOC_QUESTION = (
    "проверь информац",
    "проверить информац",
    "проверь данн",
    "проверить данн",
    "проверь фио",
    "проверь что в",
    "посмотри что",
    "посмотри внутри",
    "что там внутри",
    "что внутри",
    "что в файл",
    "что в договор",
    "что в документ",
    "что в текст",
    "расскажи что в",
    "объясни что в",
    "информация о человек",
    "информацию о человек",
    "о человеке",
    "данные о человек",
    "верно ли",
    "правильн ли",
    "сверь данн",
    "сверь фио",
    "найди в файл",
    "есть ли в договор",
    "есть ли в документ",
    "есть ли в файл",
    "какой срок",
    "какая сумм",
    "кто сторон",
    "мне нужно что бы ты проверил",
    "мне нужно чтобы ты проверил",
    "нужно чтобы ты проверил",
    "нужно что бы ты проверил",
)


def has_strong_create(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in _STRONG_CREATE)


def looks_like_doc_question(text: str) -> bool:
    """Вопрос/проверка по уже загруженному документу (не создание)."""
    lowered = (text or "").lower()
    if any(k in lowered for k in _DOC_QUESTION):
        return True
    if any(k in lowered for k in ("где указан", "где написан", "указан ли", "написано ли")):
        return True
    # «проверь …» без явного создания документа
    if ("проверь" in lowered or "проверить" in lowered) and not has_strong_create(lowered):
        if not any(k in lowered for k in ("риск", "бланк", "поля")):
            return True
    return False


def _lazy_detect_transform(text: str) -> str | None:
    """Ленивый импорт, чтобы не замкнуть llm.client ↔ edit.transforms."""
    from edit.transforms import detect_transform

    return detect_transform(text)


_FACTS_FOLLOWUP_KEYS = (
    "добав",
    "эти данн",
    "вставь",
    "встав ",
    "заполни",
    "фио",
    "подставь",
    "туда",
)


def apply_document_routing_guards(
    decision: dict[str, Any],
    *,
    text: str,
    has_files: bool,
    has_facts: bool = False,
    awaiting_gap_fill: bool = False,
) -> dict[str, Any]:
    """
    Страховка от типичных косяков роутера.
    Главное правило: если файлы уже в чате — почти никогда не спрашиваем
    «бланк или текст?»; по умолчанию работаем с загруженным документом.
    Единственная точка правок intent — здесь (не в on_text).
    """
    intent = str(decision.get("intent") or "none")
    family = str(decision.get("family") or "")
    mode = str(decision.get("mode") or "")
    lowered = (text or "").lower()
    strong = has_strong_create(lowered)
    soft = any(k in lowered for k in _SOFT_NEED)
    questionish = looks_like_doc_question(lowered)
    fillish = looks_like_fill_data(lowered) or looks_like_edit(lowered)
    toneish = looks_like_tone(lowered)
    transform_op = _lazy_detect_transform(text) if has_files else None

    if has_files:
        # 0a) После «что не заполнено» короткое значение → fill
        if (
            awaiting_gap_fill
            and looks_like_short_gap_value(text)
            and not transform_op
        ):
            return {
                "intent": "gap_fill",
                "confidence": 0.95,
                "mode": "fill",
                "family": "edit",
                "guard": "gap_fill_value",
            }

        # 0b) Накопленные факты + «вставь/добавь» → fill
        if (
            has_facts
            and any(k in lowered for k in _FACTS_FOLLOWUP_KEYS)
            and not looks_like_reverse_words(lowered)
            and not transform_op
        ):
            return {
                "intent": "edit",
                "confidence": 0.92,
                "mode": "fill",
                "family": "edit",
                "guard": "facts_followup",
            }

        # 0c) Детерминированный transform → edit
        if transform_op and intent not in {
            "check",
            "risks",
            "extract",
            "compare",
            "format",
        }:
            return {
                "intent": "edit",
                "confidence": 0.95,
                "mode": "edit",
                "family": "edit",
                "guard": f"transform:{transform_op}",
            }

        # 1) Clarify create при файлах — почти всегда ошибка
        if intent == "clarify" and family == "write" and not strong:
            return {
                "intent": "ask",
                "confidence": 0.85,
                "mode": "",
                "family": "ask",
                "guard": "files_block_write_clarify",
            }
        if intent == "clarify" and family == "write" and strong and questionish:
            return {
                "intent": "ask",
                "confidence": 0.8,
                "mode": "",
                "family": "ask",
                "guard": "question_beats_create_clarify",
            }

        # 2) write_* без явного создания — вопрос/правка
        if intent in {"write_form", "write_text"} and not strong:
            if fillish:
                return {
                    "intent": "edit",
                    "confidence": 0.9,
                    "mode": "fill" if looks_like_fill_data(lowered) else "edit",
                    "family": "edit",
                    "guard": "files_soft_write_to_edit",
                }
            return {
                "intent": "ask",
                "confidence": 0.85,
                "mode": "",
                "family": "ask",
                "guard": "files_soft_write_to_ask",
            }

        # 3) Вопрос/проверка важнее создания (но не ломаем явный write с сильным глаголом)
        if (
            questionish
            and intent in {"clarify", "write_form", "write_text", "none", "card"}
            and not (strong and intent in {"write_form", "write_text"})
        ):
            if fillish:
                return {
                    "intent": "edit",
                    "confidence": 0.88,
                    "mode": "fill" if looks_like_fill_data(lowered) else mode or "edit",
                    "family": "edit",
                    "guard": "question_with_fill",
                }
            return {
                "intent": "ask",
                "confidence": 0.88,
                "mode": "",
                "family": "ask",
                "guard": "files_question_default",
            }

        # 4) Тон / правка
        if toneish and intent not in {"edit", "check", "risks", "extract", "compare"}:
            return {
                "intent": "edit",
                "confidence": 0.9,
                "mode": "tone",
                "family": "edit",
                "guard": "tone_override",
            }
        if fillish and intent in {"ask", "none", "clarify", "card"}:
            return {
                "intent": "edit",
                "confidence": 0.9,
                "mode": "fill" if looks_like_fill_data(lowered) else "edit",
                "family": "edit",
                "guard": "fill_beats_ask",
            }

        # 5) Пустой/слабый intent при файлах → ask
        if intent in {"none", ""}:
            return {
                "intent": "ask",
                "confidence": 0.7,
                "mode": "",
                "family": "ask",
                "guard": "files_default_ask",
            }

        # 6) Карточка не должна съедать документный контекст без явного товара
        if intent == "card" and (questionish or fillish or soft) and not looks_like_card(lowered):
            return {
                "intent": "ask",
                "confidence": 0.8,
                "mode": "",
                "family": "ask",
                "guard": "block_sticky_card_like",
            }

    else:
        # Без файлов: «проверь информацию» → попросить файл, не бланк/текст
        if questionish and intent in {"clarify", "write_form", "write_text", "none"} and not strong:
            return {
                "intent": "clarify",
                "confidence": 0.75,
                "mode": "",
                "family": "ask",
                "question": "Пришлите документ в чат — проверю по нему.",
                "options": [
                    {"id": "need_file", "label": "Сейчас пришлю файл"},
                    {"id": "write_text", "label": "Нет, создай новый текст"},
                ],
                "guard": "no_files_question",
            }

    decision = dict(decision)
    decision.setdefault("guard", "")
    return decision


def classify_document_intent(
    text: str,
    *,
    has_files: bool = False,
    has_facts: bool = False,
    awaiting_gap_fill: bool = False,
) -> dict[str, Any]:
    """
    Единственный роутер намерений (+ guards).
    intent: card | write_form | write_text | edit | ask | check | risks |
            extract | compare | format | clarify | gap_fill | none
    """
    raw = (text or "").strip()
    decision = _classify_document_intent_raw(raw, has_files=has_files)
    return apply_document_routing_guards(
        decision,
        text=raw,
        has_files=has_files,
        has_facts=has_facts,
        awaiting_gap_fill=awaiting_gap_fill,
    )


def refine_document_intent_llm(
    text: str,
    *,
    has_files: bool = False,
    heuristic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LLM-уточнение, когда эвристика дала none / низкую уверенность."""
    allowed = {
        "ask",
        "edit",
        "write_form",
        "write_text",
        "check",
        "risks",
        "extract",
        "compare",
        "format",
        "card",
        "clarify",
        "none",
    }
    system = (
        "Ты классификатор намерений для Telegram-бота документов и карточек товара. "
        "Верни ТОЛЬКО JSON: "
        '{"intent":"...","confidence":0.0-1.0,"mode":"","family":"","question":"","options":[]}. '
        "intent один из: ask, edit, write_form, write_text, check, risks, extract, compare, "
        "format, card, clarify, none. "
        "mode: fill|tone|text|form|edit|sample| пусто. "
        "Если сомневаешься между создать документ и вопросом по файлу — clarify с options "
        '[{"id":"write_text","label":"..."},{"id":"ask","label":"..."}].'
    )
    user = (
        f"has_files={has_files}\n"
        f"heuristic={json.dumps(heuristic or {}, ensure_ascii=False)}\n"
        f"user_text={text!r}"
    )
    try:
        raw = _chat(system, user, temperature=0.0)
        data = _extract_json(raw)
    except Exception as exc:
        log.warning("LLM intent refine failed: %s", exc)
        return heuristic or {"intent": "none", "confidence": 0.0, "mode": "", "family": ""}

    intent = str(data.get("intent") or "none").strip()
    if intent not in allowed:
        intent = "none"
    conf = float(data.get("confidence") or 0.55)
    conf = max(0.0, min(1.0, conf))
    mode = str(data.get("mode") or "")
    family = str(data.get("family") or "")
    out: dict[str, Any] = {
        "intent": intent,
        "confidence": conf,
        "mode": mode,
        "family": family,
        "guard": "llm_refine",
    }
    if intent == "clarify":
        out["question"] = str(data.get("question") or "Уточните, что сделать?")
        opts = data.get("options") or []
        if isinstance(opts, list):
            out["options"] = opts
    return apply_document_routing_guards(
        out,
        text=text,
        has_files=has_files,
        has_facts=False,
        awaiting_gap_fill=False,
    )


def _classify_document_intent_raw(text: str, *, has_files: bool = False) -> dict[str, Any]:
    """Сырой эвристический роутер (без финальных guards)."""
    raw = (text or "").strip()
    lowered = raw.lower()
    if not lowered:
        return {"intent": "none", "confidence": 1.0, "mode": "", "family": ""}

    form_keys = (
        "титульн",
        "декларац",
        "заявлен",
        "справк",
        "бланк",
        "форма ",
        "по форм",
        "3-ндфл",
        "усн",
        "инн",
        "ифнс",
        "реквизит",
        "заполни поле",
        "шаблон",
    )
    text_keys = (
        "реферат",
        "эссе",
        "доклад",
        "курсов",
        "сочинен",
        "конспект",
        "диплом",
        "статью",
        "статья",
        "на тему",
        "по теме",
        "претензи",
        "письмо ",
        "деловое письмо",
        "коммерческое предложен",
        " кп ",
        "напиши кп",
        "сделай кп",
        "служебную записк",
        "объяснительн",
    )
    write_verbs = (
        "напиши",
        "написать",
        "написал",
        "сделай",
        "создай",
        "создать",
        "создал",
        "составь",
        "составить",
        "составил",
        "сгенерируй",
        "подготовь",
        "сформируй",
        "оформи",
        "скинь",
        "пришли",
        "нужен",
        "нужна",
        "нужно",
    )
    edit_keys = (
        "замени",
        "заменить",
        "поменяй",
        "исправ",
        "удали ",
        "удалить",
        "перепиши",
        "переписать",
        "переверн",
        "переверт",
        "задом наперёд",
        "задом наперед",
        "наоборот",
        "сократи",
        "переведи",
        "вставь",
        "встав ",
        "отредактируй",
        "заполни",
        "подставь",
        "внеси ",
        "впиши",
        "пропиши",
        "сделай фио",
        "поставь фио",
        "укажи фио",
        "смени фио",
        "добав",
        "добавить данн",
        "нужно добав",
        "надо добав",
    )

    has_form = any(k in lowered for k in form_keys)
    has_text = any(k in lowered for k in text_keys)
    has_write_verb = any(v in lowered for v in write_verbs)
    has_edit = (
        any(k in lowered for k in edit_keys)
        or bool(re.search(r"(?<![а-яё])правк", lowered))
        or looks_like_fill_data(lowered)
        or looks_like_reverse_words(lowered)
    )
    has_tone = looks_like_tone(lowered)

    # При загруженном файле правка/заполнение — не новый документ и не «поиск»
    if has_files and has_edit:
        fill = (not looks_like_reverse_words(lowered)) and (
            looks_like_fill_data(lowered)
            or any(k in lowered for k in ("добав", "заполни", "фио", "эти данн", "мои данн"))
        )
        return {
            "intent": "edit",
            "confidence": 0.93,
            "mode": "fill" if fill else "edit",
            "family": "edit",
        }

    if has_files and has_tone:
        return {
            "intent": "edit",
            "confidence": 0.9,
            "mode": "tone",
            "family": "edit",
        }

    mentions_file = any(
        k in lowered for k in ("файл", "документ", "word", "ворд", "docx")
    )
    wants_only_title = any(
        k in lowered
        for k in (
            "титульн",
            "только лист",
            "первая страниц",
            "первую страниц",
            "первый лист",
            "первый титульн",
            "только перв",
        )
    )
    about_topic = any(
        k in lowered
        for k in ("про декларац", "о декларац", "что такое", "расскажи", "объясни")
    )

    # Сравнение двух документов
    if any(
        k in lowered
        for k in (
            "сравни",
            "сравнить",
            "чем отлича",
            "отличия между",
            "разница между",
            "что изменилось",
            "diff",
        )
    ):
        return {
            "intent": "compare",
            "confidence": 0.9,
            "mode": "",
            "family": "ask",
        }

    # Оформление по образцу
    if any(
        k in lowered
        for k in (
            "по образцу",
            "как в образце",
            "оформи по",
            "по форме",
            "по госту",
            "по гост",
            "в таком же оформлен",
        )
    ):
        return {
            "intent": "format",
            "confidence": 0.88,
            "mode": "sample",
            "family": "write",
        }

    # Риски договора
    if any(
        k in lowered
        for k in (
            "проверь договор",
            "риски договор",
            "риски в договор",
            "найди риски",
            "опасные пункт",
            "что опасн",
            "разбери договор",
            "анализ договор",
            "проверь на риски",
            "юридическ риск",
        )
    ) or (
        has_files
        and "договор" in lowered
        and any(k in lowered for k in ("риск", "проверь", "разбери", "анализ"))
    ):
        return {
            "intent": "risks",
            "confidence": 0.92,
            "mode": "",
            "family": "ask",
        }

    # Выжимка фактов
    if any(
        k in lowered
        for k in (
            "вытащи факт",
            "извлеки факт",
            "ключевые факт",
            "выпиши даты",
            "выпиши сумм",
            "стороны и сумм",
            "стороны, даты",
            "стороны даты",
            "даты и суммы",
            "даты и сумм",
            "сводка по документ",
            "выписка из документ",
            "что за стороны",
        )
    ) or (
        any(k in lowered for k in ("вытащи", "извлеки", "выпиши", "собери"))
        and any(k in lowered for k in ("сторон", "сумм", "даты", "срок", "реквизит", "факт"))
    ):
        return {
            "intent": "extract",
            "confidence": 0.9,
            "mode": "",
            "family": "ask",
        }

    # Проверка данных / информации в уже загруженном файле
    if has_files and any(
        k in lowered
        for k in (
            "проверь информац",
            "проверить информац",
            "проверь данн",
            "проверить данн",
            "проверь фио",
            "сверь данн",
            "сверь фио",
            "верно ли",
            "правильн ли",
            "информация о человек",
            "информацию о человек",
            "о человеке",
            "данные о человек",
            "проверь что в файл",
            "посмотри данные",
        )
    ):
        return {"intent": "ask", "confidence": 0.9, "mode": "", "family": "ask"}

    # Проверка пустых полей бланка
    if has_files and any(
        k in lowered
        for k in (
            "что не заполн",
            "что ещё не заполн",
            "какие поля",
            "пустые поля",
            "что осталось заполн",
            "что не заполнено",
            "проверь бланк",
            "проверь поля",
        )
    ):
        return {"intent": "check", "confidence": 0.9, "mode": "gaps", "family": "ask"}

    # Правки / заполнение существующего файла — ВЫШЕ карточки
    if has_edit and (
        has_files
        or "в договор" in lowered
        or "в документ" in lowered
        or "в файл" in lowered
        or "туда" in lowered
    ):
        return {
            "intent": "edit" if has_files else "clarify",
            "confidence": 0.92 if has_files else 0.7,
            "mode": "fill" if looks_like_fill_data(lowered) else "",
            "family": "edit",
            "question": "Нужно поправить уже загруженный Word-файл?"
            if not has_files
            else "",
            "options": [
                {"id": "need_file", "label": "Да, сейчас пришлю файл"},
                {"id": "write_text", "label": "Нет, создай новый текст"},
            ]
            if not has_files
            else [],
        }
    if has_edit and not has_write_verb and not has_form and not has_text:
        return {
            "intent": "edit" if has_files else "clarify",
            "confidence": 0.7,
            "mode": "",
            "family": "edit",
            "question": "Нужно поправить уже загруженный Word-файл?"
            if not has_files
            else "",
            "options": [
                {"id": "need_file", "label": "Да, сейчас пришлю файл"},
                {"id": "write_text", "label": "Нет, создай новый текст"},
            ]
            if not has_files
            else [],
        }

    # Карточка только если нет сигналов правки/заполнения
    if looks_like_card(lowered) and not has_edit:
        return {"intent": "card", "confidence": 0.95, "mode": "", "family": "card"}

    # Создание: бланк vs текст
    if has_write_verb or has_form or has_text or (mentions_file and has_write_verb):
        # «Составь договор / акт» — создание, но слово «договор» не должно
        # перехватывать вопросы «что в договоре?»
        if has_write_verb and (
            "договор" in lowered
            or re.search(r"(?<![а-яё])акт(?![а-яё])", lowered)
        ):
            return {
                "intent": "write_form",
                "confidence": 0.85,
                "mode": "form",
                "family": "write",
            }
        # Явный бланк
        if has_form and not about_topic:
            if wants_only_title or "бланк" in lowered or "шаблон" in lowered or "титульн" in lowered:
                return {
                    "intent": "write_form",
                    "confidence": 0.92,
                    "mode": "form",
                    "family": "write",
                }
            if has_text:
                return {
                    "intent": "clarify",
                    "confidence": 0.55,
                    "mode": "",
                    "family": "write",
                    "question": "Нужен бланк (титульный лист) или связный текст на тему?",
                    "options": [
                        {"id": "write_form", "label": "Бланк / титульный лист"},
                        {"id": "write_text", "label": "Текст / реферат"},
                    ],
                }
            if "декларац" in lowered and not wants_only_title:
                return {
                    "intent": "clarify",
                    "confidence": 0.6,
                    "mode": "",
                    "family": "write",
                    "question": "Шаблон титульного листа декларации или текст про декларации?",
                    "options": [
                        {"id": "write_form", "label": "Шаблон / титульный лист"},
                        {"id": "write_text", "label": "Текст / объяснение"},
                    ],
                }
            return {
                "intent": "write_form",
                "confidence": 0.8,
                "mode": "form",
                "family": "write",
            }

        if has_text or (
            has_write_verb
            and any(
                k in lowered
                for k in (
                    "реферат",
                    "эссе",
                    "доклад",
                    "сочинен",
                    "претензи",
                    "письмо",
                    "коммерческ",
                )
            )
        ):
            return {
                "intent": "write_text",
                "confidence": 0.9,
                "mode": "text",
                "family": "write",
            }

        if has_write_verb and (mentions_file or len(lowered) >= 20):
            if has_form:
                return {
                    "intent": "write_form",
                    "confidence": 0.75,
                    "mode": "form",
                    "family": "write",
                }
            # «Мне нужно, чтобы ты проверил…» при загруженном файле —
            # это вопрос/проверка, а не создание бланка/текста.
            strong_create = any(
                v in lowered
                for v in (
                    "напиши",
                    "написать",
                    "создай",
                    "составь",
                    "сгенерируй",
                    "подготовь",
                    "сформируй",
                    "оформи",
                    "сделай бланк",
                    "сделай файл",
                    "сделай документ",
                    "сделай реферат",
                    "сделай письмо",
                )
            )
            if has_files and not strong_create and not has_text:
                return {
                    "intent": "ask",
                    "confidence": 0.75,
                    "mode": "",
                    "family": "ask",
                }
            return {
                "intent": "clarify",
                "confidence": 0.5,
                "mode": "",
                "family": "write",
                "question": "Нужен бланк с полями или связный текст?",
                "options": [
                    {"id": "write_form", "label": "Бланк / форма"},
                    {"id": "write_text", "label": "Связный текст"},
                ],
            }

    # Вопрос по файлу
    if has_files:
        return {"intent": "ask", "confidence": 0.7, "mode": "", "family": "ask"}

    if looks_like_write_legacy(lowered):
        return {
            "intent": "clarify",
            "confidence": 0.45,
            "mode": "",
            "family": "write",
            "question": "Создать бланк или текст?",
            "options": [
                {"id": "write_form", "label": "Бланк / титульный"},
                {"id": "write_text", "label": "Текст / реферат"},
            ],
        }

    return {"intent": "none", "confidence": 0.4, "mode": "", "family": ""}


def looks_like_write_legacy(text: str) -> bool:
    """Узкий fallback без рекурсии в classify."""
    lowered = (text or "").lower()
    kinds = ("реферат", "эссе", "доклад", "файл", "документ", "декларац", "титульн", "word")
    verbs = ("напиши", "создай", "составь", "сделай", "подготовь", "оформи")
    return any(k in lowered for k in kinds) and any(v in lowered for v in verbs)


def parse_document_volume(prompt: str) -> dict[str, Any]:
    """
    Достаёт желаемый объём из запроса.
    pages — целевые страницы (или None), chars — целевые знаки (или None),
    want_pdf — просили PDF.
    Оценка: ~1800 знаков ≈ 1 учебная страница (14 pt).
    """
    raw = (prompt or "").strip()
    lowered = raw.lower().replace("ё", "е")
    want_pdf = bool(re.search(r"\bpdf\b|пдф", lowered))
    pages: int | None = None
    chars: int | None = None

    m = re.search(
        r"(?:на\s+|объ[её]м(?:ом)?\s+|примерно\s+|около\s+)?"
        r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*(?:страниц\w*|стр\.?)\b",
        lowered,
    )
    if m:
        pages = max(int(m.group(1)), int(m.group(2)))
    if pages is None:
        m = re.search(
            r"(?:на\s+|объ[её]м(?:ом)?\s+|примерно\s+|около\s+)?"
            r"(\d{1,2})\s*(?:страниц\w*|стр\.?)\b",
            lowered,
        )
        if m:
            pages = int(m.group(1))
    if pages is None:
        m = re.search(r"\b(\d{1,2})\s*стр\b", lowered)
        if m:
            pages = int(m.group(1))

    m = re.search(
        r"(\d{1,3}(?:[ \u00a0]?\d{3})*|\d+)\s*(?:тыс\.?\s*)?знак",
        lowered,
    )
    if m:
        num = re.sub(r"\D", "", m.group(1))
        if num:
            chars = int(num)
            if "тыс" in lowered[m.start() : m.end() + 8]:
                chars *= 1000

    if pages is not None:
        pages = max(1, min(int(pages), 15))
    if chars is not None:
        chars = max(800, min(int(chars), 15 * 1800))
    if pages is None and chars is not None:
        pages = max(1, min(15, (chars + 1799) // 1800))
    if chars is None and pages is not None:
        chars = pages * 1800

    return {
        "pages": pages,
        "chars": chars,
        "want_pdf": want_pdf,
        "chars_per_page": 1800,
    }


def _normalize_sections(sections_raw: Any) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    if not isinstance(sections_raw, list):
        return sections
    for item in sections_raw:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "").strip()[:120]
        paras_in = item.get("paragraphs") or []
        if isinstance(paras_in, str):
            paras_in = [paras_in]
        paragraphs = [str(p).strip() for p in paras_in if str(p).strip()]
        if heading or paragraphs:
            sections.append({"heading": heading, "paragraphs": paragraphs})
    return sections


def _sections_char_count(sections: list[dict[str, Any]]) -> int:
    total = 0
    for sec in sections:
        total += len(str(sec.get("heading") or ""))
        for p in sec.get("paragraphs") or []:
            total += len(str(p))
    return total


def generate_document(prompt: str, mode: str = "auto") -> dict[str, Any]:
    """
    Генерация документа.
    mode: auto | form | text
    Для больших объёмов («на 15 страниц») — план + раскрытие разделов.
    """
    prompt_l = (prompt or "").lower()
    mode = (mode or "auto").strip().lower()
    if mode not in {"auto", "form", "text"}:
        mode = "auto"
    volume = parse_document_volume(prompt)
    target_pages = volume.get("pages")
    target_chars = volume.get("chars")
    is_form = mode == "form" or (
        mode == "auto"
        and any(
            k in prompt_l
            for k in (
                "титульн",
                "декларац",
                "заявлен",
                "справк",
                "бланк",
                "форма ",
                "по форм",
                "3-ндфл",
                "усн",
                "инн",
                "ифнс",
                "договор",
                "акт ",
                "реквизит",
                "шаблон",
            )
        )
        and not any(k in prompt_l for k in ("реферат", "эссе", "доклад", "сочинен"))
    )
    system = (
        "Ты готовишь НОВЫЕ документы на русском по запросу пользователя. "
        "Верни только JSON:\n"
        '{"title":str,"doc_type":str,"filename_stem":str,'
        '"sections":[{"heading":str,"paragraphs":[str]}]}\n\n'
        "doc_type: реферат|эссе|доклад|отчёт|сочинение|письмо|претензия|"
        "коммерческое|декларация|"
        "заявление|справка|титульный|договор|другое\n"
        "filename_stem: короткое имя файла без расширения.\n\n"
        "КРИТИЧНО — сначала определи тип запроса:\n"
        "A) БЛАНК / ТИТУЛЬНЫЙ ЛИСТ / ДЕКЛАРАЦИЯ / ЗАЯВЛЕНИЕ / СПРАВКА / ДОГОВОР\n"
        "   Если просят «титульный лист», «декларацию ИП», «бланк», «форму» —\n"
        "   это ШАБЛОН ФОРМЫ, а НЕ сочинение и НЕ реферат «про декларации».\n"
        "   Запрещено писать разделы вроде «Обязанности ИП», «Ответственность», "
        "«Сроки подачи» как учебный текст.\n"
        "   Нужны поля-плейсхолдеры в квадратных скобках и короткие подписи строк.\n"
        "   Для титульного листа налоговой декларации ИП (ориентир 3-НДФЛ / УСН) сделай "
        "секции с полями, например:\n"
        "   • ИНН: [ИНН]\n"
        "   • Номер корректировки: [0]\n"
        "   • Налоговый период (код): [34]\n"
        "   • Отчётный год: [ГГГГ]\n"
        "   • Код налогового органа: [ИФНС]\n"
        "   • Код страны / статус налогоплательщика: […]\n"
        "   • ФИО: [Фамилия] [Имя] [Отчество]\n"
        "   • Дата рождения: [ДД.ММ.ГГГГ]\n"
        "   • Документ, удостоверяющий личность: [серия] [номер]\n"
        "   • Адрес / телефон: […]\n"
        "   • Подпись / дата: [подпись] / [ДД.ММ.ГГГГ]\n"
        "   В начале одной строкой: «Типовой шаблон для заполнения, не официальный бланк ФНС».\n"
        "   Если просят ТОЛЬКО первый титульный лист — не добавляй остальные разделы декларации.\n\n"
        "B) СВЯЗНЫЙ ТЕКСТ (реферат, эссе, доклад, письмо, претензия, КП)\n"
        "   Письмо / претензия / коммерческое предложение — деловой тон, чёткие блоки "
        "(адресат, суть, требования/условия, подпись).\n"
        "   Учебный текст — введение, основные разделы, заключение; своими словами, "
        "без копипаста; можно ориентировочный список литературы.\n\n"
        "Общие правила:\n"
        "• Не выдумывай чужие ФИО, ИНН, адреса — только плейсхолдеры.\n"
        "• Не выдавай шаблон за юридическую консультацию.\n"
        "• Строго следуй объёму из запроса: «только титульный» = только титульный; "
        "«N страниц» / «N знаков» — обязательный целевой объём текста."
    )
    if mode == "form" or is_form:
        system += (
            "\n\nСейчас ОБЯЗАТЕЛЕН режим A (бланк/титульный). Режим B запрещён."
        )
        is_form = True
    elif mode == "text":
        system += (
            "\n\nСейчас ОБЯЗАТЕЛЕН режим B (связный оригинальный текст). "
            "Не делай налоговый бланк, если явно не просили форму."
        )
        is_form = False
    elif is_form:
        system += (
            "\n\nСейчас запрос похож на БЛАНК/ТИТУЛЬНЫЙ/ФОРМУ — используй режим A, "
            "не режим B."
        )

    # Длинные учебные тексты — план + пораздельная генерация (один JSON редко тянет 10+ стр.)
    long_text = (
        not is_form
        and target_pages is not None
        and int(target_pages) >= 4
    )
    if long_text:
        return _generate_long_document(
            prompt,
            target_pages=int(target_pages),
            target_chars=int(target_chars or target_pages * 1800),
            want_pdf=bool(volume.get("want_pdf")),
        )

    volume_line = ""
    if target_chars:
        volume_line = (
            f"\nЦелевой объём: примерно {target_chars} знаков"
            + (f" (~{target_pages} стр.)" if target_pages else "")
            + ". Нужны развёрнутые абзацы, не краткий конспект."
        )
    user = f"Запрос пользователя:\n{(prompt or '').strip()}{volume_line}"
    raw = _chat(system, user, temperature=0.35 if is_form else 0.75)
    try:
        data = _extract_json(raw)
    except Exception as exc:
        raise RuntimeError(f"Не разобрать ответ модели: {exc}") from exc

    title = str(data.get("title") or "Документ").strip()[:200]
    doc_type = str(data.get("doc_type") or ("титульный" if is_form else "другое")).strip().lower()[:40]
    stem = str(data.get("filename_stem") or title).strip()[:80]
    sections = _normalize_sections(data.get("sections") or [])
    if not sections:
        raise RuntimeError("Модель вернула пустой документ")
    return {
        "title": title,
        "doc_type": doc_type,
        "filename_stem": stem,
        "sections": sections,
        "mode": "form" if is_form else "text",
        "want_pdf": bool(volume.get("want_pdf")),
        "target_pages": target_pages,
        "approx_chars": _sections_char_count(sections),
    }


def _generate_long_document(
    prompt: str,
    *,
    target_pages: int,
    target_chars: int,
    want_pdf: bool = False,
) -> dict[str, Any]:
    """Реферат/доклад большого объёма: оглавление, затем текст по главам."""
    # Практика: >12 стр. одним заходом нестабильно по времени/таймауту
    effective_pages = max(4, min(int(target_pages), 12))
    effective_chars = max(effective_pages * 1800, min(int(target_chars), 12 * 1800))
    n_sections = max(6, min(12, effective_pages + 2))
    chars_per = max(900, effective_chars // n_sections)

    outline_system = (
        "Составь план учебного документа на русском. Верни только JSON:\n"
        '{"title":str,"doc_type":str,"filename_stem":str,'
        '"headings":[str]}\n'
        f"Нужно ровно {n_sections} заголовков разделов (включая введение и заключение). "
        "Без текста абзацев — только названия разделов."
    )
    outline_user = (
        f"Запрос:\n{(prompt or '').strip()}\n"
        f"Целевой объём: ~{effective_pages} страниц (~{effective_chars} знаков)."
    )
    raw = _chat(outline_system, outline_user, temperature=0.4)
    try:
        outline = _extract_json(raw)
    except Exception as exc:
        raise RuntimeError(f"Не разобрать план документа: {exc}") from exc

    title = str(outline.get("title") or "Реферат").strip()[:200]
    doc_type = str(outline.get("doc_type") or "реферат").strip().lower()[:40]
    stem = str(outline.get("filename_stem") or title).strip()[:80]
    headings = [str(h).strip()[:120] for h in (outline.get("headings") or []) if str(h).strip()]
    if len(headings) < 4:
        headings = [
            "Введение",
            "Основная часть",
            "Практический аспект",
            "Заключение",
            "Список литературы",
        ]
    headings = headings[:n_sections]
    while len(headings) < n_sections:
        headings.append(f"Раздел {len(headings)}")

    sections: list[dict[str, Any]] = []
    for heading in headings:
        expand_system = (
            "Напиши один раздел учебного текста на русском. Верни только JSON:\n"
            '{"heading":str,"paragraphs":[str]}\n'
            f"Раздел «{heading}». Нужно примерно {chars_per} знаков связного текста "
            "(несколько абзацев по 4–8 предложений). Без воды и без копипаста. "
            "Не повторяй другие разделы."
        )
        expand_user = (
            f"Тема документа: {title}\n"
            f"Общий запрос пользователя:\n{(prompt or '').strip()}\n"
            f"Пиши только раздел: {heading}"
        )
        try:
            part_raw = _chat(expand_system, expand_user, temperature=0.7)
            part = _extract_json(part_raw)
        except Exception as exc:
            log.warning("Section expand failed for %s: %s", heading, exc)
            part = {
                "heading": heading,
                "paragraphs": [
                    f"[Не удалось полностью раскрыть раздел «{heading}». "
                    f"Повторите запрос или уменьшите объём.]"
                ],
            }
        sec_list = _normalize_sections([part])
        if sec_list:
            if not sec_list[0].get("heading"):
                sec_list[0]["heading"] = heading
            sections.extend(sec_list)
        else:
            sections.append({"heading": heading, "paragraphs": ["…"]})

    if not sections:
        raise RuntimeError("Не удалось собрать длинный документ")
    note = ""
    if target_pages > effective_pages:
        note = (
            f"Запросили ~{target_pages} стр., сформировал развёрнутый текст "
            f"ориентировочно на ~{effective_pages} стр. (лимит стабильной генерации)."
        )
    return {
        "title": title,
        "doc_type": doc_type,
        "filename_stem": stem,
        "sections": sections,
        "mode": "text",
        "want_pdf": want_pdf,
        "target_pages": target_pages,
        "approx_chars": _sections_char_count(sections),
        "volume_note": note,
    }


def looks_like_card(text: str) -> bool:
    lowered = (text or "").lower()
    # Правка/заполнение документа — никогда не карточка
    if looks_like_edit(lowered) or looks_like_fill_data(lowered):
        return False
    if "инфографик" in lowered:
        return True
    if any(
        k in lowered
        for k in (
            "продаю",
            "продажа",
            "для продажи",
            "продать",
            "нужно продать",
            "listing",
        )
    ):
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
        '"image_prompt": str, "scene": str, "origin": str, "accent": str}. '
        "label — тип товара ЗАГЛАВНЫМИ (СТУЛ, КРЕСЛО, ГОРШОК, ЛАМПА, КРУЖКА), до 14 символов. "
        "title — полное название до 55 символов, 4–8 слов; всегда дописывай фразу целиком "
        "(например «Деревянный обеденный стол на 6 персон»), без многоточия и обрезки. "
        "Не заканчивай предлогом (для/на/с/и). "
        "subtitle — короткое имя/модель для второй строки обложки, до 36 символов "
        "(например «It's coffee TIME»), или пусто. "
        "hook — одна короткая выгода для бейджа, до 28 символов, в одну строку. "
        "size — габарит для бейджа на обложке: «28 CM», «180×90», «Ø40» или пусто если неизвестно. "
        "callouts — ровно 3 выноски (материал/деталь/конструкция), до 22 символов, "
        "короткие (1–4 слова), полный смысл без обрезки. "
        "bullets — ровно 4 преимущества, до 40 символов, полные фразы без «…». "
        "description — 350-550 символов для описания на маркетплейсе. "
        "keywords — 8-12 фраз через запятую. "
        "image_prompt — English ONLY for a studio catalog photo of the exact product. "
        "MUST start with the exact product from the user request/photo. "
        "Never invent another category. Preserve artwork/prints on the product surface. "
        "scene — English ONLY, one short phrase: where this product naturally belongs "
        "(e.g. 'on a sunny windowsill', 'on a wooden nightstand', 'on a kitchen table'). "
        "Pick a realistic everyday placement for THIS product type. "
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
    scene = _clip(str(data.get("scene") or ""), 220)
    accent = str(data.get("accent") or "").strip()
    if accent and not accent.startswith("#"):
        accent = "#" + accent
    hook = str(data.get("hook") or data.get("subtitle") or "").strip()
    size = str(data.get("size") or "").strip().upper()
    # Не выдумываем габарит, если в запросе пользователя нет цифр
    if size and not re.search(r"\d", notes or ""):
        size = ""
    origin = str(data.get("origin") or "").strip()[:40]
    # Origin только если намек есть в тексте пользователя
    if origin and notes:
        notes_l = notes.lower()
        origin_ok = any(
            w in notes_l
            for w in (
                "росси",
                "беларус",
                "белорус",
                "китай",
                "турц",
                "сделано",
                "произвед",
                "страна",
            )
        )
        if not origin_ok:
            origin = ""
    # image_prompt: не просим лишние надписи вокруг товара; принт на самом товаре — ок
    for bad in (
        "with text overlay",
        "title banner",
        "watermark",
        "ui mockup",
        "poster slogan",
        "надпис на фоне",
        "текст на фоне",
    ):
        if bad in image_prompt.lower():
            image_prompt = re.sub(re.escape(bad), " ", image_prompt, flags=re.I)
    image_prompt = (
        image_prompt.strip()
        + ". Studio catalog shot; keep product-surface artwork; "
        "no extra captions, badges or watermarks around the product."
    )
    return {
        "label": str(data.get("label") or "").strip()[:16],
        "title": title,
        "subtitle": _clip(str(data.get("subtitle") or ""), 40),
        "hook": _clip(hook, 28),
        "size": _clip(size, 16),
        "callouts": callouts[:3],
        "bullets": bullets[:4],
        "description": str(data.get("description") or "").strip(),
        "keywords": str(data.get("keywords") or "").strip(),
        "image_prompt": image_prompt[:400],
        "scene": scene,
        "origin": origin,
        "accent": accent[:7],
    }
