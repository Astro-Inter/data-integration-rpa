"""Testes sem serviços externos para configuração, falhas e liberação."""

import base64
import json
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from app.database.connections import create_database_engine, check_database_connection
from app.services.firebase_service import initialize_firebase, check_firebase_connection
from app.services.integrations import IntegrationError, open_integrations
from app.main import main
import test_settings


def settings(**overrides):
    return test_settings.SettingsTests().load(**overrides)


class DatabaseTests(unittest.TestCase):
    def test_special_password_and_independent_databases(self):
        config = settings(LEGACY_DB_PASSWORD="p@ss:/?#%", TARGET_DB_NAME="outro")
        with patch("app.database.connections.create_engine") as create:
            create_database_engine(config, legacy=True)
            args, kwargs = create.call_args
            self.assertEqual(args[0].password, "p@ss:/?#%")
            self.assertEqual(args[0].database, "legado")
            self.assertIn("default_transaction_read_only=on", kwargs["connect_args"]["options"])
            create_database_engine(config, legacy=False)
            args, kwargs = create.call_args
            self.assertEqual(args[0].database, "outro")
            self.assertNotIn("read_only", kwargs["connect_args"]["options"])
            create_database_engine(settings(SYNC_DRY_RUN="true"), legacy=False)
            self.assertIn("read_only=on", create.call_args.kwargs["connect_args"]["options"])

    def test_check_uses_constant_and_closes_connection_on_error(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.side_effect = RuntimeError("falha")
        with self.assertRaises(RuntimeError):
            check_database_connection(engine)
        self.assertEqual(str(connection.execute.call_args.args[0]), "SELECT 1")
        engine.connect.return_value.__exit__.assert_called_once()


class FirebaseTests(unittest.TestCase):
    def test_invalid_credentials_and_project_rejected(self):
        values = ["!base64-invalido", base64.b64encode(b"not-json").decode()]
        for document in ([], {"type": "service_account", "project_id": "outro"}):
            values.append(base64.b64encode(json.dumps(document).encode()).decode())
        with patch("app.services.firebase_service.firebase_admin.initialize_app") as initialize:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    initialize_firebase(settings(FIREBASE_CREDENTIALS_BASE64=value))
            initialize.assert_not_called()

    def test_certificate_in_memory_and_explicit_app_on_read(self):
        document = {"type": "service_account", "project_id": "projeto-teste"}
        encoded = base64.b64encode(json.dumps(document).encode()).decode()
        with patch("app.services.firebase_service.credentials.Certificate") as certificate:
            with patch("app.services.firebase_service.firebase_admin.initialize_app") as initialize:
                app = initialize_firebase(settings(FIREBASE_CREDENTIALS_BASE64=encoded))
                certificate.assert_called_once_with(document)
                self.assertEqual(initialize.call_args.kwargs["options"]["projectId"], "projeto-teste")
        with patch("app.services.firebase_service.auth.list_users") as read:
            check_firebase_connection(app)
            read.assert_called_once_with(max_results=1, app=app)


class LifecycleTests(unittest.TestCase):
    def test_cleanup_on_success_and_each_partial_failure(self):
        for failure in (None, "firebase_init", "legacy", "target", "firebase_auth", "consumer"):
            with self.subTest(failure=failure), ExitStack() as patches:
                module = "app.services.integrations."
                init = patches.enter_context(patch(module + "initialize_firebase"))
                delete = patches.enter_context(patch(module + "firebase_admin.delete_app"))
                create = patches.enter_context(patch(module + "create_database_engine"))
                check = patches.enter_context(patch(module + "check_database_connection"))
                auth = patches.enter_context(patch(module + "check_firebase_connection"))
                legacy, target = MagicMock(), MagicMock()
                create.side_effect = [legacy, target]
                if failure == "firebase_init":
                    init.side_effect = ValueError("segredo")
                if failure == "legacy":
                    check.side_effect = RuntimeError("segredo")
                if failure == "target":
                    check.side_effect = [None, RuntimeError("segredo")]
                if failure == "firebase_auth":
                    auth.side_effect = RuntimeError("segredo")
                try:
                    with open_integrations(settings()) as connections:
                        self.assertIs(connections.legacy, legacy)
                        if failure == "consumer":
                            raise LookupError("erro do consumidor")
                except IntegrationError as exc:
                    self.assertIn(failure, ("firebase_init", "legacy", "target", "firebase_auth"))
                    self.assertNotIn("segredo", str(exc))
                except LookupError:
                    self.assertEqual(failure, "consumer")
                else:
                    self.assertIsNone(failure)
                if failure != "firebase_init":
                    delete.assert_called_once_with(init.return_value)
                    legacy.dispose.assert_called_once()
                else:
                    delete.assert_not_called()
                    create.assert_not_called()
                if failure not in ("firebase_init", "legacy"):
                    target.dispose.assert_called_once()

    def test_main_returns_failure_for_unavailable_integration(self):
        with patch("app.main.Settings", return_value=settings()):
            with patch("app.main.open_integrations", side_effect=IntegrationError("Falha na conexão")):
                with self.assertLogs(level="ERROR"):
                    self.assertEqual(main(), 1)
