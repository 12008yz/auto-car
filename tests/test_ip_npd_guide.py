from __future__ import annotations

import unittest


class TestIpNpdGuide(unittest.TestCase):
    def test_steps_mention_tax_and_banks(self) -> None:
        from docs.ip_npd_guide import STEPS, DISCLAIMER, alfa_url, tochka_url

        joined = "\n".join(s.body for s in STEPS).lower()
        self.assertIn("4%", joined)
        self.assertIn("6%", joined)
        self.assertIn("альфа", joined)
        self.assertIn("точк", joined)
        self.assertIn("госпошлин", joined)
        self.assertIn(DISCLAIMER[:20].lower(), joined)
        self.assertTrue(alfa_url().startswith("http"))
        self.assertTrue(tochka_url().startswith("http"))
        self.assertNotIn("примет всех", joined)
        self.assertIn("без госпошлин", joined)
        self.assertIn("декларац", joined)
        self.assertIn("кнопкой ниже", joined)
        self.assertIn("гид по ип бесплатный", joined)

    def test_partner_cta_buttons(self) -> None:
        from bot import ui

        alfa_kb = ui.ip_npd_keyboard("alfa")
        tochka_kb = ui.ip_npd_keyboard("tochka")
        end_kb = ui.ip_npd_keyboard("npd_check")
        alfa_labels = [b.text for row in alfa_kb.inline_keyboard for b in row]
        tochka_labels = [b.text for row in tochka_kb.inline_keyboard for b in row]
        end_cbs = {
            b.callback_data
            for row in end_kb.inline_keyboard
            for b in row
            if b.callback_data
        }
        self.assertTrue(any("ссылка бота" in (t or "") for t in alfa_labels))
        self.assertTrue(any("ссылка бота" in (t or "") for t in tochka_labels))
        self.assertIn("menu:docs", end_cbs)
        self.assertIn("menu:card", end_cbs)
        self.assertIn("menu:plans", end_cbs)
        diy_kb = ui.ip_npd_keyboard("diy")
        diy_urls = [b.url for row in diy_kb.inline_keyboard for b in row if b.url]
        diy_cbs = {
            b.callback_data
            for row in diy_kb.inline_keyboard
            for b in row
            if b.callback_data
        }
        self.assertEqual(len(diy_urls), 2)
        self.assertTrue(all(u.startswith("http") for u in diy_urls))
        self.assertIn("ipnpd:step:why", diy_cbs)

    def test_navigation(self) -> None:
        from docs.ip_npd_guide import first_step, next_step_id, step_by_id

        self.assertEqual(first_step().id, "why")
        self.assertEqual(next_step_id("why"), "alfa")
        self.assertEqual(next_step_id("alfa"), "tochka")
        self.assertEqual(next_step_id("tochka"), "npd_check")
        self.assertIsNone(next_step_id("npd_check"))
        self.assertIsNotNone(step_by_id("tochka"))

    def test_ui_entry(self) -> None:
        from bot import ui

        home = {
            btn.callback_data
            for row in ui.home_inline().inline_keyboard
            for btn in row
            if btn.callback_data
        }
        self.assertIn("menu:ip_npd", home)
        self.assertIn(ui.BTN_IP_NPD, ui.REPLY_BUTTONS)
        labels = [
            btn.text
            for row in ui.main_reply_keyboard().keyboard
            for btn in row
        ]
        self.assertIn(ui.BTN_IP_NPD, labels)
        self.assertIn("бесплатно", ui.BTN_IP_NPD.lower())
        help_l = ui.help_text().lower()
        self.assertIn("ип на нпд", help_l)


if __name__ == "__main__":
    unittest.main()
