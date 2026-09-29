from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

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
    pad = 56
    shadow = Image.new("RGBA", (product.width + pad * 2, product.height + pad * 2), (0, 0, 0, 0))
    layer = Image.new("RGBA", product.size, (0, 0, 0, 55))
    layer.putalpha(product.split()[-1].point(lambda a: min(55, a)))
    shadow.paste(layer, (pad + 4, pad + 18), layer)
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
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
    subtitle = _clip_words(str(data.get("subtitle") or ""), 56, ellipsis=False)
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


def _chiba_background(palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Мягкий градиент + блики в духе WB-инфографики."""
    soft, _dark, white, accent, deep = palette
    top = _mix_hex(soft, white, 0.35)
    bottom = _mix_hex(deep, accent, 0.22)
    canvas = _gradient((CARD_W, CARD_H), top, bottom)
    overlay = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    seed = sum(ord(c) for c in accent) * 17 + 91
    # мягкие боке-круги
    for i in range(18):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = seed % CARD_W
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = seed % CARD_H
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        r = 40 + seed % 120
        col = _hex_rgb(accent if i % 3 else white)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*col, 28 if i % 3 else 36))
    # искры / звёздочки
    for i in range(28):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = 40 + seed % (CARD_W - 80)
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = 40 + seed % (CARD_H - 80)
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        s = 3 + seed % 7
        d.ellipse((x - s, y - s, x + s, y + s), fill=(255, 255, 255, 140))
        d.line((x - s * 2, y, x + s * 2, y), fill=(255, 255, 255, 90), width=1)
        d.line((x, y - s * 2, x, y + s * 2), fill=(255, 255, 255, 90), width=1)
    canvas = Image.alpha_composite(canvas, overlay)
    return canvas


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
    # перпендикуляр для дуги
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


def _slide_hero(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    """Обложка в стиле WB-инфографики «Чиба»: title, товар, выноски, бейджи."""
    _soft, dark, white, accent, _deep = palette
    canvas = _chiba_background(palette)
    draw = ImageDraw.Draw(canvas)

    # Заголовок по центру сверху — крупный с обводкой
    line1 = (data["label"] or data["title"]).strip().upper()
    line2 = ""
    if data["label"] and data["subtitle"]:
        line2 = data["subtitle"].strip()
    elif data["label"] and data["title"] and data["title"].upper() != data["label"]:
        t = data["title"]
        if t.upper().startswith(data["label"]):
            line2 = t[len(data["label"]) :].strip(" -—,.")
        else:
            line2 = t
    elif not data["label"]:
        words = data["title"].split()
        if len(words) > 3:
            mid = max(2, len(words) // 2)
            line1 = " ".join(words[:mid]).upper()
            line2 = " ".join(words[mid:])
        else:
            line1 = data["title"].upper()

    title_fill = _mix_hex(accent, "#F5D76E", 0.35)
    outline = _mix_hex(accent, "#7A1020", 0.55)
    y = 70
    for idx, line in enumerate([line1, line2] if line2 else [line1]):
        if not line:
            continue
        max_w = CARD_W - 100
        size = 72 if idx == 0 else 54
        font = _font(size, "bold")
        while size >= 34 and _text_w(draw, line, font) > max_w:
            size -= 2
            font = _font(size, "bold")
        fill = title_fill if idx == 0 else _mix_hex("#F5D76E", accent, 0.25)
        _text_outline(
            draw,
            (CARD_W / 2, y + size / 2),
            line,
            font,
            fill=fill,
            outline=outline,
            width=5 if idx == 0 else 3,
            anchor="mm",
        )
        y += size + 12

    # Товар по центру
    photo_top = max(y + 20, 260)
    photo_bottom = CARD_H - 160
    if product is not None:
        cx, cy = _place_in_box(canvas, product, (120, photo_top, CARD_W - 120, photo_bottom))
    else:
        draw.rounded_rectangle((180, photo_top + 60, 900, photo_bottom - 60), radius=36, fill=white)
        cx, cy = CARD_W // 2, (photo_top + photo_bottom) // 2

    # Бейдж размера — пунктирный круг слева сверху у товара
    size_txt = (data.get("size") or "").strip()
    if size_txt:
        bx, by, br = 150, photo_top + 90, 78
        _dashed_circle(draw, (bx, by), br, white, width=5, dashes=32)
        _dashed_circle(draw, (bx, by), br - 3, accent, width=2, dashes=32)
        sf = _font(34, "bold")
        _text_outline(draw, (bx, by), size_txt, sf, fill=dark, outline=white, width=3, anchor="mm")

    # Выноски со стрелками (до 3)
    callouts = [c for c in (data.get("callouts") or []) if c][:3]
    callout_font = _font(28, "bold")
    # позиции текста и якоря на товаре
    layouts = [
        # left mid
        {"text_xy": (70, cy - 40), "anchor": (cx - 120, cy - 20), "align": "left", "bend": 0.28},
        # right mid
        {"text_xy": (CARD_W - 70, cy - 10), "anchor": (cx + 130, cy + 10), "align": "right", "bend": -0.26},
        # right lower
        {"text_xy": (CARD_W - 70, cy + 220), "anchor": (cx + 80, cy + 160), "align": "right", "bend": -0.18},
    ]
    for i, text in enumerate(callouts):
        lay = layouts[i]
        tx, ty = lay["text_xy"]
        ax, ay = lay["anchor"]
        wrapped = _wrap(draw, text.upper() if len(text) < 22 else text, callout_font, 320, max_lines=2)
        if not wrapped:
            continue
        # блок текста
        line_h = 34
        block_h = line_h * len(wrapped)
        max_ln_w = int(max(_text_w(draw, ln, callout_font) for ln in wrapped))
        if lay["align"] == "left":
            x0 = tx
            for j, ln in enumerate(wrapped):
                _text_outline(
                    draw,
                    (x0, ty + j * line_h),
                    ln,
                    callout_font,
                    fill=dark,
                    outline=white,
                    width=3,
                    anchor="lm",
                )
            text_end = (x0 + max_ln_w + 8, ty)
        else:
            for j, ln in enumerate(wrapped):
                _text_outline(
                    draw,
                    (tx, ty + j * line_h),
                    ln,
                    callout_font,
                    fill=dark,
                    outline=white,
                    width=3,
                    anchor="rm",
                )
            text_end = (tx - max_ln_w - 8, ty)
        # стрелка от текста к товару
        start = (text_end[0], ty + block_h / 2 - line_h / 2)
        _draw_curve_arrow(draw, start, (ax, ay), dark, width=4, bend=lay["bend"])

    # Origin справа внизу
    origin = (data.get("origin") or "").strip()
    if origin:
        short = origin
        for prefix in ("Сделано в ", "сделано в ", "Произведено в ", "Страна: "):
            if short.lower().startswith(prefix.lower()):
                short = short[len(prefix) :]
                break
        short = short.strip()
        # Именительный падеж для частых стран
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
        ox, oy = CARD_W - 64, CARD_H - 90
        draw.ellipse((ox - 210, oy - 28, ox - 154, oy + 28), fill=accent)
        draw.ellipse((ox - 200, oy - 18, ox - 164, oy + 18), fill=white)
        of = _font(26, "bold")
        _text_outline(draw, (ox, oy), short[:18], of, fill=dark, outline=white, width=3, anchor="rm")

    return canvas


def _slide_callouts(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    soft, dark, white, accent, deep = palette
    canvas = _gradient((CARD_W, CARD_H), white, soft)
    draw = ImageDraw.Draw(canvas)

    draw.text((64, 64), "ДЕТАЛИ", font=_font(24, "semi"), fill=accent)
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], 640, max_lines=2, start_size=42, min_size=30
    )
    _draw_lines(draw, title_lines, title_font, 64, 104, dark, max(40, int(title_size * 1.2)))

    if product is not None:
        cx, cy = _place_in_box(canvas, product, (40, 300, 660, 1280))
    else:
        draw.rounded_rectangle((100, 360, 660, 1180), radius=28, fill=soft)
        cx, cy = 360, 780

    callout_font = _font(26, "semi")
    box_left, box_right = 760, 1036
    text_pad = 20
    text_max = box_right - box_left - text_pad * 2
    slots = [400, 640, 880]
    for i, text in enumerate(data["callouts"][:3]):
        ty = slots[i]
        wrapped = _wrap(draw, text, callout_font, text_max, max_lines=2)
        line_heights = []
        for ln in wrapped:
            b = draw.textbbox((0, 0), ln, font=callout_font)
            line_heights.append(b[3] - b[1])
        content_h = sum(line_heights) + 6 * max(0, len(wrapped) - 1)
        block_h = max(64, content_h + 28)
        content_w = max((_text_w(draw, ln, callout_font) for ln in wrapped), default=0)
        bw = int(min(box_right - box_left, max(180, content_w + text_pad * 2)))
        bx1 = box_right - bw
        by1 = ty - block_h // 2
        by2 = by1 + block_h
        draw.rounded_rectangle((bx1, by1, box_right, by2), radius=18, fill=white)
        anchor_y = cy - 80 + i * 90
        draw.line((cx + 36, anchor_y, bx1 - 8, ty), fill=accent, width=3)
        draw.ellipse((cx + 28, anchor_y - 8, cx + 44, anchor_y + 8), fill=accent)
        draw.ellipse((bx1 - 14, ty - 8, bx1 + 2, ty + 8), fill=accent)
        _draw_centered_lines(
            draw,
            wrapped,
            callout_font,
            (bx1 + text_pad // 2, by1, box_right - text_pad // 2, by2),
            dark,
            line_gap=6,
        )
    return canvas


def _slide_benefits(data: dict, product: Image.Image | None, palette: tuple[str, str, str, str, str]) -> Image.Image:
    soft, dark, white, accent, deep = palette
    canvas = Image.new("RGBA", (CARD_W, CARD_H), soft)
    draw = ImageDraw.Draw(canvas)

    draw.text((64, 64), "ПРЕИМУЩЕСТВА", font=_font(24, "semi"), fill=accent)
    title_max = CARD_W - 360 if product is not None else CARD_W - 128
    title_font, title_lines, title_size = _fit_title(
        draw, data["title"], title_max, max_lines=2, start_size=42, min_size=30
    )
    y = _draw_lines(draw, title_lines, title_font, 64, 110, dark, max(40, int(title_size * 1.2)))

    if product is not None:
        frame = (CARD_W - 300, 96, CARD_W - 56, 360)
        draw.rounded_rectangle(frame, radius=24, fill=white)
        _place_in_box(
            canvas,
            product,
            (frame[0] + 20, frame[1] + 20, frame[2] - 20, frame[3] - 20),
        )

    items = data["bullets"][:4] or data["callouts"][:4]
    card_font = _font(30, "semi")
    num_font = _font(26, "bold")
    top = max(y + 40, 400)
    gap = 24
    card_h = 156
    card_x1, card_x2 = 64, CARD_W - 64
    circle = 72
    inner_gap = 28
    side_pad = 40

    for i, item in enumerate(items):
        yy = top + i * (card_h + gap)
        if yy + card_h > CARD_H - 120:
            break
        draw.rounded_rectangle((card_x1, yy, card_x2, yy + card_h), radius=28, fill=white)

        # Текст не выходит за карточку; группа (номер+текст) по центру
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

    if data["origin"]:
        _pill(draw, (64, CARD_H - 100), data["origin"], accent, white, _font(22, "semi"))
    return canvas


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
) -> list[Path]:
    """
    Набор из 3 слайдов как у карточек WB/Ozon:
    1) обложка (CTR), 2) детали с выносками, 3) преимущества.
    """
    prepared = _prepare_data(data)
    palette = _palette(prepared["title"], prepared["accent"])
    product = _load_photo(photo)

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
