from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

CARD_W = 1080
CARD_H = 1440

_PALETTES = [
    # soft_bg, dark, white, accent, gradient_deep
    ("#F3E9E4", "#1C1917", "#FFFFFF", "#E8A598", "#E7D5CE"),
    ("#E8EEE9", "#1C1917", "#FFFFFF", "#7FA89A", "#D5E0D8"),
    ("#F0EBE3", "#1C1917", "#FFFFFF", "#C4A574", "#E4D9C8"),
    ("#E9EEF3", "#1C1917", "#FFFFFF", "#7A9BB8", "#D5DEE8"),
    ("#F0EAF0", "#1C1917", "#FFFFFF", "#B89BC4", "#E0D5E3"),
    ("#F5F0EA", "#1C1917", "#FFFFFF", "#D4A574", "#EBE0D4"),
]

_FONT_REG = [
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
]
_FONT_BOLD = [
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
]
_FONT_SEMI = [
    Path(r"C:\Windows\Fonts\seguisb.ttf"),
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
]


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    pool = {"bold": _FONT_BOLD, "semi": _FONT_SEMI}.get(weight, _FONT_REG)
    for path in list(pool) + _FONT_REG:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


# Короткие служебные слова — лучше не оставлять их «висячими» в конце строки
_SOFT_BREAK_BEFORE = {
    "для", "на", "с", "и", "в", "из", "по", "к", "от", "до", "у", "о",
    "со", "во", "об", "про", "без", "под", "над", "при", "через",
}


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return float(draw.textlength(text, font=font))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int | None = None) -> list[str]:
    """Перенос с балансом длин строк — без одиночных хвостов вроде «лампой»."""
    words = (text or "").split()
    if not words:
        return []

    def fits(s: str) -> bool:
        return _text_w(draw, s, font) <= max_width

    full = " ".join(words)
    if fits(full):
        return [full]

    # Жадный первый проход
    greedy: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if fits(trial):
            current = trial
        else:
            greedy.append(current)
            current = word
    greedy.append(current)

    # Если слишком длинное слово — оставляем как есть (не режем по слогам)
    lines = greedy

    # Балансировка: двигаем слова с предыдущей строки на последнюю,
    # чтобы убрать сирот и выровнять длины.
    if len(lines) >= 2:
        prev = lines[-2].split()
        last = lines[-1].split()
        while len(prev) > 1:
            moved = prev[-1]
            # Не тащим предлог в конец предыдущей строки, если можно
            candidate_prev = " ".join(prev[:-1])
            candidate_last = " ".join([moved] + last)
            if not fits(candidate_prev) or not fits(candidate_last):
                break
            old_spread = abs(_text_w(draw, lines[-2], font) - _text_w(draw, lines[-1], font))
            new_spread = abs(_text_w(draw, candidate_prev, font) - _text_w(draw, candidate_last, font))
            orphan = len(last) == 1 or _text_w(draw, lines[-1], font) < max_width * 0.38
            better = new_spread + 8 < old_spread or (orphan and new_spread <= old_spread + 20)
            if not better:
                break
            prev = prev[:-1]
            last = [moved] + last
            lines[-2] = " ".join(prev)
            lines[-1] = " ".join(last)

        # Предлог в конце предпоследней строки → перенос на следующую, если влезает
        if len(prev) >= 2 and prev[-1].lower() in _SOFT_BREAK_BEFORE:
            moved = prev[-1]
            candidate_prev = " ".join(prev[:-1])
            candidate_last = " ".join([moved] + last)
            if fits(candidate_prev) and fits(candidate_last):
                lines[-2] = candidate_prev
                lines[-1] = candidate_last

    if max_lines is not None and len(lines) > max_lines:
        # Сжимаем хвост в последнюю допустимую строку с многоточием при необходимости
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        while not fits(tail) and " " in tail:
            tail = tail.rsplit(" ", 1)[0]
        if not fits(tail):
            # жёсткая обрезка
            while tail and not fits(tail + "…"):
                tail = tail[:-1]
            tail = (tail.rstrip(" .,;:") + "…") if tail else "…"
        lines = head + [tail]
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    x: int,
    y: int,
    fill: str,
    line_h: int,
) -> int:
    """Рисует строки, возвращает Y после блока."""
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return y


def _vline_center(box_top: int, box_h: int, lines: list[str], line_h: int) -> int:
    """Вертикальный старт текста по центру бокса."""
    block = line_h * max(1, len(lines))
    return box_top + max(0, (box_h - block) // 2)


def _palette(seed: str, accent_hex: str | None = None) -> tuple[str, str, str, str, str]:
    if accent_hex:
        try:
            h = accent_hex.strip().lstrip("#")
            if len(h) == 6:
                r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
                hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
                soft = colorsys.hsv_to_rgb(hh, min(0.18, ss * 0.35), 0.94)
                deep = colorsys.hsv_to_rgb(hh, min(0.28, ss * 0.5), 0.86)
                accent = colorsys.hsv_to_rgb(hh, min(0.45, max(ss, 0.25)), min(0.78, vv))
                to_hex = lambda rgb: f"#{int(rgb[0]*255):02X}{int(rgb[1]*255):02X}{int(rgb[2]*255):02X}"
                return (to_hex(soft), "#1C1917", "#FFFFFF", to_hex(accent), to_hex(deep))
        except Exception:
            pass
    return _PALETTES[sum(ord(c) for c in (seed or "x")) % len(_PALETTES)]


def _gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, top)
    tr, tg, tb = (int(top[i : i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(bottom[i : i + 2], 16) for i in (1, 3, 5))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg - tg) * t)
        b = int(tb + (bb - tb) * t)
        draw.line((0, y, w, y), fill=(r, g, b))
    return img.convert("RGBA")


