from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


def _reset_billing_module() -> None:
    import billing.service as svc

    svc._initialized = False


class TestAdminStats(unittest.TestCase):
    def _ctx(self):
        import config
        from bot import session as session_mod

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(tmp.name)
        db_path = root / "billing.sqlite3"
        old_db = config.BILLING_DB_PATH
        old_open = config.BILLING_OPEN_ACCESS
        old_admins = set(config.BILLING_ADMIN_IDS)
        config.BILLING_DB_PATH = db_path
        config.BILLING_OPEN_ACCESS = False
        # Mutate in place — handlers imports the set by reference.
        config.BILLING_ADMIN_IDS.clear()
        config.BILLING_ADMIN_IDS.add(900001)
        session_mod._sessions.clear()
        _reset_billing_module()
        return tmp, old_db, old_open, old_admins, session_mod

    def _restore(self, tmp, old_db, old_open, old_admins, session_mod) -> None:
        import config

        config.BILLING_DB_PATH = old_db
        config.BILLING_OPEN_ACCESS = old_open
        config.BILLING_ADMIN_IDS.clear()
        config.BILLING_ADMIN_IDS.update(old_admins)
        session_mod._sessions.clear()
        _reset_billing_module()
        tmp.cleanup()

    def test_consume_records_usage_daily(self) -> None:
        from billing.db import connect, init_db
        from billing.service import consume, ensure_user, _today

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            uid = 770001
            ensure_user(uid, "ru")
            self.assertTrue(consume(uid, "ask", "ru").ok)
            self.assertTrue(consume(uid, "card", "ru").ok)
            with connect() as conn:
                row = conn.execute(
                    "SELECT ask, card, summary, write, edit FROM usage_daily "
                    "WHERE telegram_id = ? AND day = ?",
                    (uid, _today()),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row["ask"]), 1)
            self.assertEqual(int(row["card"]), 1)
            self.assertEqual(int(row["summary"]), 0)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_failed_consume_does_not_record(self) -> None:
        from billing.db import connect, init_db
        from billing.service import consume, ensure_user, _today

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            uid = 770002
            ensure_user(uid, "ru")
            blocked = consume(uid, "edit", "ru")
            self.assertFalse(blocked.ok)
            with connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM usage_daily WHERE telegram_id = ? AND day = ?",
                    (uid, _today()),
                ).fetchone()
            self.assertIsNone(row)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_open_access_records_write_and_edit(self) -> None:
        import config
        from billing.db import connect, init_db
        from billing.service import consume, ensure_user, get_admin_stats

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            config.BILLING_OPEN_ACCESS = True
            init_db()
            uid = 770003
            ensure_user(uid, "ru")
            self.assertTrue(consume(uid, "write", "ru").ok)
            self.assertTrue(consume(uid, "edit", "ru").ok)
            with connect() as conn:
                row = conn.execute(
                    'SELECT "write", edit FROM usage_daily WHERE telegram_id = ?',
                    (uid,),
                ).fetchone()
            self.assertEqual(int(row["write"]), 1)
            self.assertEqual(int(row["edit"]), 1)
            stats = get_admin_stats()
            self.assertEqual(stats.today.actions.write, 1)
            self.assertEqual(stats.today.actions.edit, 1)
            self.assertEqual(stats.users_total, 1)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_empty_stats_format(self) -> None:
        from billing.db import init_db
        from billing.service import format_admin_stats, get_admin_stats

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            text = format_admin_stats(get_admin_stats())
            self.assertIn("Users: 0", text)
            self.assertIn("(none)", text)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_get_admin_stats_summary_and_top(self) -> None:
        from billing.db import init_db
        from billing.service import (
            consume,
            ensure_user,
            get_admin_stats,
            record_usage,
        )

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            u1, u2 = 770010, 770011
            ensure_user(u1, "ru")
            ensure_user(u2, "ru")
            for _ in range(3):
                self.assertTrue(consume(u1, "ask", "ru").ok)
            self.assertTrue(consume(u2, "card", "ru").ok)
            # Older day for all-time / 7d coverage
            record_usage(u2, "write", day="2020-01-01")

            stats = get_admin_stats(top_n=5)
            self.assertEqual(stats.users_total, 2)
            self.assertEqual(stats.users_new_today, 2)
            self.assertEqual(stats.today.active_users, 2)
            self.assertEqual(stats.today.actions.ask, 3)
            self.assertEqual(stats.today.actions.card, 1)
            self.assertEqual(stats.today.actions.total, 4)
            self.assertEqual(stats.days7.active_users, 2)
            self.assertEqual(stats.all_time.actions.write, 1)
            self.assertEqual(stats.all_time.actions.total, 5)
            self.assertEqual(stats.today.top[0].telegram_id, u1)
            self.assertEqual(stats.today.top[0].counts.total, 3)
            self.assertGreaterEqual(stats.paid_orders, 0)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_format_admin_stats_contains_sections(self) -> None:
        from billing.db import init_db
        from billing.service import consume, ensure_user, format_admin_stats, get_admin_stats

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            ensure_user(770020, "ru")
            consume(770020, "ask", "ru")
            text = format_admin_stats(get_admin_stats())
            self.assertIn("Users:", text)
            self.assertIn("Active:", text)
            self.assertIn("Actions today:", text)
            self.assertIn("Top today:", text)
            self.assertIn("Top 7d:", text)
            self.assertIn("Top all:", text)
            self.assertIn("770020", text)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_stats_command_silent_for_non_admin(self) -> None:
        import asyncio

        from billing.handlers import cmd_stats

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            msg = MagicMock()
            msg.from_user = MagicMock()
            msg.from_user.id = 111  # not admin
            msg.answer = AsyncMock()
            asyncio.run(cmd_stats(msg))
            msg.answer.assert_not_called()
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)

    def test_stats_command_replies_for_admin(self) -> None:
        import asyncio

        from billing.db import init_db
        from billing.handlers import cmd_stats
        from billing.service import ensure_user

        tmp, old_db, old_open, old_admins, session_mod = self._ctx()
        try:
            init_db()
            ensure_user(900001, "ru")
            msg = MagicMock()
            msg.from_user = MagicMock()
            msg.from_user.id = 900001
            msg.answer = AsyncMock()
            asyncio.run(cmd_stats(msg))
            msg.answer.assert_called_once()
            text = msg.answer.call_args[0][0]
            self.assertIn("Users:", text)
        finally:
            self._restore(tmp, old_db, old_open, old_admins, session_mod)


if __name__ == "__main__":
    unittest.main()
