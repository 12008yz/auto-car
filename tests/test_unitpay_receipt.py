from __future__ import annotations

import base64
import json
import unittest
from urllib.parse import parse_qs, urlparse


class TestUnitpayReceipt(unittest.TestCase):
    def test_encode_cash_items_roundtrip(self) -> None:
        from billing.unitpay import encode_cash_items

        raw = encode_cash_items(
            name="Pro на 30 дней",
            price=299.0,
            nds="none",
            item_type="service",
        )
        decoded = json.loads(base64.b64decode(raw).decode("utf-8"))
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["name"], "Pro на 30 дней")
        self.assertEqual(decoded[0]["count"], 1)
        self.assertEqual(decoded[0]["price"], 299.0)
        self.assertEqual(decoded[0]["nds"], "none")
        self.assertEqual(decoded[0]["type"], "service")
        self.assertEqual(decoded[0]["paymentMethod"], "full_payment")

    def test_build_payment_url_includes_receipt(self) -> None:
        import config
        from billing.unitpay import build_payment_url

        old_pub = config.UNITPAY_PUBLIC_KEY
        old_sec = config.UNITPAY_SECRET_KEY
        try:
            config.UNITPAY_PUBLIC_KEY = "public-test"
            config.UNITPAY_SECRET_KEY = "secret-test"
            url = build_payment_url(
                account="order-1",
                sum_value=99.0,
                desc="20 кредитов",
                customer_email="buyer@example.com",
                receipt_name="20 кредитов",
                receipt_nds="none",
            )
            parsed = urlparse(url)
            self.assertTrue(parsed.path.endswith("/public-test"))
            qs = parse_qs(parsed.query)
            self.assertEqual(qs["customerEmail"], ["buyer@example.com"])
            self.assertIn("cashItems", qs)
            items = json.loads(base64.b64decode(qs["cashItems"][0]).decode("utf-8"))
            self.assertEqual(items[0]["name"], "20 кредитов")
            self.assertEqual(items[0]["price"], 99.0)
        finally:
            config.UNITPAY_PUBLIC_KEY = old_pub
            config.UNITPAY_SECRET_KEY = old_sec

    def test_email_validation(self) -> None:
        from billing.unitpay import is_valid_email

        self.assertTrue(is_valid_email("a@b.co"))
        self.assertFalse(is_valid_email("not-an-email"))
        self.assertFalse(is_valid_email(""))

    def test_public_base_rejects_placeholder(self) -> None:
        import config
        from billing.pay import _public_base_ready

        old = config.BILLING_PUBLIC_BASE_URL
        try:
            config.BILLING_PUBLIC_BASE_URL = ""
            self.assertFalse(_public_base_ready())
            config.BILLING_PUBLIC_BASE_URL = "https://YOUR_DOMAIN"
            self.assertFalse(_public_base_ready())
            config.BILLING_PUBLIC_BASE_URL = "http://pay.example.org"
            self.assertFalse(_public_base_ready())
            config.BILLING_PUBLIC_BASE_URL = "https://localhost"
            self.assertFalse(_public_base_ready())
            config.BILLING_PUBLIC_BASE_URL = "https://pay.example.org"
            self.assertTrue(_public_base_ready())
        finally:
            config.BILLING_PUBLIC_BASE_URL = old


if __name__ == "__main__":
    unittest.main()
