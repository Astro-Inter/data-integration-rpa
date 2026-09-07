"""Alertas sanitizados e atribuição de falhas; SMTP e integrações simulados."""

from dataclasses import replace
import smtplib
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app.config.settings import EmailSettings
from app.main import main
from app.services.failure_alert import FailureReport, build_message, send_failure_alert
from app.services.integrations import IntegrationError, open_integrations
from app.services.user_preparation_service import prepare_user, UserDataError
import test_change_tracking
import test_settings


class FailureAlertTests(unittest.TestCase):
    def setUp(self):
        self.settings = test_settings.SettingsTests().load(
            EMAIL_ALERTS_ENABLED="true", SMTP_PASSWORD="senha-app-ficticia",
        )
        self.smtp_factory = self.enterContext(patch("app.services.failure_alert.smtplib.SMTP"))
        self.smtp = self.smtp_factory.return_value.__enter__.return_value
        self.report = FailureReport("run-alert-test")
        self.report.email_settings = EmailSettings(**self.settings.model_dump(), _env_file=None)

    def test_tls_login_and_single_message_with_same_sender_recipient(self):
        self.report.set_stage("PostgreSQL destino", "Persistência")
        self.report.add("Conflito de unicidade.")
        self.assertEqual(send_failure_alert(self.report, 1, 3), "enviado")
        self.smtp_factory.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
        methods = [call[0] for call in self.smtp.method_calls]
        self.assertEqual(methods, ["ehlo", "starttls", "ehlo", "login", "send_message"])
        self.assertTrue(self.smtp.starttls.call_args.kwargs["context"].check_hostname)
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message["From"], "app.4str0@gmail.com")
        self.assertEqual(message["To"], "app.4str0@gmail.com")
        self.assertIn("PostgreSQL destino", message.get_content())
        self.assertIn("run-alert-test", message.get_content())
        self.assertNotIn("senha-app-ficticia", message.as_string())

    def test_success_and_disabled_do_not_connect(self):
        self.assertEqual(send_failure_alert(self.report, 0, 1), "ignorado")
        self.report.email_settings.email_alerts_enabled = False
        self.assertEqual(send_failure_alert(self.report, 1, 1), "ignorado")
        self.smtp_factory.assert_not_called()

    def test_missing_password_and_tls_failure_do_not_expose_credentials(self):
        self.report.email_settings.smtp_password = test_settings.SettingsTests().load().smtp_password
        with self.assertLogs(level="WARNING"):
            self.assertEqual(send_failure_alert(self.report, 1, 1), "sem_credencial")
        self.smtp_factory.assert_not_called()
        self.report.email_settings.smtp_password = self.settings.smtp_password
        self.smtp.starttls.side_effect = smtplib.SMTPException("senha-app-ficticia")
        with self.assertLogs(level="ERROR") as logs:
            self.assertEqual(send_failure_alert(self.report, 1, 1), "falha_envio")
        self.smtp.login.assert_not_called()
        self.smtp.send_message.assert_not_called()
        self.assertNotIn("senha-app-ficticia", " ".join(logs.output))

    def test_validation_details_include_ids_and_reason_without_documents(self):
        user = replace(test_change_tracking.user(), cpf="11144477734", empresa_cnpj="01123456000188")
        with self.assertRaises(UserDataError) as caught:
            prepare_user(user)
        self.report.validation(caught.exception)
        self.report.dry_run = True
        body = build_message(self.report, 1, 4).get_content()
        for value in ("PostgreSQL legado", "funcionario_id=1", "empresa_id=1", "usuario.cpf", "workspace.cnpj", "dígitos verificadores inválidos", "simulação"):
            self.assertIn(value, body)
        for value in (user.cpf, user.empresa_cnpj, "ana@example.com"):
            self.assertNotIn(value, body)

    def test_report_limits_errors_and_discloses_truncation(self):
        for _ in range(60):
            self.report.add("Campo inválido.")
        self.assertEqual(len(self.report.issues), 50)
        self.assertIn("limite de 50: 10", build_message(self.report, 1, 1).get_content())

    def test_main_source_connection_failure_is_not_reported_as_target(self):
        with patch("app.main.Settings", return_value=self.settings), patch("app.main.open_integrations") as integrations:
            integrations.return_value.__enter__.return_value.legacy.connect.side_effect = OperationalError(
                "SELECT segredo", {}, Exception("senha-do-banco"),
            )
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 1)
        body = self.smtp.send_message.call_args.args[0].get_content()
        self.assertIn("[PostgreSQL legado] Abrir conexão para leitura", body)
        self.assertNotIn("SELECT segredo", body)
        self.assertNotIn("senha-do-banco", body)
        self.smtp.send_message.assert_called_once()

    def test_main_target_connection_failure_and_smtp_failure_preserve_code(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"segredo")
        with patch("app.main.Settings", return_value=self.settings), patch("app.main.open_integrations") as integrations:
            integrations.return_value.__enter__.return_value.target.begin.side_effect = OperationalError(
                "SQL", {}, Exception("senha-do-banco"),
            )
            with patch("app.services.failure_alert.build_message", wraps=build_message) as build:
                with self.assertLogs(level="INFO") as logs:
                    self.assertEqual(main(), 1)
        self.assertEqual(build.call_args.args[0].system, "PostgreSQL destino")
        self.assertNotIn("segredo", " ".join(logs.output))
        self.smtp.send_message.assert_not_called()

    def test_integration_initialization_identifies_correct_system(self):
        for side_effect, expected in (([RuntimeError("segredo")], "PostgreSQL legado"),
                                      ([None, RuntimeError("segredo")], "PostgreSQL destino")):
            with self.subTest(expected=expected), \
                 patch("app.services.integrations.initialize_firebase"), \
                 patch("app.services.integrations.firebase_admin.delete_app"), \
                 patch("app.services.integrations.create_database_engine"), \
                 patch("app.services.integrations.check_database_connection", side_effect=side_effect):
                with self.assertRaises(IntegrationError) as caught:
                    with open_integrations(self.settings):
                        pass
                self.assertEqual(caught.exception.system, expected)
                self.assertNotIn("segredo", str(caught.exception))

    def test_dry_run_sends_one_email_with_multiple_validation_failures(self):
        self.settings.sync_dry_run = True
        invalid = replace(test_change_tracking.user(), cpf="11144477734")
        with patch("app.main.Settings", return_value=self.settings), \
             patch("app.main.open_integrations"), \
             patch("app.main.LegacyUserRepository") as legacy, \
             patch("app.main.SyncStateRepository") as state:
            legacy.return_value.iter_batches.return_value = [[invalid, replace(invalid, id_funcionario=2)]]
            state.return_value.confirmed_hashes.return_value = {}
            state.return_value.last_synced_at.return_value = None
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 1)
        self.smtp.send_message.assert_called_once()
        body = self.smtp.send_message.call_args.args[0].get_content()
        self.assertIn("funcionario_id=1", body)
        self.assertIn("funcionario_id=2", body)
        self.assertIn("dígitos verificadores inválidos", body)

    def test_configuration_failure_can_email_without_database_settings(self):
        import os
        env = {"EMAIL_ALERTS_ENABLED": "true", "SMTP_PASSWORD": "senha-app-ficticia"}
        with patch.dict(os.environ, env, clear=True), \
             patch("app.main.Settings", side_effect=lambda: test_settings.Settings(_env_file=None)):
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 1)
        body = self.smtp.send_message.call_args.args[0].get_content()
        self.assertIn("legacy_db_host", body)
        self.assertIn("target_db_host", body)
        self.assertNotIn("senha-app-ficticia", body)

    def test_history_warning_sends_alert_even_when_sync_succeeds(self):
        self.report.committed = True
        self.report.add("Histórico indisponível.", system="Firestore", stage="Histórico")
        self.assertEqual(send_failure_alert(self.report, 0, 1), "enviado")
        message = self.smtp.send_message.call_args.args[0]
        self.assertIn("Alerta de histórico", message["Subject"])
        self.assertIn("commit concluído", message.get_content())

    def test_interruption_sends_one_alert_with_original_exit_code(self):
        from app.main import RunInterrupted
        def interrupted(execution):
            execution.report.email_settings = self.report.email_settings
            execution.report.set_stage("PostgreSQL destino", "Persistência")
            raise RunInterrupted()
        with patch("app.main.run", side_effect=interrupted):
            with self.assertLogs(level="INFO"):
                self.assertEqual(main(), 143)
        self.smtp.send_message.assert_called_once()
        body = self.smtp.send_message.call_args.args[0].get_content()
        self.assertIn("SIGTERM", body)
        self.assertIn("Código de saída: 143", body)