def _load_product(photo: Path) -> Image.Image:
    raw = Image.open(photo)
    if raw.mode in {"RGBA", "LA"} or (raw.mode == "P" and "transparency" in raw.info):
        return raw.convert("RGBA")
    return raw.convert("RGBA")


def _fit(product: Image.Image, box: tuple[int, int]) -> Image.Image:
    if product.mode != "RGBA":
        product = product.convert("RGBA")
    return ImageOps.contain(product, box, Image.Resampling.LANCZOS)


def _alpha_paste(base: Image.Image, overlay: Image.Image, xy: tuple[int, int]) -> None:
    x, y = xy
    bw, bh = base.size
    ow, oh = overlay.size
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(ow, bw - x), min(oh, bh - y)
    if right <= left or bottom <= top:
        return
    base.alpha_composite(overlay.crop((left, top, right, bottom)), (x + left, y + top))


def _paste_shadow(canvas: Image.Image, product: Image.Image, xy: tuple[int, int]) -> None:
    x, y = xy
    if product.mode != "RGBA":
        product = product.convert("RGBA")
    pad = 72
    shadow = Image.new("RGBA", (product.width + pad * 2, product.height + pad * 2), (0, 0, 0, 0))
    layer = Image.new("RGBA", product.size, (0, 0, 0, 70))
    layer.putalpha(product.split()[-1].point(lambda a: min(70, a)))
    # мягкая «контактная» тень чуть ниже товара
    shadow.paste(layer, (pad + 2, pad + 28), layer)
    shadow = shadow.filter(ImageFilter.GaussianBlur(32))
    _alpha_paste(canvas, shadow, (x - pad, y - pad))
    _alpha_paste(canvas, product, (x, y))


def _pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fill: str,
    color: str,
    font,
) -> int:
    x, y = xy
    pad_x = 22
    h = 52
    tw = int(_text_w(draw, text, font))
    w = tw + pad_x * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=26, fill=fill)
    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    ty = y + (h - th) // 2 - bbox[1]
    draw.text((x + pad_x, ty), text, font=font, fill=color)
    return w


def _clip_words(text: str, max_chars: int, ellipsis: bool = False) -> str:
    """Обрезает по словам. По умолчанию без «…» — лучше полный смысл и перенос."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip(" .,;:—-")
    if not cut:
        cut = text[:max_chars]
    return (cut + "…") if ellipsis else cut


def _fit_title(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_lines: int = 2,
    start_size: int = 52,
    min_size: int = 34,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int]:
    """Подбирает размер шрифта так, чтобы заголовок вместился целиком без «…»."""
    text = (text or "").strip() or "Карточка товара"
    for size in range(start_size, min_size - 1, -2):
        font = _font(size, "bold")
        lines = _wrap(draw, text, font, max_width, max_lines=None)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _font(min_size, "bold")
    lines = _wrap(draw, text, font, max_width, max_lines=None)
    if len(lines) <= max_lines + 1:
        return font, lines[: max_lines + 1], min_size
    return font, _wrap(draw, text, font, max_width, max_lines=max_lines), min_size


def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    box: tuple[int, int, int, int],
    fill: str,
    line_gap: int = 6,
) -> None:
    """Текст по центру бокса по X и Y (через textbbox / anchor)."""
    x1, y1, x2, y2 = box
    if not lines:
        return
    if len(lines) == 1:
        draw.text(((x1 + x2) // 2, (y1 + y2) // 2), lines[0], font=font, fill=fill, anchor="mm")
        return
    sizes: list[tuple[str, int, int, int]] = []
    for line in lines:
        b = draw.textbbox((0, 0), line, font=font)
        sizes.append((line, b[2] - b[0], b[3] - b[1], b[1]))
    total_h = sum(h for _, _, h, _ in sizes) + line_gap * (len(sizes) - 1)
    y = y1 + max(0, (y2 - y1 - total_h) // 2)
    for line, w, h, top in sizes:
        x = x1 + max(0, (x2 - x1 - w) // 2)
        draw.text((x, y - top), line, font=font, fill=fill)
        y += h + line_gap


def _prepare_data(data: dict) -> dict:
    # Заголовок не режем многоточием — влезает за счёт переноса/уменьшения шрифта
    title = _clip_words(str(data.get("title") or "Карточка товара"), 64, ellipsis=False)
    subtitle = _clip_words(str(data.get("subtitle") or ""), 40, ellipsis=False)
    label = str(data.get("label") or "").strip().upper()[:16]
    hook = str(data.get("hook") or subtitle or (data.get("bullets") or [""])[0] or "").strip()
    origin = str(data.get("origin") or "").strip()
    size = str(data.get("size") or "").strip().upper()
    accent = str(data.get("accent") or "").strip() or None
    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    bullets = [_clip_words(str(x), 44, ellipsis=False) for x in bullets if str(x).strip()]
    callouts = data.get("callouts") or []
    if not isinstance(callouts, list):
        callouts = []
    callouts = [_clip_words(str(x), 28, ellipsis=False) for x in callouts if str(x).strip()]
    if not callouts:
        callouts = bullets[:3]
    return {
        "title": title,
        "subtitle": subtitle,
        "label": label,
        "hook": _clip_words(hook, 32, ellipsis=False),
        "origin": origin[:40],
        "size": _clip_words(size, 16, ellipsis=False),
        "accent": accent,
        "bullets": bullets[:4],
        "callouts": callouts[:3],
    }


def _load_photo(photo: Path | None) -> Image.Image | None:
    if photo is None or not photo.exists():
        return None
    try:
        return _load_product(photo)
    except Exception:
        return None


def _trim_transparent(product: Image.Image, pad: int = 8) -> Image.Image:
    """Обрезает пустые края по альфе — фото не «висит» криво в кадре."""
    if product.mode != "RGBA":
        product = product.convert("RGBA")
    alpha = product.split()[-1]
    bbox = alpha.getbbox()
    if not bbox:
        return product
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(product.width, right + pad)
    bottom = min(product.height, bottom + pad)
    return product.crop((left, top, right, bottom))


def _place_in_box(
    canvas: Image.Image,
    product: Image.Image,
    box: tuple[int, int, int, int],
    max_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Вписывает товар в прямоугольник box=(x1,y1,x2,y2), возвращает центр."""
    x1, y1, x2, y2 = box
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    if max_size:
        bw = min(bw, max_size[0])
        bh = min(bh, max_size[1])
    product = _trim_transparent(product)
    fitted = _fit(product, (bw, bh))
    px = x1 + (x2 - x1 - fitted.width) // 2
    py = y1 + (y2 - y1 - fitted.height) // 2
    _paste_shadow(canvas, fitted, (px, py))
    return px + fitted.width // 2, py + fitted.height // 2


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix_hex(a: str, b: str, t: float) -> str:
    ar, ag, ab = _hex_rgb(a)
    br, bg, bb = _hex_rgb(b)
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    b_ = int(ab + (bb - ab) * t)
    return f"#{r:02X}{g:02X}{b_:02X}"


