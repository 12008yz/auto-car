"""Кириллица на слайдах карточки — нужен TTF, не PIL default."""

from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]


class TestCardFonts(unittest.TestCase):
    def test_bundled_dejavu_exists(self) -> None:
        fonts = ROOT / "card" / "fonts"
        self.assertTrue((fonts / "DejaVuSans.ttf").is_file())
        self.assertTrue((fonts / "DejaVuSans-Bold.ttf").is_file())

    def test_font_renders_cyrillic(self) -> None:
        from card.render import _font

        font = _font(36, "bold")
        self.assertTrue(hasattr(font, "getbbox") or hasattr(font, "getsize"))
        img = Image.new("RGB", (480, 80), "#F3E9E4")
        draw = ImageDraw.Draw(img)
        text = "Стол деревянный"
        draw.text((10, 20), text, font=font, fill="#1C1917")
        # У «тофу»/bitmap default ширина кириллицы часто 0 или крошечная
        width = float(draw.textlength(text, font=font))
        self.assertGreater(width, 120, msg=f"Cyrillic width too small: {width}")


if __name__ == "__main__":
    unittest.main()
