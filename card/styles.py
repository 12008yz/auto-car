from __future__ import annotations

from pathlib import Path

# Ротация: 1-я карточка catalog, 2-я lifestyle, 3-я editorial, …
VARIANTS = ("catalog", "lifestyle", "editorial")

VARIANT_TITLES = {
    "catalog": "каталог",
    "lifestyle": "в интерьере",
    "editorial": "постер",
}

# Типичные сцены, если модель не вернула scene
_SCENE_HINTS: dict[str, str] = {
    "ГОРШОК": "on a sunny windowsill with soft daylight and a bit of greenery nearby",
    "КАШПО": "on a wooden shelf near a bright window",
    "ЦВЕТОК": "on a windowsill with natural morning light",
    "ЛАМПА": "on a wooden desk or nightstand in a cozy room",
    "СВЕТИЛЬНИК": "on a side table in a modern living room",
    "КРУЖКА": "on a kitchen table with soft morning light",
    "ЧАШКА": "on a cafe table with warm ambient light",
    "СТУЛ": "in a bright living room next to a dining table",
    "КРЕСЛО": "in a cozy living room corner on a rug",
    "СТОЛ": "in a modern dining room with natural light",
    "ВАЗА": "on a console table against a light wall",
    "ПОДУШКА": "on a sofa in a bright living room",
    "ОДЕЯЛО": "folded on the edge of a made bed",
    "КОВРИК": "on a wooden floor in a bright hallway",
    "БУТЫЛКА": "on a kitchen counter near a window",
    "КРЕМ": "on a clean bathroom shelf",
    "ШАМПУНЬ": "in a bright bathroom on a shelf",
    "НАУШНИКИ": "on a desk next to a laptop",
    "ТЕЛЕФОН": "on a wooden desk with soft daylight",
    "СУМКА": "on a chair in a bright hallway",
    "ОБУВЬ": "on a clean floor near an entryway (product only, no people)",
}


def variant_for_index(index: int) -> str:
    return VARIANTS[int(index) % len(VARIANTS)]


def load_card_count(user_dir: Path) -> int:
    path = Path(user_dir) / "card_style_count.txt"
    if not path.exists():
        return 0
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except ValueError:
        return 0


def save_card_count(user_dir: Path, count: int) -> None:
    path = Path(user_dir) / "card_style_count.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(max(0, int(count))), encoding="utf-8")


def peek_variant(user_dir: Path) -> str:
    return variant_for_index(load_card_count(user_dir))


def bump_card_count(user_dir: Path) -> int:
    """После успешной карточки. Возвращает новый count."""
    n = load_card_count(user_dir) + 1
    save_card_count(user_dir, n)
    return n


def scene_for_product(label: str, title: str = "", scene: str = "") -> str:
    """Английская сцена размещения товара."""
    custom = (scene or "").strip()
    if custom and len(custom) > 8:
        return custom[:220]
    key = (label or "").strip().upper()
    if key in _SCENE_HINTS:
        return _SCENE_HINTS[key]
    # эвристики по словам
    blob = f"{key} {(title or '').upper()}"
    rules = (
        (("ГОРШ", "КАШП", "РАСТЕН", "ЦВЕТ"), _SCENE_HINTS["ГОРШОК"]),
        (("ЛАМП", "СВЕТИЛ", "НОЧНИК"), _SCENE_HINTS["ЛАМПА"]),
        (("КРУЖ", "ЧАШК", "БОКАЛ"), _SCENE_HINTS["КРУЖКА"]),
        (("СТУЛ", "КРЕСЛ", "ПУФ"), _SCENE_HINTS["СТУЛ"]),
        (("ВАЗ",), _SCENE_HINTS["ВАЗА"]),
        (("ПОДУШ", "ПЛЕД", "ОДЕЯЛ"), _SCENE_HINTS["ПОДУШКА"]),
        (("НАУШН", "ГАДЖЕТ"), _SCENE_HINTS["НАУШНИКИ"]),
        (("СУМК", "РЮКЗАК"), _SCENE_HINTS["СУМКА"]),
        (("КРЕМ", "СЫВОРОТ", "ШАМПУН"), _SCENE_HINTS["КРЕМ"]),
    )
    for keys, hint in rules:
        if any(k in blob for k in keys):
            return hint
    return "in a bright modern interior on a suitable surface, soft natural daylight"
