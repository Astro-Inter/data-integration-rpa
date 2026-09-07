"""Executa as consultas em SQLite com dados fictícios e escrita bloqueada."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.main import main
from app.repositories.legacy_user_repository import LegacyReadError, LegacyUserRepository
import test_settings


class LegacyUserRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        for ddl in (
            "CREATE TABLE empresa (id_empresa INTEGER PRIMARY KEY, nome TEXT, cnpj TEXT)",
            "CREATE TABLE departamento (id_departamento INTEGER PRIMARY KEY, nome TEXT, id_empresa INTEGER)",
            "CREATE TABLE funcionario (id_funcionario INTEGER PRIMARY KEY, cpf TEXT, nome TEXT, cargo TEXT, id_empresa INTEGER, id_departamento INTEGER)",
            "CREATE TABLE email (id_email INTEGER PRIMARY KEY, email TEXT, id_funcionario INTEGER)",
        ):
            self.connection.execute(text(ddl))
        self.repository = LegacyUserRepository(self.connection)

    def seed(self):
        self.connection.execute(text("INSERT INTO empresa VALUES (1, 'Empresa A', '00123456000100'), (2, 'Empresa B', '00987654000100')"))
        self.connection.execute(text("INSERT INTO departamento VALUES (10, 'Operação', 1), (20, 'Operação', 2)"))
        self.connection.execute(text("""
            INSERT INTO funcionario VALUES
                (-2, '00000000001', '  ANA  ', 'Analista', 1, 10),
                (0, '00000000002', 'Bruno', 'Analista', 2, 20),
                (7, '00000000003', 'Carla', 'Analista', 1, 20),
                (99, '00000000004', 'Davi', 'Analista', 999, 999)
        """))
        self.connection.execute(text("""
            INSERT INTO email VALUES
                (3, ' ANA@example.test ', -2),
                (1, 'ana@example.test', -2),
                (2, 'ana@example.test', -2),
                (4, NULL, 0),
                (5, '', 0)
        """))
        self.connection.commit()

    def read_only(self):
        self.connection.execute(text("PRAGMA query_only = ON"))

    def test_empty_database(self):
        self.read_only()
        self.assertEqual(list(self.repository.iter_batches(2)), [])

    def test_pages_preserve_all_users_and_raw_emails_without_duplicates(self):
        self.seed()
        self.read_only()
        for size, lengths in ((1, [1, 1, 1, 1]), (2, [2, 2]), (3, [3, 1]), (10, [4])):
            with self.subTest(size=size):
                batches = list(self.repository.iter_batches(size))
                self.assertEqual([len(batch) for batch in batches], lengths)
                users = [user for batch in batches for user in batch]
                self.assertEqual([user.id_funcionario for user in users], [-2, 0, 7, 99])
                self.assertEqual(users[0].cpf, '00000000001')
                self.assertEqual(users[0].nome, '  ANA  ')
                self.assertEqual([item.id_email for item in users[0].emails], [1, 2, 3])
                self.assertEqual([item.email for item in users[0].emails], ['ana@example.test', 'ana@example.test', ' ANA@example.test '])
                self.assertEqual([item.email for item in users[1].emails], [None, ''])
                self.assertEqual(users[2].emails, ())

    def test_company_context_and_inconsistent_relationships_preserved(self):
        self.seed()
        self.read_only()
        users = list(self.repository.iter_batches(10))[0]
        self.assertEqual(users[0].empresa_nome, 'Empresa A')
        self.assertEqual(users[1].empresa_nome, 'Empresa B')
        self.assertEqual(users[0].departamento_nome, users[1].departamento_nome)
        self.assertEqual(users[2].id_empresa, 1)
        self.assertEqual(users[2].departamento_id_empresa, 2)
        self.assertEqual(users[3].id_empresa, 999)
        self.assertIsNone(users[3].empresa_nome)
        self.assertIsNone(users[3].departamento_nome)

    def test_new_higher_ids_wait_for_next_scan(self):
        self.seed()
        batches = self.repository.iter_batches(2)
        self.assertEqual([user.id_funcionario for user in next(batches)], [-2, 0])
        self.connection.execute(text("INSERT INTO funcionario VALUES (100, '00000000005', 'Eva', 'Analista', 1, 10)"))
        self.assertEqual([user.id_funcionario for batch in batches for user in batch], [7, 99])
        self.assertEqual([user.id_funcionario for batch in self.repository.iter_batches(10) for user in batch], [-2, 0, 7, 99, 100])

    def test_invalid_batch_size(self):
        for value in (0, -1, True, 1.5, '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                list(self.repository.iter_batches(value))

    def test_query_failure_is_safe(self):
        self.connection.execute(text("DROP TABLE funcionario"))
        with self.assertRaises(LegacyReadError) as caught:
            list(self.repository.iter_batches(10))
        self.assertNotIn('SELECT', str(caught.exception))
        self.assertNotIn('sqlite', str(caught.exception))

    def test_main_counts_users_and_closes_connection(self):
        self.seed()
        self.read_only()
        settings = test_settings.SettingsTests().load(SYNC_BATCH_SIZE='3')
        with patch('app.main.Settings', return_value=settings), patch('app.main.open_integrations') as integrations:
            connection_context = integrations.return_value.__enter__.return_value.legacy.connect.return_value
            connection_context.__enter__.return_value = self.connection
            with self.assertLogs(level='INFO') as logs:
                self.assertEqual(main(), 0)
            connection_context.__exit__.assert_called_once()
        output = ' '.join(logs.output)
        self.assertIn('4 funcionários encontrados', output)
        self.assertNotIn('00000000001', output)
        self.assertNotIn('ana@example.test', output)

    def test_main_reports_failure_instead_of_success_on_partial_read(self):
        self.seed()
        settings = test_settings.SettingsTests().load()
        def failed_batches(_):
            yield []
            raise LegacyReadError('Falha ao consultar funcionários no legado.')
        with (
            patch('app.main.Settings', return_value=settings),
            patch('app.main.open_integrations') as integrations,
            patch('app.main.LegacyUserRepository') as repository,
        ):
            repository.return_value.iter_batches.side_effect = failed_batches
            with self.assertLogs(level='INFO') as logs:
                self.assertEqual(main(), 1)
            integrations.return_value.__enter__.return_value.legacy.connect.return_value.__exit__.assert_called_once()
        self.assertNotIn('Consulta do legado concluída', ' '.join(logs.output))

    def test_main_handles_connection_loss_without_exposing_driver_error(self):
        settings = test_settings.SettingsTests().load()
        with patch('app.main.Settings', return_value=settings), patch('app.main.open_integrations') as integrations:
            integrations.return_value.__enter__.return_value.legacy.connect.side_effect = OperationalError(
                'SELECT segredo', {}, Exception('credencial-sensivel')
            )
            with self.assertLogs(level='ERROR') as logs:
                self.assertEqual(main(), 1)
        self.assertNotIn('credencial-sensivel', ' '.join(logs.output))
        self.assertNotIn('SELECT segredo', ' '.join(logs.output))
