"""Verifica configuração e erros sem usar credenciais ou serviços reais."""

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config.settings import Settings
from app.main import main


ENV = {
    "FIREBASE_PROJECT_ID": "projeto-teste",
    "FIREBASE_CREDENTIALS_BASE64": "credencial-ficticia",
    "LEGACY_DB_HOST": "localhost",
    "LEGACY_DB_NAME": "legado",
    "LEGACY_DB_USER": "leitor",
    "LEGACY_DB_PASSWORD": "senha-ficticia-legado",
    "TARGET_DB_HOST": "localhost",
    "TARGET_DB_NAME": "destino",
    "TARGET_DB_USER": "integracao",
    "TARGET_DB_PASSWORD": "senha-ficticia-destino",
}


class SettingsTests(unittest.TestCase):
    def load(self, **overrides):
        with patch.dict(os.environ, ENV | overrides, clear=True):
            return Settings(_env_file=None)

    def test_environment_types_and_secret_masking(self):
        settings = self.load(SYNC_DRY_RUN="true", SYNC_BATCH_SIZE="25", LOG_LEVEL="debug")
        self.assertTrue(settings.sync_dry_run)
        self.assertEqual(settings.sync_batch_size, 25)
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertNotIn(ENV["LEGACY_DB_PASSWORD"], repr(settings))
        self.assertNotIn(ENV["FIREBASE_CREDENTIALS_BASE64"], settings.model_dump_json())

    def test_invalid_configuration_rejected(self):
        for field, value in [
            ("SYNC_BATCH_SIZE", "0"),
            ("LEGACY_DB_PORT", "65536"),
            ("TARGET_DB_PORT", "0"),
            ("SYNC_DRY_RUN", "talvez"),
            ("LOG_LEVEL", "TRACE"),
            ("FIREBASE_CREDENTIALS_BASE64", ""),
            ("TARGET_DB_PASSWORD", ""),
        ]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.load(**{field: value})

    def test_environment_overrides_example_file(self):
        with patch.dict(os.environ, ENV | {"SYNC_BATCH_SIZE": "23"}, clear=True):
            settings = Settings(_env_file=".env.example")
        self.assertEqual(settings.sync_batch_size, 23)
        self.assertEqual(settings.legacy_db_port, 5432)
        self.assertFalse(settings.sync_dry_run)

    def test_startup_failure_does_not_log_input(self):
        with patch.dict(os.environ, ENV | {"LEGACY_DB_PORT": "segredo-invalido"}, clear=True):
            with patch("app.main.Settings", side_effect=lambda: Settings(_env_file=None)):
                with self.assertLogs(level="ERROR") as logs:
                    self.assertEqual(main(), 1)
        self.assertNotIn("segredo-invalido", " ".join(logs.output))
        self.assertIn("legacy_db_port", " ".join(logs.output))

    def test_startup_explicitly_reports_unimplemented_sync(self):
        with (
            patch("app.main.Settings", return_value=self.load()),
            patch("app.main.open_integrations"),
            patch("app.main.LegacyUserRepository") as repository,
        ):
            repository.return_value.iter_batches.return_value = iter(())
            with self.assertLogs(level="INFO") as logs:
                self.assertEqual(main(), 0)
        self.assertIn("Sincronização ainda não implementada", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