def _studio_background(palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Чистый каталожный фон: почти белый + мягкий tint акцента (как у топ-карточек WB)."""
    soft, _dark, white, accent, deep = palette
    top = _mix_hex(white, soft, 0.22)
    bottom = _mix_hex(soft, deep, 0.28)
    canvas = _gradient((CARD_W, CARD_H), top, bottom)
    overlay = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    ar, ag, ab = _hex_rgb(accent)
    d.ellipse((-180, CARD_H - 420, 420, CARD_H + 180), fill=(ar, ag, ab, 28))
    d.ellipse((CARD_W - 380, -160, CARD_W + 200, 380), fill=(ar, ag, ab, 18))
    return Image.alpha_composite(canvas, overlay)


def _chiba_background(palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Совместимость: тот же студийный фон."""
    return _studio_background(palette)


def _draw_product_pedestal(
    canvas: Image.Image,
    center: tuple[int, int],
    width: int,
    accent: str,
) -> None:
    """Мягкая эллиптическая подложка под товар."""
    cx, cy = center
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    rw, rh = max(140, width // 2), max(40, width // 9)
    y = cy + int(width * 0.38)
    d.ellipse((cx - rw, y - rh // 2, cx + rw, y + rh), fill=(0, 0, 0, 36))
    ar, ag, ab = _hex_rgb(accent)
    d.ellipse(
        (cx - int(rw * 0.82), y - int(rh * 0.3), cx + int(rw * 0.82), y + int(rh * 0.65)),
        fill=(ar, ag, ab, 48),
    )
    canvas.alpha_composite(overlay.filter(ImageFilter.GaussianBlur(20)))


def _safe_margin() -> int:
    """Отступ от краёв: WB перекрывает углы бейджами UI."""
    return 72


def _feature_chip(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fill: str,
    text_color: str,
    font,
    *,
    max_w: int = 360,
) -> tuple[int, int]:
    """Короткая плашка УТП (1–2 строки). Возвращает (w, h)."""
    x, y = xy
    lines = _wrap(draw, text, font, max_w - 40, max_lines=2) or [text[:18]]
    pad_x, pad_y, gap = 22, 14, 4
    heights = []
    for ln in lines:
        b = draw.textbbox((0, 0), ln, font=font)
        heights.append(b[3] - b[1])
    tw = int(max(_text_w(draw, ln, font) for ln in lines))
    th = sum(heights) + gap * max(0, len(lines) - 1)
    w = tw + pad_x * 2
    h = th + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=max(18, h // 2), fill=fill)
    ty = y + pad_y
    for i, ln in enumerate(lines):
        b = draw.textbbox((0, 0), ln, font=font)
        draw.text((x + pad_x, ty - b[1]), ln, font=font, fill=text_color)
        ty += heights[i] + gap
    return w, h


def _text_outline(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,
    fill: str,
    outline: str,
    width: int = 4,
    anchor: str | None = None,
) -> None:
    x, y = xy
    for dx in range(-width, width + 1):
        for dy in range(-width, width + 1):
            if dx == 0 and dy == 0:
                continue
            if dx * dx + dy * dy > width * width:
                continue
            kwargs = {"font": font, "fill": outline}
            if anchor:
                kwargs["anchor"] = anchor
            draw.text((x + dx, y + dy), text, **kwargs)
    kwargs = {"font": font, "fill": fill}
    if anchor:
        kwargs["anchor"] = anchor
    draw.text((x, y), text, **kwargs)


def _curve_points(
    start: tuple[float, float],
    end: tuple[float, float],
    bend: float = 0.22,
) -> list[tuple[float, float]]:
    sx, sy = start
    ex, ey = end
    mx = (sx + ex) / 2
    my = (sy + ey) / 2
    dx, dy = ex - sx, ey - sy
    nx, ny = -dy, dx
    norm = (nx * nx + ny * ny) ** 0.5 or 1.0
    cx = mx + nx / norm * bend * ((dx * dx + dy * dy) ** 0.5)
    cy = my + ny / norm * bend * ((dx * dx + dy * dy) ** 0.5)
    pts: list[tuple[float, float]] = []
    for i in range(25):
        t = i / 24
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t**2 * ex
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t**2 * ey
        pts.append((x, y))
    return pts


def _draw_curve_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: str,
    width: int = 5,
    bend: float = 0.2,
) -> None:
    pts = _curve_points(start, end, bend=bend)
    draw.line(pts, fill=color, width=width, joint="curve")
    if len(pts) < 2:
        return
    x1, y1 = pts[-2]
    x2, y2 = pts[-1]
    ang = math.atan2(y2 - y1, x2 - x1)
    ah = 16
    left = (x2 - ah * math.cos(ang - 0.45), y2 - ah * math.sin(ang - 0.45))
    right = (x2 - ah * math.cos(ang + 0.45), y2 - ah * math.sin(ang + 0.45))
    draw.polygon([end, left, right], fill=color)


def _dashed_circle(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    color: str,
    width: int = 4,
    dashes: int = 28,
) -> None:
    cx, cy = center
    for i in range(dashes):
        if i % 2:
            continue
        a0 = 2 * math.pi * i / dashes
        a1 = 2 * math.pi * (i + 0.85) / dashes
        draw.arc(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            start=math.degrees(a0),
            end=math.degrees(a1),
            fill=color,
            width=width,
        )


def _callout_pill(
    draw: ImageDraw.ImageDraw,
    text_lines: list[str],
    font,
    anchor_xy: tuple[float, float],
    align: str,
    fill: str,
    text_color: str,
    border: str,
) -> tuple[float, float, float, float]:
    pad_x, pad_y, gap = 22, 14, 4
    widths = [int(_text_w(draw, ln, font)) for ln in text_lines] or [40]
    heights = []
    for ln in text_lines:
        b = draw.textbbox((0, 0), ln, font=font)
        heights.append(b[3] - b[1])
    tw = max(widths)
    th = sum(heights) + gap * max(0, len(text_lines) - 1)
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    ax, ay = anchor_xy
    if align == "left":
        x1, x2 = ax, ax + bw
    else:
        x1, x2 = ax - bw, ax
    y1, y2 = ay - bh / 2, ay + bh / 2
    draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=fill, outline=border, width=3)
    y = y1 + pad_y
    for i, ln in enumerate(text_lines):
        b = draw.textbbox((0, 0), ln, font=font)
        draw.text((x1 + pad_x, y - b[1]), ln, font=font, fill=text_color)
        y += heights[i] + gap
    return x1, y1, x2, y2


def _hero_headline(data: dict) -> tuple[str, str]:
    raw_title = (data.get("title") or "").strip()
    label = (data.get("label") or "").strip()
    subtitle = (data.get("subtitle") or "").strip()
    if raw_title.lower() in {"карточка товара", "product card", "товар"}:
        return (label or subtitle or "ТОВАР").upper(), ""
    if label:
        line1 = label.upper()
        if subtitle:
            return line1, subtitle
        if raw_title.upper().startswith(label.upper()):
            return line1, raw_title[len(label) :].strip(" -—,.")
        return line1, raw_title
    words = raw_title.split()
    if len(words) > 3:
        mid = max(2, len(words) // 2)
        return " ".join(words[:mid]).upper(), " ".join(words[mid:])
    return raw_title.upper(), subtitle


def _slide_hero(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    """
    Обложка как у топ-карточек WB:
    товар ~70% кадра, УТП-плашка, до 2 коротких чипов, safe-margin под UI WB.
    """
    soft, dark, white, accent, _deep = palette
    canvas = _studio_background(palette)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    line1, line2 = _hero_headline(data)
    y = float(m - 8)
    for idx, line in enumerate([line1, line2] if line2 else [line1]):
        if not line:
            continue
        max_w = CARD_W - m * 2
        size = 56 if idx == 0 else 34
        font = _font(size, "bold")
        while size >= 28 and _text_w(draw, line, font) > max_w:
            size -= 2
            font = _font(size, "bold")
        fill = dark if idx == 0 else _mix_hex(dark, accent, 0.4)
        draw.text((CARD_W / 2, y + size / 2), line, font=font, fill=fill, anchor="mm")
        y += size + (8 if idx == 0 else 6)

    hook = (data.get("hook") or "").strip()
    if hook:
        hf = _font(24, "semi")
        hook_text = hook[:28]
        pill_w = int(_text_w(draw, hook_text, hf)) + 44
        _pill(draw, ((CARD_W - pill_w) // 2, int(y + 6)), hook_text, accent, white, hf)
        y += 58

    photo_top = int(max(y + 12, 200))
    photo_bottom = CARD_H - m - 20
    if product is not None:
        box_cx = CARD_W // 2
        box_cy = (photo_top + photo_bottom) // 2 + 30
        _draw_product_pedestal(canvas, (box_cx, box_cy), 680, accent)
        cx, cy = _place_in_box(
            canvas,
            product,
            (m + 20, photo_top, CARD_W - m - 20, photo_bottom),
        )
    else:
        draw.rounded_rectangle(
            (180, photo_top + 80, 900, photo_bottom - 80), radius=36, fill=white
        )
        cx, cy = CARD_W // 2, (photo_top + photo_bottom) // 2

    size_txt = (data.get("size") or "").strip()
    if size_txt:
        bx, by, br = m + 70, photo_top + 80, 68
        _dashed_circle(draw, (bx, by), br, white, width=5, dashes=32)
        _dashed_circle(draw, (bx, by), br - 3, accent, width=2, dashes=32)
        sf = _font(28, "bold")
        _text_outline(draw, (bx, by), size_txt, sf, fill=dark, outline=white, width=3, anchor="mm")

    chips = [c for c in (data.get("callouts") or []) if c][:2]
    chip_font = _font(24, "semi")
    if chips:
        _feature_chip(draw, (m, int(cy - 40)), chips[0], white, dark, chip_font, max_w=300)
    if len(chips) > 1:
        lines = _wrap(draw, chips[1], chip_font, 260, max_lines=2) or [chips[1]]
        tw = int(max(_text_w(draw, ln, chip_font) for ln in lines)) + 44
        _feature_chip(
            draw,
            (CARD_W - m - tw, int(cy + 120)),
            chips[1],
            white,
            dark,
            chip_font,
            max_w=300,
        )

    origin = (data.get("origin") or "").strip()
    if origin:
        short = origin
        for prefix in ("Сделано в ", "сделано в ", "Произведено в ", "Страна: "):
            if short.lower().startswith(prefix.lower()):
                short = short[len(prefix) :]
                break
        short = short.strip()
        _country = {
            "беларуси": "БЕЛАРУСЬ",
            "беларусь": "БЕЛАРУСЬ",
            "россии": "РОССИЯ",
            "россия": "РОССИЯ",
            "китае": "КИТАЙ",
            "китай": "КИТАЙ",
            "турции": "ТУРЦИЯ",
            "турция": "ТУРЦИЯ",
        }
        short = _country.get(short.lower(), short.upper())
        _pill(draw, (m, CARD_H - m - 52), short[:18], accent, white, _font(22, "semi"))

    return canvas


def _slide_callouts(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Слайд деталей: товар слева, 3 плашки справа со стрелками."""
    soft, dark, white, accent, _deep = palette
    canvas = _gradient((CARD_W, CARD_H), white, soft)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    draw.text((m, m), "ДЕТАЛИ", font=_font(22, "semi"), fill=accent)
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2 - 40, max_lines=2, start_size=40, min_size=28
    )
    _draw_lines(draw, title_lines, title_font, m, m + 40, dark, max(38, int(title_size * 1.15)))

    if product is not None:
        cx, cy = _place_in_box(canvas, product, (m - 10, 280, 620, CARD_H - m))
    else:
        draw.rounded_rectangle((m, 360, 600, CARD_H - m - 40), radius=28, fill=soft)
        cx, cy = 320, 780

    callout_font = _font(26, "semi")
    box_right = CARD_W - m
    box_left = 700
    text_pad = 22
    text_max = box_right - box_left - text_pad * 2
    items = data["callouts"][:3] or data["bullets"][:3]
    slots = [420, 700, 980]
    for i, text in enumerate(items):
        ty = slots[i]
        wrapped = _wrap(draw, text, callout_font, text_max, max_lines=2)
        if not wrapped:
            continue
        line_heights = []
        for ln in wrapped:
            b = draw.textbbox((0, 0), ln, font=callout_font)
            line_heights.append(b[3] - b[1])
        content_h = sum(line_heights) + 6 * max(0, len(wrapped) - 1)
        block_h = max(72, content_h + 32)
        content_w = max((_text_w(draw, ln, callout_font) for ln in wrapped), default=0)
        bw = int(min(box_right - box_left, max(200, content_w + text_pad * 2)))
        bx1 = box_right - bw
        by1 = ty - block_h // 2
        by2 = by1 + block_h
        draw.rounded_rectangle(
            (bx1 + 4, by1 + 6, box_right + 4, by2 + 6),
            radius=20,
            fill=_mix_hex(soft, "#000000", 0.08),
        )
        draw.rounded_rectangle((bx1, by1, box_right, by2), radius=20, fill=white)
        draw.rounded_rectangle((bx1, by1, bx1 + 8, by2), radius=4, fill=accent)
        anchor_y = cy - 100 + i * 100
        draw.line((cx + 40, anchor_y, bx1 - 10, ty), fill=accent, width=3)
        draw.ellipse((cx + 32, anchor_y - 8, cx + 48, anchor_y + 8), fill=accent)
        _draw_centered_lines(
            draw,
            wrapped,
            callout_font,
            (bx1 + text_pad, by1, box_right - text_pad // 2, by2),
            dark,
            line_gap=6,
        )
    return canvas


def _slide_benefits(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Слайд выгод: 4 крупные карточки + мини-фото."""
    soft, dark, white, accent, _deep = palette
    canvas = _studio_background(palette)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    draw.text((m, m), "ПРЕИМУЩЕСТВА", font=_font(22, "semi"), fill=accent)
    title_max = CARD_W - m * 2 - (280 if product is not None else 0)
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], title_max, max_lines=2, start_size=40, min_size=28
    )
    y = _draw_lines(draw, title_lines, title_font, m, m + 42, dark, max(38, int(title_size * 1.15)))

    if product is not None:
        frame = (CARD_W - m - 240, m, CARD_W - m, m + 240)
        draw.rounded_rectangle(frame, radius=28, fill=white)
        _place_in_box(
            canvas,
            product,
            (frame[0] + 16, frame[1] + 16, frame[2] - 16, frame[3] - 16),
        )

    items = data["bullets"][:4] or data["callouts"][:4]
    card_font = _font(30, "semi")
    num_font = _font(28, "bold")
    top = max(y + 36, 320)
    gap = 22
    card_h = 168
    card_x1, card_x2 = m, CARD_W - m
    circle = 76
    inner_gap = 28
    side_pad = 36

    for i, item in enumerate(items):
        yy = top + i * (card_h + gap)
        if yy + card_h > CARD_H - m:
            break
        draw.rounded_rectangle(
            (card_x1 + 3, yy + 5, card_x2 + 3, yy + card_h + 5),
            radius=28,
            fill=_mix_hex(soft, "#000000", 0.07),
        )
        draw.rounded_rectangle((card_x1, yy, card_x2, yy + card_h), radius=28, fill=white)

        max_text_w = (card_x2 - card_x1) - side_pad * 2 - circle - inner_gap
        wrapped = _wrap(draw, item, card_font, max_text_w, max_lines=2)
        text_w = int(max((_text_w(draw, ln, card_font) for ln in wrapped), default=0))
        group_w = circle + inner_gap + text_w
        group_x = card_x1 + (card_x2 - card_x1 - group_w) // 2

        cy1 = yy + (card_h - circle) // 2
        cx1 = group_x
        draw.ellipse((cx1, cy1, cx1 + circle, cy1 + circle), fill=accent)
        num = str(i + 1)
        nb = draw.textbbox((0, 0), num, font=num_font)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text(
            (cx1 + (circle - nw) // 2 - nb[0], cy1 + (circle - nh) // 2 - nb[1]),
            num,
            font=num_font,
            fill=white,
        )
        tx1 = cx1 + circle + inner_gap
        _draw_centered_lines(
            draw,
            wrapped,
            card_font,
            (tx1, yy, tx1 + text_w, yy + card_h),
            dark,
            line_gap=6,
        )

    if data.get("origin"):
        _pill(draw, (m, CARD_H - m - 52), data["origin"], accent, white, _font(22, "semi"))
    return canvas


def _dominant_accent(product: Image.Image | None) -> str | None:
    """Мягкий accent из среднего цвета непрозрачных пикселей товара."""
    if product is None:
        return None
    try:
        img = product.convert("RGBA")
        img.thumbnail((96, 96))
        pixels = list(img.getdata())
        rs = gs = bs = n = 0
        for r, g, b, a in pixels:
            if a < 160:
                continue
            if r > 245 and g > 245 and b > 245:
                continue
            if r < 18 and g < 18 and b < 18:
                continue
            rs += r
            gs += g
            bs += b
            n += 1
        if n < 20:
            return None
        r, g, b = rs // n, gs // n, bs // n
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return None


def _fit_scene_photo(photo: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Contain product/scene in box with soft blurred fill (no crop of the subject)."""
    x0, y0, x1, y1 = box
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    # RGBA cutout → flatten onto soft beige (never convert RGBA→RGB via black matte)
    if photo.mode == "RGBA":
        src = Image.new("RGB", photo.size, (236, 228, 218))
        src.paste(photo, mask=photo.split()[-1])
    else:
        src = photo.convert("RGB")
    sw, sh = src.size
    scale = min(bw / max(1, sw), bh / max(1, sh))
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    fitted = src.resize((nw, nh), Image.Resampling.LANCZOS)

    fill = src.resize((bw, bh), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    fill = ImageEnhance.Brightness(fill).enhance(0.92)
    canvas = Image.new("RGB", (bw, bh), (236, 228, 218))
    canvas.paste(fill, (0, 0))
    canvas.paste(fitted, ((bw - nw) // 2, (bh - nh) // 2))
    return canvas


def _paste_scene(canvas: Image.Image, product: Image.Image | None) -> None:
    if product is None:
        return
    scene = _fit_scene_photo(product, (0, 0, CARD_W, CARD_H))
    canvas.paste(scene, (0, 0))


def _bottom_veil(canvas: Image.Image, height: int = 360, strength: float = 0.72) -> Image.Image:
    """Soft dark gradient at bottom; keeps most of the product visible above."""
    base = canvas.convert("RGBA")
    veil = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    h = max(80, min(height, CARD_H // 2))
    for i in range(h):
        a = int(255 * strength * ((i / h) ** 1.35))
        vd.line([(0, CARD_H - h + i), (CARD_W, CARD_H - h + i)], fill=(18, 14, 12, a))
    return Image.alpha_composite(base, veil).convert("RGB")


def _slide_hero_lifestyle(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Lifestyle cover: полный товар в сцене (contain) + мягкая подпись снизу."""
    soft, dark, white, accent, _deep = palette
    canvas = Image.new("RGB", (CARD_W, CARD_H), soft)
    _paste_scene(canvas, product)
    canvas = _bottom_veil(canvas, height=300, strength=0.68)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    eyebrow = "В ИНТЕРЬЕРЕ"
    ef = _font(22, "semi")
    draw.text((CARD_W / 2, CARD_H - 250), eyebrow, font=ef, fill=white, anchor="mm")

    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2, max_lines=3, start_size=44, min_size=28
    )
    y = CARD_H - 210
    lh = max(40, int(title_size * 1.12))
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=white, anchor="mm")
        y += lh
    return canvas


def _slide_callouts_lifestyle(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Lifestyle детали: полная сцена + 3 нумерованные плашки."""
    soft, dark, white, accent, _deep = palette
    canvas = Image.new("RGB", (CARD_W, CARD_H), soft)
    _paste_scene(canvas, product)
    canvas = _bottom_veil(canvas, height=520, strength=0.78)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    eyebrow = "В ИНТЕРЬЕРЕ"
    draw.text((CARD_W / 2, CARD_H - 490), eyebrow, font=_font(22, "semi"), fill=white, anchor="mm")

    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2, max_lines=2, start_size=40, min_size=26
    )
    y = CARD_H - 450
    lh = max(38, int(title_size * 1.12))
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=white, anchor="mm")
        y += lh

    items = data["callouts"][:3] or data["bullets"][:3]
    body = _font(26, "semi")
    num_font = _font(22, "bold")
    y += 20
    for i, text in enumerate(items, start=1):
        if not text:
            continue
        pill_h = 76
        x1, x2 = m + 20, CARD_W - m - 20
        draw.rounded_rectangle((x1, y, x2, y + pill_h), radius=38, fill=white)
        cx, cy = x1 + 44, y + pill_h // 2
        draw.ellipse((cx - 24, cy - 24, cx + 24, cy + 24), fill=accent)
        draw.text((cx, cy), str(i), font=num_font, fill=white, anchor="mm")
        wrapped = _wrap(draw, text, body, x2 - (x1 + 90) - 24, max_lines=2) or [text]
        _draw_centered_lines(
            draw,
            wrapped,
            body,
            (x1 + 90, y, x2 - 24, y + pill_h),
            dark,
            line_gap=4,
        )
        y += pill_h + 16
    return canvas


def _slide_benefits_lifestyle(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Lifestyle выгоды: сцена сверху (полный товар) + карточки преимуществ снизу."""
    soft, dark, white, accent, _deep = palette
    canvas = _studio_background(palette)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    draw.text((CARD_W / 2, m + 8), "ПОЧЕМУ ЭТОТ ТОВАР", font=_font(22, "semi"), fill=accent, anchor="mm")
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2, max_lines=2, start_size=40, min_size=26
    )
    y = m + 48
    lh = max(38, int(title_size * 1.12))
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=dark, anchor="mm")
        y += lh

    # Scene strip — contain, rounded
    strip_top = int(y + 20)
    strip_bottom = strip_top + 420
    frame = (m, strip_top, CARD_W - m, strip_bottom)
    if product is not None:
        fitted = _fit_scene_photo(product, (0, 0, frame[2] - frame[0], frame[3] - frame[1]))
        mask = Image.new("L", fitted.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, fitted.size[0] - 1, fitted.size[1] - 1), radius=28, fill=255
        )
        canvas.paste(fitted, (frame[0], frame[1]), mask)
    else:
        draw.rounded_rectangle(frame, radius=28, fill=white)

    items = data["bullets"][:4] or data["callouts"][:4]
    body = _font(26, "semi")
    by = strip_bottom + 28
    for item in items:
        if not item or by + 78 > CARD_H - m:
            break
        draw.rounded_rectangle((m, by, CARD_W - m, by + 72), radius=20, fill=white)
        draw.rectangle((m, by + 16, m + 8, by + 56), fill=accent)
        wrapped = _wrap(draw, item, body, CARD_W - m * 2 - 48, max_lines=1) or [item]
        tw = _text_w(draw, wrapped[0], body)
        draw.text(((CARD_W - tw) / 2, by + 22), wrapped[0], font=body, fill=dark)
        by += 88
    return canvas.convert("RGB")


def _slide_hero_editorial(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Постер: мягкое поле + крупный заголовок + полный товар."""
    soft, dark, white, accent, _deep = palette
    field = _mix_hex(soft, white, 0.35)
    canvas = Image.new("RGB", (CARD_W, CARD_H), field)
    overlay = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    ar, ag, ab = _hex_rgb(accent)
    for cx, cy, r, a in (
        (140, 200, 220, 38),
        (CARD_W - 80, 560, 260, 32),
        (CARD_W // 2, CARD_H - 40, 280, 26),
    ):
        od.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(ar, ag, ab, a))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    label = "ПОСТЕР"
    lf = _font(20, "semi")
    lw = int(_text_w(draw, label, lf)) + 44
    _pill(draw, ((CARD_W - lw) // 2, m - 4), label, accent, white, lf)

    title_font, title_lines, title_size = _fit_title(
        draw, data["title"].upper(), CARD_W - m * 2, max_lines=3, start_size=48, min_size=28
    )
    y = m + 64
    lh = max(44, int(title_size * 1.15))
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=dark, anchor="mm")
        y += lh

    hook = (data.get("hook") or "").strip()
    if hook:
        hf = _font(26, "semi")
        for line in _wrap(draw, hook, hf, CARD_W - m * 2 - 40, max_lines=2):
            draw.text((CARD_W / 2, y + 16), line, font=hf, fill=_mix_hex(dark, accent, 0.35), anchor="mm")
            y += 36

    photo_top = int(max(y + 28, 280))
    if product is not None:
        _place_in_box(canvas, product, (m + 40, photo_top, CARD_W - m - 40, CARD_H - m - 20))
    return canvas.convert("RGB")


def _slide_callouts_editorial(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Постер детали: товар сверху целиком + сетка фактов."""
    soft, dark, white, accent, _deep = palette
    field = _mix_hex(soft, white, 0.4)
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (*_hex_rgb(field), 255))
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    draw.text((CARD_W / 2, m), "ПОСТЕР · ДЕТАЛИ", font=_font(22, "semi"), fill=accent, anchor="mm")
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2, max_lines=2, start_size=40, min_size=26
    )
    y = m + 40
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=dark, anchor="mm")
        y += max(38, int(title_size * 1.12))

    photo_bottom = int(y + 460)
    if product is not None:
        _place_in_box(canvas, product, (m + 60, int(y + 16), CARD_W - m - 60, photo_bottom))

    items = [t for t in (data["callouts"][:4] or data["bullets"][:4]) if t]
    body = _font(24, "semi")
    num_font = _font(22, "bold")
    gap = 16
    top = photo_bottom + 24
    # 1–3 пункта — в столбик (без «дырки» в сетке 2×2); 4 — сетка 2×2
    if len(items) <= 3:
        cell_h = 90
        for i, text in enumerate(items, start=1):
            yy = top + (i - 1) * (cell_h + gap)
            if yy + cell_h > CARD_H - m:
                break
            draw.rounded_rectangle((m, yy, CARD_W - m, yy + cell_h), radius=22, fill=white)
            draw.ellipse((m + 18, yy + 21, m + 62, yy + 65), fill=accent)
            draw.text((m + 40, yy + 43), str(i), font=num_font, fill=white, anchor="mm")
            wrapped = _wrap(draw, text, body, CARD_W - m * 2 - 100, max_lines=2) or [text]
            _draw_centered_lines(
                draw, wrapped, body, (m + 78, yy + 8, CARD_W - m - 20, yy + cell_h - 8), dark, line_gap=4
            )
    else:
        cols = 2
        cell_w = (CARD_W - m * 2 - gap) // cols
        cell_h = 120
        for i, text in enumerate(items):
            col, row = i % cols, i // cols
            x1 = m + col * (cell_w + gap)
            yy = top + row * (cell_h + gap)
            if yy + cell_h > CARD_H - m:
                break
            draw.rounded_rectangle((x1, yy, x1 + cell_w, yy + cell_h), radius=22, fill=white)
            draw.ellipse((x1 + 18, yy + 18, x1 + 62, yy + 62), fill=accent)
            draw.text((x1 + 40, yy + 40), str(i + 1), font=num_font, fill=white, anchor="mm")
            wrapped = _wrap(draw, text, body, cell_w - 90, max_lines=2) or [text]
            _draw_centered_lines(
                draw, wrapped, body, (x1 + 72, yy + 12, x1 + cell_w - 16, yy + cell_h - 12), dark, line_gap=4
            )
    return canvas.convert("RGB")


def _slide_benefits_editorial(
    data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]
) -> Image.Image:
    """Постер выгоды: список плюсов + полный товар снизу."""
    soft, dark, white, accent, _deep = palette
    field = _mix_hex(soft, white, 0.4)
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (*_hex_rgb(field), 255))
    overlay = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    ar, ag, ab = _hex_rgb(accent)
    od.ellipse((-60, 60, 280, 400), fill=(ar, ag, ab, 30))
    od.ellipse((CARD_W - 200, CARD_H - 360, CARD_W + 80, CARD_H + 40), fill=(ar, ag, ab, 28))
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    m = _safe_margin()

    draw.text((CARD_W / 2, m), "ПОСТЕР · ПЛЮСЫ", font=_font(22, "semi"), fill=accent, anchor="mm")
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], CARD_W - m * 2, max_lines=2, start_size=40, min_size=26
    )
    y = m + 42
    for line in title_lines:
        draw.text((CARD_W / 2, y + title_size / 2), line, font=title_font, fill=dark, anchor="mm")
        y += max(38, int(title_size * 1.12))

    items = data["bullets"][:4] or data["callouts"][:4]
    body = _font(26, "semi")
    by = y + 20
    for item in items:
        if not item:
            continue
        draw.rounded_rectangle((m, by, CARD_W - m, by + 74), radius=18, fill=white)
        draw.ellipse((m + 18, by + 17, m + 56, by + 55), fill=accent)
        # check
        draw.line([(m + 28, by + 36), (m + 36, by + 44), (m + 46, by + 28)], fill=white, width=3)
        wrapped = _wrap(draw, item, body, CARD_W - m * 2 - 100, max_lines=1) or [item]
        draw.text((m + 72, by + 22), wrapped[0], font=body, fill=dark)
        by += 90

    if product is not None and by + 200 < CARD_H - m:
        _place_in_box(canvas, product, (m + 80, by + 10, CARD_W - m - 80, CARD_H - m - 10))
    return canvas.convert("RGB")


