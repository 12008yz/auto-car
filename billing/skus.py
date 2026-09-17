from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["ask", "summary", "edit", "card"]
SkuId = Literal["pro_month", "credits_20", "credits_50"]
Rail = Literal["unitpay", "stars"]


@dataclass(frozen=True)
class Sku:
    id: SkuId
    title_ru: str
    title_en: str
    credits: int
    days: int  # 0 for credit packs
    price_rub: float
    price_stars: int


SKUS: dict[str, Sku] = {
    "pro_month": Sku(
        id="pro_month",
        title_ru="Pro на 30 дней",
        title_en="Pro for 30 days",
        credits=200,
        days=30,
        price_rub=299.0,
        price_stars=250,
    ),
    "credits_20": Sku(
        id="credits_20",
        title_ru="20 кредитов",
        title_en="20 credits",
        credits=20,
        days=0,
        price_rub=99.0,
        price_stars=80,
    ),
    "credits_50": Sku(
        id="credits_50",
        title_ru="50 кредитов",
        title_en="50 credits",
        credits=50,
        days=0,
        price_rub=199.0,
        price_stars=150,
    ),
}

ACTION_COST: dict[Action, int] = {
    "ask": 1,
    "summary": 2,
    "edit": 3,
    "card": 2,
}

# Free daily caps (edit blocked on free)
FREE_DAILY: dict[Action, int] = {
    "ask": 5,
    "summary": 1,
    "edit": 0,
    "card": 1,
}


def get_sku(sku_id: str) -> Sku:
    sku = SKUS.get(sku_id)
    if sku is None:
        raise KeyError(f"Unknown SKU: {sku_id}")
    return sku
