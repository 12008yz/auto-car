from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class TestQuotaActor(unittest.TestCase):
    def test_require_quota_uses_explicit_user_not_message_author(self) -> None:
        """Callback message.from_user is the bot — quota must use query.from_user."""
        import asyncio
        import config
        from billing.db import init_db
        from billing.service import ensure_user, get_balance
        from bot.handlers import _require_quota

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "billing.sqlite3"
            old_db = config.BILLING_DB_PATH
            old_open = config.BILLING_OPEN_ACCESS
            config.BILLING_DB_PATH = db_path
            config.BILLING_OPEN_ACCESS = True
            try:
                init_db()
                bot_id = 8802928759
                human_id = 1109824687
                ensure_user(bot_id, "ru")
                ensure_user(human_id, "ru")

                answers: list[str] = []

                async def fake_answer(text, **kwargs):
                    answers.append(text)
                    return SimpleNamespace(message_id=1)

                message = SimpleNamespace(
                    from_user=SimpleNamespace(id=bot_id, language_code="ru"),
                    answer=fake_answer,
                )
                human = SimpleNamespace(id=human_id, language_code="ru")

                charge = asyncio.run(_require_quota(message, "ask", user=human))  # type: ignore[arg-type]
                self.assertIsNotNone(charge)
                self.assertEqual(get_balance(human_id, "ru").daily_ask, 1)
                self.assertEqual(get_balance(bot_id, "ru").daily_ask, 0)
            finally:
                config.BILLING_DB_PATH = old_db
                config.BILLING_OPEN_ACCESS = old_open


if __name__ == "__main__":
    unittest.main()