def render_product_card(
    dest: Path,
    data: dict,
    photo: Path | None = None,
) -> Path:
    """Обратная совместимость: один hero-слайд."""
    paths = render_product_card_pack(dest.parent, data, photo, stem=dest.stem)
    return paths[0]


def render_product_card_pack(
    dest_dir: Path,
    data: dict,
    photo: Path | None = None,
    stem: str = "card",
    variant: str = "catalog",
) -> list[Path]:
    """
    Набор из 3 слайдов.
    variant: catalog | lifestyle | editorial
    """
    prepared = _prepare_data(data)
    product = _load_photo(photo)
    accent = prepared.get("accent") or _dominant_accent(product)
    palette = _palette(prepared["title"], accent)
    variant = (variant or "catalog").strip().lower()
    if variant not in {"catalog", "lifestyle", "editorial"}:
        variant = "catalog"

    if variant == "lifestyle":
        slides = [
            ("hero", _slide_hero_lifestyle(prepared, product, palette)),
            ("details", _slide_callouts_lifestyle(prepared, product, palette)),
            ("benefits", _slide_benefits_lifestyle(prepared, product, palette)),
        ]
    elif variant == "editorial":
        slides = [
            ("hero", _slide_hero_editorial(prepared, product, palette)),
            ("details", _slide_callouts_editorial(prepared, product, palette)),
            ("benefits", _slide_benefits_editorial(prepared, product, palette)),
        ]
    else:
        slides = [
            ("hero", _slide_hero(prepared, product, palette)),
            ("details", _slide_callouts(prepared, product, palette)),
            ("benefits", _slide_benefits(prepared, product, palette)),
        ]

    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, img in slides:
        path = dest_dir / f"{stem}_{name}.png"
        n = 1
        while path.exists():
            path = dest_dir / f"{stem}_{name}_{n}.png"
            n += 1
        img.convert("RGB").save(path, format="PNG", optimize=True)
        paths.append(path)
    return paths
