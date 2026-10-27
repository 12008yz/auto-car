from __future__ import annotations

import unittest


class TestIpNpdGuide(unittest.TestCase):
    def test_steps_mention_tax_and_tochka(self) -> None:
        from docs.ip_npd_guide import STEPS, DISCLAIMER, tochka_url, gosuslugi_url

        joined = "\n".join(s.body for s in STEPS).lower()
        self.assertIn("ип и налог", joined)
        self.assertIn("точк", joined)
        self.assertIn("усн", joined)
        self.assertIn("нпд", joined)
        self.assertIn("самозанят", joined)
        self.assertIn("госуслуг", joined)
        self.assertNotIn("альфа", joined)
        self.assertIn("декларац", joined)
        self.assertIn(DISCLAIMER[:20].lower(), joined)
        self.assertTrue(tochka_url().startswith("http"))
        self.assertTrue(gosuslugi_url().startswith("http"))
        self.assertIn("partner.tochka.com", tochka_url().lower())
        self.assertIn("кнопк", joined)
        self.assertIn("системы налогообложения", joined)
        self.assertIn("гид бесплатный", joined)

    def test_tax_choice_buttons(self) -> None:
        from bot import ui

        why_kb = ui.ip_npd_keyboard("why")
        cbs = {
            b.callback_data
            for row in why_kb.inline_keyboard
            for b in row
            if b.callback_data
        }
        self.assertIn("ipnpd:step:npd_selfemp", cbs)
        self.assertIn("ipnpd:step:usn_reg", cbs)
        self.assertIn("ipnpd:step:other_reg", cbs)

        self_kb = ui.ip_npd_keyboard("npd_selfemp")
        urls = [b.url for row in self_kb.inline_keyboard for b in row if b.url]
        self.assertTrue(any("gosuslugi" in (u or "").lower() for u in urls))
        self.assertTrue(any("nalog" in (u or "").lower() for u in urls))

    def test_partner_cta_buttons(self) -> None:
        from bot import ui

        reg_kb = ui.ip_npd_keyboard("npd_reg")
        tochka_kb = ui.ip_npd_keyboard("tochka")
        end_kb = ui.ip_npd_keyboard("tax")
        reg_labels = [b.text for row in reg_kb.inline_keyboard for b in row]
        tochka_labels = [b.text for row in tochka_kb.inline_keyboard for b in row]
        end_cbs = {
            b.callback_data
            for row in end_kb.inline_keyboard
            for b in row
            if b.callback_data
        }
        self.assertTrue(any("открыть ип" in (t or "").lower() for t in reg_labels))
        self.assertTrue(any("ссылка бота" in (t or "") for t in tochka_labels))
        self.assertIn("menu:docs", end_cbs)
        diy_kb = ui.ip_npd_keyboard("diy")
        diy_urls = [b.url for row in diy_kb.inline_keyboard for b in row if b.url]
        self.assertEqual(len(diy_urls), 1)

    def test_navigation(self) -> None:
        from docs.ip_npd_guide import first_step, next_step_id, step_by_id

        self.assertEqual(first_step().id, "why")
        self.assertIsNone(next_step_id("why"))
        self.assertEqual(next_step_id("npd_selfemp"), "npd_reg")
        self.assertEqual(next_step_id("npd_reg"), "tochka")
        self.assertEqual(next_step_id("usn_reg"), "tochka")
        self.assertEqual(next_step_id("other_reg"), "tochka")
        self.assertEqual(next_step_id("tochka"), "tax")
        self.assertIsNone(next_step_id("tax"))
        self.assertEqual(step_by_id("alfa").id, "npd_selfemp")  # type: ignore[union-attr]
        self.assertEqual(step_by_id("npd_check").id, "tax")  # type: ignore[union-attr]

    def test_ui_entry(self) -> None:
        from bot import ui

        self.assertEqual(ui.BTN_IP_NPD, "Открыть ИП")
        help_l = ui.help_text().lower()
        self.assertIn("открыть ип", help_l)
        self.assertIn("точк", help_l)
        self.assertNotIn("альфа", help_l)
        self.assertIn("menu:ip_npd", {
            b.callback_data
            for row in ui.home_inline().inline_keyboard
            for b in row
            if b.callback_data
        })


if __name__ == "__main__":
    unittest.main()
