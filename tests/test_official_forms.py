from __future__ import annotations

import unittest


class TestOfficialFormsCatalog(unittest.TestCase):
    def test_match_r21001(self) -> None:
        from docs.official_forms import match_official_forms

        hits = match_official_forms("Нужен официальный бланк Р21001 — регистрация ИП")
        self.assertTrue(hits)
        self.assertEqual(hits[0][0].id, "r21001")
        self.assertTrue(hits[0][0].file_url)

    def test_match_usn_decl_and_patent(self) -> None:
        from docs.official_forms import match_official_forms

        self.assertEqual(
            match_official_forms("Официальная декларация по УСН")[0][0].id,
            "usn_decl",
        )
        self.assertEqual(
            match_official_forms("Заявление на патент официальное")[0][0].id,
            "patent",
        )

    def test_title_page_is_not_official(self) -> None:
        from docs.official_forms import looks_like_official_form_request
        from llm.client import classify_document_intent

        text = (
            "Сделай титульный лист налоговой декларации для ИП — "
            "только первая страница, поля пустыми для заполнения"
        )
        self.assertFalse(looks_like_official_form_request(text))
        self.assertEqual(classify_document_intent(text)["intent"], "write_form")

    def test_official_phrase_routes(self) -> None:
        from llm.client import classify_document_intent

        d = classify_document_intent("Нужен официальный бланк Р21001 — регистрация ИП")
        self.assertEqual(d["intent"], "official_form")

    def test_allowed_hosts_and_disclaimer(self) -> None:
        from docs.official_forms import (
            ALLOWED_HOSTS,
            DISCLAIMER_RU,
            OFFICIAL_FORMS,
            format_form_caption,
            is_allowed_url,
        )

        self.assertIn("www.nalog.gov.ru", ALLOWED_HOSTS)
        for form in OFFICIAL_FORMS:
            self.assertTrue(is_allowed_url(form.page_url), form.id)
            if form.file_url:
                self.assertTrue(is_allowed_url(form.file_url), form.id)
            cap = format_form_caption(form, with_file=True)
            self.assertIn(DISCLAIMER_RU[:40], cap)
            self.assertIn(form.page_url, cap)

    def test_form_example_mentions_official(self) -> None:
        from bot import ui

        text = ui.docs_form_example_text().lower()
        self.assertIn("официальн", text)
        self.assertIn("р21001", text)
        self.assertIn("фнс", text)
        self.assertIn("титульный", text)


if __name__ == "__main__":
    unittest.main()
