from __future__ import annotations

import unittest


class TestLegalSite(unittest.TestCase):
    def test_pages_render(self) -> None:
        import config
        from billing.legal_site import (
            merchant_ready,
            page_contacts,
            page_home,
            page_offer,
            page_payment,
            page_privacy,
            page_refund,
            page_security,
        )

        old = {
            "name": config.MERCHANT_LEGAL_NAME,
            "inn": config.MERCHANT_INN,
            "ogrn": config.MERCHANT_OGRNIP,
            "addr": config.MERCHANT_ADDRESS,
            "email": config.MERCHANT_EMAIL,
            "phone": config.MERCHANT_PHONE,
            "bot": config.BOT_USERNAME,
            "base": config.BILLING_PUBLIC_BASE_URL,
        }
        try:
            config.MERCHANT_LEGAL_NAME = "ИП Тестов Т.Т."
            config.MERCHANT_INN = "123456789012"
            config.MERCHANT_OGRNIP = "123456789012345"
            config.MERCHANT_ADDRESS = "г. Москва"
            config.MERCHANT_EMAIL = "pay@example.org"
            config.MERCHANT_PHONE = ""
            config.BOT_USERNAME = "example_bot"
            config.BILLING_PUBLIC_BASE_URL = "https://pay.example.org"

            self.assertTrue(merchant_ready())

            async def _run() -> None:
                from aiohttp.web import Request
                from unittest.mock import MagicMock

                req = MagicMock(spec=Request)
                for fn in (
                    page_home,
                    page_offer,
                    page_privacy,
                    page_refund,
                    page_security,
                    page_payment,
                    page_contacts,
                ):
                    resp = await fn(req)
                    text = resp.text or ""
                    self.assertIn("ИП Тестов", text)
                    self.assertIn("123456789012", text)
                    self.assertNotIn("кв.", text.lower())
                    self.assertEqual(resp.content_type, "text/html")
                contacts = await page_contacts(req)
                self.assertIn("через Telegram-бота", contacts.text or "")

            import asyncio

            asyncio.run(_run())
        finally:
            config.MERCHANT_LEGAL_NAME = old["name"]
            config.MERCHANT_INN = old["inn"]
            config.MERCHANT_OGRNIP = old["ogrn"]
            config.MERCHANT_ADDRESS = old["addr"]
            config.MERCHANT_EMAIL = old["email"]
            config.MERCHANT_PHONE = old["phone"]
            config.BOT_USERNAME = old["bot"]
            config.BILLING_PUBLIC_BASE_URL = old["base"]


if __name__ == "__main__":
    unittest.main()
