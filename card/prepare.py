from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

log = logging.getLogger(__name__)

_session = None


def _get_rembg_session():
    """Ленивая загрузка rembg — модель тяжелая, поднимаем один раз."""
    global _session
    if _session is not None:
        return _session
    from rembg import new_session

    # u2net — баланс качества/скорости для товаров
    _session = new_session("u2net")
    return _session


def _has_useful_alpha(img: Image.Image, *, min_transparent_ratio: float = 0.08) -> bool:
    if img.mode != "RGBA":
        return False
    alpha = img.split()[-1]
    hist = alpha.histogram()
    transparent = sum(hist[:200])  # почти прозрачные
    total = img.width * img.height or 1
    return (transparent / total) >= min_transparent_ratio


def _trim_alpha(img: Image.Image, pad: int = 12) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    bbox = img.split()[-1].getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(img.width, right + pad)
    bottom = min(img.height, bottom + pad)
    return img.crop((left, top, right, bottom))


def _soft_polish(img: Image.Image) -> Image.Image:
    """Лёгкий контраст/резкость без перерисовки и без порчи альфы."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    r, g, b, a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    rgb = ImageEnhance.Contrast(rgb).enhance(1.06)
    rgb = ImageEnhance.Color(rgb).enhance(1.04)
    rgb = ImageEnhance.Sharpness(rgb).enhance(1.08)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def remove_background(image_bytes: bytes) -> bytes:
    """Вырезает фон через rembg, возвращает PNG RGBA."""
    from rembg import remove

    session = _get_rembg_session()
    result = remove(image_bytes, session=session)
    img = Image.open(BytesIO(result)).convert("RGBA")
    # сгладить край маски
    alpha = img.split()[-1].filter(ImageFilter.GaussianBlur(0.6))
    img.putalpha(alpha)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def prepare_studio_product(
    src: Path | bytes,
    dest: Path,
    *,
    force_rembg: bool = False,
    skip_rembg: bool = False,
) -> Path:
    """
    Post-process студийного/исходного фото:
    - rembg, если нет полезной прозрачности (или force_rembg);
    - для lifestyle (skip_rembg) фон сцены сохраняем;
    - trim по альфе (только если есть альфа);
    - лёгкий polish;
    - сохранить PNG.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(src, (bytes, bytearray)):
        raw = bytes(src)
    else:
        raw = Path(src).read_bytes()

    img = Image.open(BytesIO(raw))
    if img.mode not in {"RGBA", "RGB"}:
        img = img.convert("RGBA")
    else:
        img = img.convert("RGBA")

    need_cut = (not skip_rembg) and (force_rembg or not _has_useful_alpha(img))
    if need_cut:
        try:
            cut = remove_background(raw)
            img = Image.open(BytesIO(cut)).convert("RGBA")
            log.info("prepare_studio_product: rembg applied -> %s", dest.name)
        except Exception as exc:
            log.warning("prepare_studio_product: rembg failed (%s), keep original", exc)
            img = Image.open(BytesIO(raw)).convert("RGBA")

    if _has_useful_alpha(img):
        img = _trim_alpha(img)
    img = _soft_polish(img)
    img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    img.save(dest, format="PNG", optimize=True)
    return dest


def prepare_fallback_cutout(src: Path, dest: Path) -> Path:
    """Fallback: вырезать товар из сырого кадра пользователя, если AI-студия упала."""
    return prepare_studio_product(src, dest, force_rembg=True)
