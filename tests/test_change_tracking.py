"""Verifica detecção e persistência transacional com bancos em memória."""

from dataclasses import replace
from unittest.mock import Mock, patch
import unittest

from sqlalchemy import create_engine, select, text

from app.database.sync_schema import metadata, sync_control, sync_users
from app.database.init_sync_control import main as init_control
from app.models.legacy_user import LegacyEmail, LegacyUser
from app.repositories.sync_state_repository import SyncStateRepository
from app.services.change_tracking_service import ChangeTrackingService, user_fingerprint
import test_settings


def user(legacy_id=1):
    return LegacyUser(
        id_funcionario=legacy_id, cpf="00000000001", nome="Ana", cargo="Analista",
        id_empresa=1, empresa_nome="Empresa A", empresa_cnpj="00123456000100",
        id_departamento=10, departamento_nome="Operação", departamento_id_empresa=1,
        emails=(LegacyEmail(1, "ana@example.test"), LegacyEmail(2, None)),
    )


class ChangeTrackingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE destino_teste (id INTEGER PRIMARY KEY, nome TEXT)"))

    def run_sync(self, users, *, dry_run=False, processor=None):
        source = Mock()
        source.iter_batches.side_effect = lambda _: iter([users[:1], users[1:]])
        with self.engine.begin() as connection:
            return ChangeTrackingService(source, SyncStateRepository(connection)).run(
                1, dry_run=dry_run, process_user=processor
            )

    def persist(self, item, connection):
        connection.execute(text("""
            INSERT INTO destino_teste VALUES (:id, :nome)
            ON CONFLICT(id) DO UPDATE SET nome = EXCLUDED.nome
        """), {"id": item.id_funcionario, "nome": item.nome})

    def state(self):
        with self.engine.connect() as connection:
            return connection.execute(select(sync_users).order_by(sync_users.c.legacy_id)).all()

    def last_sync(self):
        with self.engine.connect() as connection:
            return SyncStateRepository(connection).last_synced_at()

    def test_preview_repeats_new_without_confirming_anything(self):
        for _ in range(2):
            summary = self.run_sync([user()])
            self.assertEqual((summary.new, summary.confirmed), (1, 0))
            self.assertIsNone(summary.last_synced_at)
        self.assertEqual(self.state(), [])
        self.assertIsNone(self.last_sync())

    def test_success_restart_and_change_only_process_pending(self):
        process = Mock(side_effect=self.persist)
        first = self.run_sync([user(1), user(2)], processor=process)
        self.assertEqual((first.new, first.confirmed), (2, 2))
        self.assertIsNotNone(self.last_sync())
        self.assertEqual(len(self.state()), 2)
        process.reset_mock()
        second = self.run_sync([user(1), user(2)], processor=process)
        self.assertEqual((second.unchanged, second.confirmed), (2, 0))
        process.assert_not_called()
        third = self.run_sync([replace(user(1), cargo="Gestora"), user(2), user(3)], processor=process)
        self.assertEqual((third.new, third.changed, third.unchanged, third.confirmed), (1, 1, 1, 2))
        self.assertEqual([call.args[0].id_funcionario for call in process.call_args_list], [1, 3])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM destino_teste")).scalar_one(), 3)

    def test_related_fields_and_email_changes_affect_fingerprint(self):
        base = user()
        modifications = {
            "cpf": "00000000002", "nome": "Ana Silva", "cargo": "Gestora",
            "id_empresa": 2, "empresa_nome": "Nova empresa", "empresa_cnpj": "00987654000100",
            "id_departamento": 20, "departamento_nome": "Novo departamento", "departamento_id_empresa": 2,
            "emails": (LegacyEmail(1, "novo@example.test"),),
        }
        for field, value in modifications.items():
            with self.subTest(field=field):
                self.assertNotEqual(user_fingerprint(base), user_fingerprint(replace(base, **{field: value})))
        self.assertEqual(user_fingerprint(base), user_fingerprint(replace(base, emails=tuple(reversed(base.emails)))))
        self.assertNotEqual(user_fingerprint(base), user_fingerprint(replace(base, emails=())))
        self.assertEqual(len(user_fingerprint(base)), 64)

    def test_dry_run_never_calls_processor_or_changes_existing_state(self):
        self.run_sync([user()], processor=self.persist)
        before, last = self.state(), self.last_sync()
        process = Mock(side_effect=AssertionError("não deve executar"))
        summary = self.run_sync([replace(user(), nome="Nova"), user(2)], dry_run=True, processor=process)
        self.assertEqual((summary.changed, summary.new, summary.confirmed), (1, 1, 0))
        process.assert_not_called()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.last_sync(), last)

    def test_partial_failure_rolls_back_destination_hashes_and_last_sync(self):
        self.run_sync([user()], processor=self.persist)
        before, last = self.state(), self.last_sync()
        def failing_process(item, connection):
            self.persist(item, connection)
            if item.id_funcionario == 2:
                raise RuntimeError("Firebase indisponível")
        with self.assertRaises(RuntimeError):
            self.run_sync([replace(user(), nome="Alterada"), user(2)], processor=failing_process)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.last_sync(), last)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT id, nome FROM destino_teste")).all(), [(1, "Ana")])
        retry = self.run_sync([replace(user(), nome="Alterada"), user(2)], processor=self.persist)
        self.assertEqual((retry.changed, retry.new, retry.confirmed), (1, 1, 2))

    def test_source_failure_does_not_advance_checkpoint(self):
        def pages(_):
            yield [user()]
            raise RuntimeError("Consulta interrompida")
        source = Mock()
        source.iter_batches.side_effect = pages
        with self.assertRaises(RuntimeError), self.engine.begin() as connection:
            ChangeTrackingService(source, SyncStateRepository(connection)).run(1, process_user=self.persist)
        self.assertEqual(self.state(), [])
        self.assertIsNone(self.last_sync())

    def test_failed_checkpoint_write_rolls_back_user_persistence(self):
        with patch.object(SyncStateRepository, "confirm_run", side_effect=RuntimeError("falha")):
            with self.assertRaises(RuntimeError):
                self.run_sync([user()], processor=self.persist)
        self.assertEqual(self.state(), [])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM destino_teste")).scalar_one(), 0)

    def test_read_only_preview_with_existing_state(self):
        self.run_sync([user()], processor=self.persist)
        source = Mock()
        source.iter_batches.return_value = [[user(), user(2)]]
        with self.engine.begin() as connection:
            connection.execute(text("PRAGMA query_only = ON"))
            summary = ChangeTrackingService(source, SyncStateRepository(connection)).run(10, dry_run=True)
            self.assertEqual((summary.unchanged, summary.new), (1, 1))
            connection.execute(text("PRAGMA query_only = OFF"))

    def test_requires_transaction(self):
        with self.engine.connect() as connection:
            with self.assertRaises(RuntimeError):
                ChangeTrackingService(Mock(), SyncStateRepository(connection)).run(10)

    def test_state_schema_contains_no_raw_personal_data(self):
        self.run_sync([user()], processor=self.persist)
        self.assertEqual(set(sync_users.c.keys()), {"integration_key", "legacy_id", "data_hash", "synced_at"})
        self.assertNotIn("ana@example.test", str(self.state()))
        self.assertNotIn("00000000001", str(self.state()))

    def test_empty_source_confirms_only_when_processor_available(self):
        self.run_sync([])
        self.assertIsNone(self.last_sync())
        summary = self.run_sync([], processor=self.persist)
        self.assertEqual(summary.total, 0)
        self.assertIsNotNone(self.last_sync())

    def test_schema_preparation_is_repeatable_without_erasing_state(self):
        self.run_sync([user()], processor=self.persist)
        before, last = self.state(), self.last_sync()
        metadata.create_all(self.engine)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.last_sync(), last)

    def test_init_control_refuses_dry_run_before_engine_creation(self):
        settings = test_settings.SettingsTests().load(SYNC_DRY_RUN="true")
        with patch("app.database.init_sync_control.Settings", return_value=settings):
            with patch("app.database.init_sync_control.create_database_engine") as create:
                with self.assertLogs(level="ERROR"):
                    self.assertEqual(init_control(), 1)
                create.assert_not_called()

    def test_init_control_disposes_engine_on_schema_failure(self):
        settings = test_settings.SettingsTests().load()
        with patch("app.database.init_sync_control.Settings", return_value=settings):
            with patch("app.database.init_sync_control.create_database_engine") as create:
                create.return_value.begin.side_effect = RuntimeError("senha-secreta")
                with self.assertLogs(level="ERROR") as logs:
                    self.assertEqual(init_control(), 1)
                create.return_value.dispose.assert_called_once()
                self.assertNotIn("senha-secreta", " ".join(logs.output))
