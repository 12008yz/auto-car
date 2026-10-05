from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


def _reset_billing_module() -> None:
    import billing.service as svc

    svc._initialized = False


class TestBillingDailyFree(unittest.TestCase):
    def _ctx(self):
        import config
        from bot import session as session_mod

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(tmp.name)
        db_path = root / "billing.sqlite3"
        old_db = config.BILLING_DB_PATH
        old_open = config.BILLING_OPEN_ACCESS
        config.BILLING_DB_PATH = db_path
        config.BILLING_OPEN_ACCESS = False
        session_mod._sessions.clear()
        _reset_billing_module()
        return tmp, old_db, old_open, session_mod

    def _restore(self, tmp, old_db, old_open, session_mod) -> None:
        import config

        config.BILLING_DB_PATH = old_db
        config.BILLING_OPEN_ACCESS = old_open
        session_mod._sessions.clear()
        _reset_billing_module()
        tmp.cleanup()

    def test_daily_first_then_credits_for_card(self) -> None:
        from billing.db import init_db
        from billing.service import admin_grant_credits, consume, ensure_user, get_balance

        tmp, old_db, old_open, session_mod = self._ctx()
        try:
            init_db()
            uid = 880001
            ensure_user(uid, "ru")
            admin_grant_credits(uid, 10, "ru")
            r1 = consume(uid, "card", "ru")
            self.assertTrue(r1.ok)
            self.assertEqual(r1.charged, "daily")
            self.assertEqual(get_balance(uid, "ru").credits, 10)
            r2 = consume(uid, "card", "ru")
            self.assertTrue(r2.ok)
            self.assertEqual(r2.charged, "credits")
            self.assertEqual(r2.amount, 2)
            self.assertEqual(get_balance(uid, "ru").credits, 8)
        finally:
            self._restore(tmp, old_db, old_open, session_mod)

    def test_ask_summary_share_five(self) -> None:
        from billing.db import init_db
        from billing.service import consume, ensure_user, get_balance

        tmp, old_db, old_open, session_mod = self._ctx()
        try:
            init_db()
            uid = 880002
            ensure_user(uid, "ru")
            for _ in range(3):
                self.assertEqual(consume(uid, "ask", "ru").charged, "daily")
            for _ in range(2):
                self.assertEqual(consume(uid, "summary", "ru").charged, "daily")
            self.assertEqual(get_balance(uid, "ru").daily_ask, 5)
            blocked = consume(uid, "ask", "ru")
            self.assertFalse(blocked.ok)
            self.assertIn("бесплатная попытка", blocked.message.lower())
        finally:
            self._restore(tmp, old_db, old_open, session_mod)

    def test_edit_requires_credits(self) -> None:
        from billing.db import init_db
        from billing.service import admin_grant_credits, consume, ensure_user, get_balance

        tmp, old_db, old_open, session_mod = self._ctx()
        try:
            init_db()
            uid = 880003
            ensure_user(uid, "ru")
            denied = consume(uid, "edit", "ru")
            self.assertFalse(denied.ok)
            self.assertIn("3", denied.message)
            admin_grant_credits(uid, 3, "ru")
            ok = consume(uid, "edit", "ru")
            self.assertTrue(ok.ok)
            self.assertEqual(ok.charged, "credits")
            self.assertEqual(get_balance(uid, "ru").credits, 0)
        finally:
            self._restore(tmp, old_db, old_open, session_mod)

    def test_write_one_free(self) -> None:
        from billing.db import init_db
        from billing.service import consume, ensure_user

        tmp, old_db, old_open, session_mod = self._ctx()
        try:
            init_db()
            uid = 880004
            ensure_user(uid, "ru")
            self.assertEqual(consume(uid, "write", "ru").charged, "daily")
            self.assertFalse(consume(uid, "write", "ru").ok)
        finally:
            self._restore(tmp, old_db, old_open, session_mod)

    def test_summary_refund_uses_ask_counter(self) -> None:
        from billing.db import init_db
        from billing.service import consume, ensure_user, get_balance, refund_consume

        tmp, old_db, old_open, session_mod = self._ctx()
        try:
            init_db()
            uid = 880005
            ensure_user(uid, "ru")
            charge = consume(uid, "summary", "ru")
            self.assertEqual(charge.charged, "daily")
            self.assertEqual(get_balance(uid, "ru").daily_ask, 1)
            refund_consume(uid, charge, "summary")
            self.assertEqual(get_balance(uid, "ru").daily_ask, 0)
        finally:
            self._restore(tmp, old_db, old_open, session_mod)

    def test_plans_copy_mentions_costs(self) -> None:
        from billing.pay import plans_text

        text = plans_text(True)
        self.assertIn("1 карточка", text)
        self.assertIn("5 вопросов", text)
        self.assertIn("правка Word — 3", text)
        self.assertIn("за кредиты", text)
        self.assertIn("+200 кредитов", text)
        self.assertNotIn("до 100 вопросов", text)


if __name__ == "__main__":
    unittest.main()
