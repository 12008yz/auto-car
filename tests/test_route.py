"""
Phase A: таблица маршрутизации текста с учётом режимов сессии.
Без Telegram и без LLM refine.

Запуск:
  .venv\\Scripts\\python.exe -m unittest tests.test_route -v
"""

from __future__ import annotations

import unittest

from bot.route import TextRouteContext, resolve_text_route


# ctx kwargs, text, expected action, expected intent (для dispatch)
ROUTE_CASES: list[tuple[dict, str, str, str]] = [
    # Регрессия: карточка + файл + короткое имя товара
    ({"card_mode": True, "has_files": True}, "Стол", "card", ""),
    ({"card_mode": True, "has_files": True}, "стол", "card", ""),
    ({"card_mode": True}, "керамическая кружка 300 мл", "card", ""),
    ({"card_mode": True}, "Нужно продать такую кружку", "card", ""),
    ({"card_mode": True}, "отмена", "cancel_card", ""),
    ({"card_mode": True}, "стоп", "cancel_card", ""),
    ({"card_mode": True}, "привет", "exit_card_menu", ""),
    # Явная правка файла выводит из карточки
    (
        {"card_mode": True, "has_files": True},
        "Замени ФИО на Иванова",
        "dispatch",
        "edit",
    ),
    # Без card_mode — обычная классификация
    (
        {"has_files": True},
        "Что в договоре про срок оплаты?",
        "dispatch",
        "ask",
    ),
    (
        {"has_files": True, "awaiting_gap_fill": True},
        "Богородицк",
        "dispatch",
        "gap_fill",
    ),
    (
        {"has_files": True},
        "Проверь договор на риски",
        "dispatch",
        "risks",
    ),
    (
        {},
        "Напиши реферат на тему ИИ",
        "dispatch",
        "write_text",
    ),
]


class TestResolveTextRoute(unittest.TestCase):
    def test_route_table(self) -> None:
        for ctx_kwargs, text, action, intent in ROUTE_CASES:
            with self.subTest(text=text, ctx=ctx_kwargs):
                ctx = TextRouteContext(**ctx_kwargs)
                route = resolve_text_route(text, ctx)
                self.assertEqual(route.action, action)
                if action == "dispatch" and intent:
                    self.assertEqual(route.intent, intent)

    def test_card_mode_short_name_not_ask(self) -> None:
        """Главная регрессия: «Стол» при файле не должен стать ask."""
        route = resolve_text_route(
            "Стол",
            TextRouteContext(card_mode=True, has_files=True),
        )
        self.assertEqual(route.action, "card")
        self.assertNotEqual(route.intent, "ask")

    def test_edit_from_card_clears_mode_flag(self) -> None:
        route = resolve_text_route(
            "Замени ФИО на Иванова",
            TextRouteContext(card_mode=True, has_files=True),
        )
        self.assertEqual(route.action, "dispatch")
        self.assertTrue(route.clear_card_mode)


if __name__ == "__main__":
    unittest.main()
