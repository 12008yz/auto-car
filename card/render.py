from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CARD_W = 1080
CARD_H = 1440
PHOTO_H = 860
PADDING = 48

_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\calibri.ttf"),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = []
    if bold:
        names.extend(
            [
                Path(r"C:\Windows\Fonts\segoeuib.ttf"),
                Path(r"C:\Windows\Fonts\arialbd.ttf"),
            ]
        )
    names.extend(_FONT_CANDIDATES)
    for path in names:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_product_card(
    dest: Path,
    data: dict,
    photo: Path | None = None,
) -> Path:
    img = Image.new("RGB", (CARD_W, CARD_H), "#111827")
    draw = ImageDraw.Draw(img)

    if photo and photo.exists():
        try:
            product = Image.open(photo).convert("RGB")
            product = ImageOps.fit(product, (CARD_W, PHOTO_H), Image.Resampling.LANCZOS)
            img.paste(product, (0, 0))
            overlay = Image.new("RGB", (CARD_W, CARD_H - PHOTO_H + 40), "#111827")
            img.paste(overlay, (0, PHOTO_H - 40))
            text_top = PHOTO_H + 8
        except Exception:
            photo = None
    if not photo or not photo.exists():
        draw.rectangle((0, 0, CARD_W, 220), fill="#2563EB")
        text_top = 80

    title_font = _font(52, bold=True)
    sub_font = _font(32)
    bullet_font = _font(30)
    max_w = CARD_W - PADDING * 2

    title = str(data.get("title") or "Карточка товара")
    y = text_top
    for line in _wrap(draw, title, title_font, max_w)[:3]:
        draw.text((PADDING, y), line, font=title_font, fill="#F9FAFB")
        y += 62

    subtitle = str(data.get("subtitle") or "").strip()
    if subtitle:
        y += 8
        for line in _wrap(draw, subtitle, sub_font, max_w)[:2]:
            draw.text((PADDING, y), line, font=sub_font, fill="#93C5FD")
            y += 40

    y += 24
    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = []
    for item in bullets[:5]:
        text = str(item).strip()
        if not text:
            continue
        wrapped = _wrap(draw, "• " + text, bullet_font, max_w)
        for line in wrapped[:3]:
            if y > CARD_H - 70:
                break
            draw.text((PADDING, y), line, font=bullet_font, fill="#E5E7EB")
            y += 40
        y += 8

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="PNG", optimize=True)
    return dest
