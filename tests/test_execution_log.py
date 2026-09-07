"""Histórico Firestore com cliente simulado, sem credenciais reais."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from app.main import main
from app.services.change_tracking_service import ChangeSummary
from app.services.execution_log import Event, ExecutionLog
from app.services.integrations import IntegrationError
import test_settings


class ExecutionLogTests(unittest.TestCase):
    def setUp(self):
        self.settings = test_settings.SettingsTests().load(FIRESTORE_LOGS_ENABLED="true")
        self.initialize = self.enterContext(patch("app.services.execution_log.initialize_firebase"))
        self.client_factory = self.enterContext(patch("app.services.execution_log.firestore.client"))
        self.delete = self.enterContext(patch("app.services.execution_log.firebase_admin.delete_app"))
        self.client = self.client_factory.return_value
        self.document = self.client.collection.return_value.document.return_value
        self.snapshots = []
        self.document.set.side_effect = lambda data, **kw: self.snapshots.append(deepcopy(data))
        self.execution = ExecutionLog("run-test")
        self.addCleanup(self.execution.close)

    def test_success_stores_timeline_counts_and_exit_without_user_data(self):
        self.execution.configure(self.settings)
        self.execution.event(Event.PROCESSING)
        self.execution.summary(ChangeSummary(total=3, new=2, unchanged=1, confirmed=2))
        self.execution.finish(0, 1.23456)
        data = self.snapshots[-1]
        self.assertEqual(data["run_id"], "run-test")
        self.assertEqual(data["status"], "SUCESSO")
        self.assertEqual(data["contagens"]["confirmed"], 2)
        self.assertEqual(data["duracao_segundos"], 1.235)
        self.assertEqual([e["codigo"] for e in data["eventos"]], [
            "EXECUCAO_INICIADA", "PROCESSANDO_USUARIOS", "EXECUCAO_ENCERRADA",
        ])
        self.assertLessEqual(data["iniciado_em"], data["encerrado_em"])
        self.client.collection.assert_called_once_with("rpa_execucoes")
        self.client_factory.assert_called_once_with(self.initialize.return_value, database_id="(default)")
        self.assertEqual(self.document.set.call_args.kwargs, {"retry": None, "timeout": 3})
        for sensitive in ("senha-ficticia", "credencial-ficticia", "email", "cpf"):
            self.assertNotIn(sensitive, str(data))

    def test_dry_run_has_history_and_disabled_has_no_client(self):
        self.execution.configure(test_settings.SettingsTests().load())
        self.initialize.assert_not_called()
        self.execution.configure(test_settings.SettingsTests().load(
            FIRESTORE_LOGS_ENABLED="true", SYNC_DRY_RUN="true",
        ))
        self.assertTrue(self.snapshots[-1]["dry_run"])

    def test_write_failure_is_sanitized_and_stops_repeated_remote_attempts(self):
        self.document.set.side_effect = RuntimeError("segredo-do-provedor")
        with self.assertLogs(level="WARNING") as logs:
            self.execution.configure(self.settings)
            self.execution.event(Event.CONNECTING)
            self.execution.finish(0, 2)
        self.assertFalse(self.execution.available)
        self.assertEqual(self.document.set.call_count, 1)
        self.assertNotIn("segredo-do-provedor", " ".join(logs.output))

    def test_mid_run_failure_keeps_last_remote_snapshot_and_console_warning(self):
        self.execution.configure(self.settings)
        self.document.set.side_effect = RuntimeError("segredo")
        with self.assertLogs(level="WARNING"):
            self.execution.event(Event.PROCESSING)
        self.execution.finish(0, 1)
        self.assertEqual(self.snapshots[-1]["status"], "EM_EXECUCAO")
        self.assertEqual(self.document.set.call_count, 2)

    def test_initialization_failure_still_releases_created_app(self):
        self.client_factory.side_effect = RuntimeError("segredo")
        with self.assertLogs(level="WARNING"):
            self.execution.configure(self.settings)
        self.execution.close()
        self.delete.assert_called_once_with(self.initialize.return_value)
        self.execution.close()
        self.delete.assert_called_once()

    def test_arbitrary_messages_rejected_and_timeline_bounded(self):
        self.execution.configure(self.settings)
        with self.assertRaises(ValueError):
            self.execution.event("senha ou e-mail de uma exceção")
        for _ in range(60):
            self.execution.event(Event.PROCESSING)
        self.execution.finish(143, 10)
        self.assertEqual(len(self.snapshots[-1]["eventos"]), 50)
        self.assertEqual(self.snapshots[-1]["status"], "INTERROMPIDA")
        self.assertEqual(self.snapshots[-1]["ultima_etapa"], "EXECUCAO_ENCERRADA")

    def test_main_records_connection_failure_and_releases_log_app(self):
        with patch("app.main.Settings", return_value=self.settings), \
             patch("app.main.open_integrations", side_effect=IntegrationError("Falha na conexão")):
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 1)
        data = self.snapshots[-1]
        self.assertEqual(data["status"], "FALHA")
        self.assertEqual(data["exit_code"], 1)
        self.assertIn("FALHA_INTEGRACAO", [e["codigo"] for e in data["eventos"]])
        self.assertNotIn("Falha na conexão", str(data))
        self.delete.assert_called_once()
        self.client.close.assert_called_once()

    def test_main_result_survives_history_and_cleanup_failure(self):
        self.document.set.side_effect = RuntimeError("segredo")
        self.client.close.side_effect = RuntimeError("outro-segredo")
        def successful_run(execution):
            execution.configure(self.settings)
            return 0
        with patch("app.main.run", side_effect=successful_run):
            with self.assertLogs(level="INFO") as logs:
                self.assertEqual(main(), 0)
        self.assertNotIn("segredo", " ".join(logs.output))
        self.delete.assert_called_once()
