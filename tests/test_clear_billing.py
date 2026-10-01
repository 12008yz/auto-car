from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestClearKeepsBilling(unittest.TestCase):
    def test_clear_workspace_does_not_reset_wallet(self) -> None:
        """Очистка чата не должна обнулять кредиты и дневные попытки."""
        import config
        from billing.db import init_db
        from billing.service import admin_grant_credits, consume, ensure_user, get_balance
        from bot.session import clear_user_workspace, get_session
        from bot import session as session_mod

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "billing.sqlite3"
            data_root = Path(tmp) / "users"
            data_root.mkdir()
            old_db = config.BILLING_DB_PATH
            old_open = config.BILLING_OPEN_ACCESS
            config.BILLING_DB_PATH = db_path
            config.BILLING_OPEN_ACCESS = False
            session_mod._sessions.clear()
            try:
                init_db()
                uid = 777001
                ensure_user(uid, "ru")
                admin_grant_credits(uid, 50, "ru")
                # Кредиты списываются раньше дневных лимитов
                self.assertTrue(consume(uid, "ask", "ru").ok)
                before_credits = get_balance(uid, "ru")
                self.assertEqual(before_credits.credits, 49)

                # Отдельный пользователь без кредитов — только дневной счётчик
                uid2 = 777003
                ensure_user(uid2, "ru")
                for _ in range(3):
                    self.assertTrue(consume(uid2, "ask", "ru").ok)
                before_daily = get_balance(uid2, "ru")
                self.assertEqual(before_daily.credits, 0)
                self.assertEqual(before_daily.daily_ask, 3)

                user_dir = data_root / str(uid)
                user_dir.mkdir()
                (user_dir / "doc.txt").write_text("hi", encoding="utf-8")
                get_session(uid).remember_doc_task("ask", "вопрос")
                get_session(uid).chat_message_ids.append(100)

                clear_user_workspace(uid, data_root)
                clear_user_workspace(uid2, data_root)

                after_credits = get_balance(uid, "ru")
                after_daily = get_balance(uid2, "ru")
                self.assertEqual(after_credits.credits, 49, msg="кредиты не должны слетать")
                self.assertEqual(
                    after_daily.daily_ask, 3, msg="дневные попытки не должны обнуляться"
                )
                self.assertFalse((data_root / str(uid)).exists())
                self.assertIsNone(get_session(uid).doc_task)
            finally:
                config.BILLING_DB_PATH = old_db
                config.BILLING_OPEN_ACCESS = old_open
                session_mod._sessions.clear()

    def test_open_access_still_counts_daily_usage(self) -> None:
        """В открытом доступе лимиты не блокируют, но счётчики растут."""
        import config
        from billing.db import init_db
        from billing.service import consume, ensure_user, get_balance
        from bot import session as session_mod

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "billing.sqlite3"
            old_db = config.BILLING_DB_PATH
            old_open = config.BILLING_OPEN_ACCESS
            config.BILLING_DB_PATH = db_path
            config.BILLING_OPEN_ACCESS = True
            session_mod._sessions.clear()
            try:
                init_db()
                uid = 777002
                ensure_user(uid, "ru")
                r = consume(uid, "ask", "ru")
                self.assertTrue(r.ok)
                self.assertEqual(r.charged, "open")
                bal = get_balance(uid, "ru")
                self.assertEqual(bal.daily_ask, 1)
                for _ in range(5):
                    self.assertTrue(consume(uid, "ask", "ru").ok)
                self.assertEqual(get_balance(uid, "ru").daily_ask, 6)
            finally:
                config.BILLING_DB_PATH = old_db
                config.BILLING_OPEN_ACCESS = old_open
                session_mod._sessions.clear()

    def test_open_access_refund_undoes_counter(self) -> None:
        """Сбой после consume в open access должен откатить дневной счётчик."""
        import config
        from billing.db import init_db
        from billing.service import consume, ensure_user, get_balance, refund_consume
        from bot import session as session_mod

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "billing.sqlite3"
            old_db = config.BILLING_DB_PATH
            old_open = config.BILLING_OPEN_ACCESS
            config.BILLING_DB_PATH = db_path
            config.BILLING_OPEN_ACCESS = True
            session_mod._sessions.clear()
            try:
                init_db()
                uid = 777004
                ensure_user(uid, "ru")
                charge = consume(uid, "edit", "ru")
                self.assertEqual(charge.charged, "open")
                self.assertEqual(get_balance(uid, "ru").daily_write, 1)
                refund_consume(uid, charge, "edit")
                self.assertEqual(get_balance(uid, "ru").daily_write, 0)
            finally:
                config.BILLING_DB_PATH = old_db
                config.BILLING_OPEN_ACCESS = old_open
                session_mod._sessions.clear()


if __name__ == "__main__":
    unittest.main()
