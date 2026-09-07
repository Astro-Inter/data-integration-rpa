"""Logs finais e interrupções sem vazar mensagens inesperadas."""

import signal
import unittest
from unittest.mock import patch

from app.main import RunInterrupted, _terminate, main
import test_settings


class RunLifecycleTests(unittest.TestCase):
    def test_unexpected_error_is_sanitized_and_final_summary_emitted(self):
        with patch("app.main.run", side_effect=RuntimeError("token-email-senha")):
            with self.assertLogs(level="INFO") as logs:
                self.assertEqual(main(), 1)
        output = " ".join(logs.output)
        self.assertNotIn("token-email-senha", output)
        self.assertIn("exit_code=1", output)
        self.assertIn("duracao_segundos=", output)
        self.assertEqual(output.count("run_id="), 2)

    def test_signal_and_keyboard_exit_codes_restore_handler(self):
        original = signal.getsignal(signal.SIGTERM)
        for failure, expected in ((RunInterrupted(), 143), (KeyboardInterrupt(), 130)):
            with self.subTest(expected=expected), patch("app.main.run", side_effect=failure):
                with self.assertLogs(level="INFO"):
                    self.assertEqual(main(), expected)
                self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_sigterm_unwinds_transaction_context(self):
        from sqlalchemy import create_engine, text
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as db:
            db.execute(text("CREATE TABLE checkpoint (id INTEGER)"))
        def interrupted():
            with engine.begin() as db:
                db.execute(text("INSERT INTO checkpoint VALUES (1)"))
                _terminate(signal.SIGTERM, None)
        with patch("app.main.run", side_effect=interrupted):
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 143)
        with engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM checkpoint")).scalar_one(), 0)

    def test_sigterm_during_initialization_is_not_wrapped_as_provider_error(self):
        settings = test_settings.SettingsTests().load()
        with patch("app.main.Settings", return_value=settings), \
             patch("app.services.integrations.initialize_firebase", side_effect=RunInterrupted()):
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 143)
