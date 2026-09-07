"""Fluxo com SQL real em SQLite, constraints e Firebase simulado."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from sqlalchemy import create_engine, text, select

from app.database.sync_schema import metadata, user_links, sync_users
from app.models.legacy_user import LegacyEmail
from app.repositories.target_user_repository import TargetIdentityError, TargetUserRepository
from app.repositories.sync_state_repository import SyncStateRepository
from app.services.change_tracking_service import ChangeTrackingService
from app.services.firebase_user_service import FirebaseUserService
from app.services.user_preparation_service import prepare_user
from app.services.user_sync_processor import UserSyncProcessor
from app.main import main
import test_change_tracking
import test_settings


DDL = (
    "CREATE TABLE workspaces (id_workspace INTEGER PRIMARY KEY, nome TEXT NOT NULL CHECK(length(trim(nome))>=2), cnpj TEXT NOT NULL UNIQUE)",
    "CREATE TABLE cargos (id_cargo INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE, UNIQUE(workspace_id,nome))",
    "CREATE TABLE unidades (id_unidade INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL REFERENCES workspaces, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE, UNIQUE(workspace_id,nome))",
    """CREATE TABLE usuarios (id_usuario INTEGER PRIMARY KEY, nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE, cpf TEXT UNIQUE, firebase_uid TEXT NOT NULL UNIQUE,
        cargo_id INTEGER NOT NULL REFERENCES cargos, unidade_id INTEGER NOT NULL REFERENCES unidades,
        tipo TEXT NOT NULL CHECK(tipo IN ('FUNCIONARIO','GESTOR','GESTOR_WORKSPACE')),
        status TEXT NOT NULL DEFAULT 'PRE_CADASTRADO' CHECK(status IN ('PRE_CADASTRADO','ATIVO','DESATIVADO')),
        modalidade TEXT, criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE TABLE admin (email TEXT UNIQUE, firebase_uid TEXT UNIQUE)",
    "CREATE TABLE conta (email TEXT, firebase_uid TEXT UNIQUE)",
)


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as db:
            db.execute(text("PRAGMA foreign_keys=ON"))
            for ddl in DDL:
                db.execute(text(ddl))
        metadata.create_all(self.engine)
        self.raw = test_change_tracking.user()

    def rows(self, table):
        with self.engine.connect() as db:
            return db.execute(text(f"SELECT * FROM {table}")).mappings().all()

    def persist(self, raw=None, uid="uid-1"):
        with self.engine.begin() as db:
            repo = TargetUserRepository(db)
            prepared = prepare_user(raw or self.raw)
            repo.resolve_identity(prepared)
            repo.persist(prepared, uid)

    def test_create_all_relations_and_repeat_without_duplicates(self):
        self.persist()
        self.persist()
        for table in ("workspaces", "cargos", "unidades", "usuarios", "rpa_user_links"):
            self.assertEqual(len(self.rows(table)), 1)
        saved = self.rows("usuarios")[0]
        self.assertEqual((saved["tipo"], saved["status"], saved["firebase_uid"]), ("FUNCIONARIO", "PRE_CADASTRADO", "uid-1"))
        self.assertIsNotNone(saved["criado_em"])
        self.assertEqual(saved["cargo_id"], self.rows("cargos")[0]["id_cargo"])
        self.assertEqual(saved["unidade_id"], self.rows("unidades")[0]["id_unidade"])
        self.assertEqual(self.rows("conta"), [])

    def test_update_preserves_profile_status_dates_and_disabled_entities(self):
        self.persist()
        with self.engine.begin() as db:
            db.execute(text("UPDATE usuarios SET tipo='GESTOR', status='DESATIVADO', modalidade='Híbrida', criado_em='2020-01-01'"))
            db.execute(text("UPDATE cargos SET ativo=FALSE"))
            db.execute(text("UPDATE unidades SET ativo=FALSE"))
        self.persist(replace(self.raw, nome="Ana Silva", empresa_nome="Empresa atualizada"))
        saved = self.rows("usuarios")[0]
        self.assertEqual((saved["nome"], saved["tipo"], saved["status"], saved["modalidade"], saved["criado_em"]),
                         ("Ana Silva", "GESTOR", "DESATIVADO", "Híbrida", "2020-01-01"))
        self.assertFalse(self.rows("cargos")[0]["ativo"])
        self.assertFalse(self.rows("unidades")[0]["ativo"])
        self.assertEqual(self.rows("workspaces")[0]["nome"], "Empresa atualizada")

    def test_changed_job_and_department_relink_without_renaming_shared_records(self):
        self.persist()
        self.persist(replace(self.raw, cargo="Coordenadora", departamento_nome="Financeiro"))
        self.assertEqual(len(self.rows("cargos")), 2)
        self.assertEqual(len(self.rows("unidades")), 2)
        self.assertEqual(len(self.rows("usuarios")), 1)
        self.assertEqual(self.rows("usuarios")[0]["cargo_id"], 2)

    def test_existing_user_can_be_adopted_only_with_matching_identity(self):
        self.persist()
        with self.engine.begin() as db:
            db.execute(user_links.delete())
            self.assertEqual(TargetUserRepository(db).resolve_identity(prepare_user(self.raw)), "uid-1")
            TargetUserRepository(db).persist(prepare_user(self.raw), "uid-1")
        self.assertEqual(len(self.rows("usuarios")), 1)
        self.assertEqual(len(self.rows("rpa_user_links")), 1)

    def test_different_person_or_uid_or_source_cannot_take_over(self):
        self.persist()
        for raw, uid in ((replace(self.raw, cpf="01234567890"), "uid-1"),
                         (self.raw, "outro-uid"), (replace(self.raw, id_funcionario=2), "uid-1")):
            with self.subTest(uid=uid, id=raw.id_funcionario), self.assertRaises(TargetIdentityError):
                self.persist(raw, uid)
        self.assertEqual(len(self.rows("usuarios")), 1)

    def test_cross_workspace_move_is_blocked(self):
        self.persist()
        with self.assertRaises(TargetIdentityError):
            self.persist(replace(self.raw, empresa_cnpj="04252011000110"))
        self.assertEqual(len(self.rows("workspaces")), 1)

    def test_admin_and_parent_conflicts_are_blocked(self):
        for table in ("admin", "conta"):
            with self.subTest(table=table):
                with self.engine.begin() as db:
                    db.execute(text(f"INSERT INTO {table} VALUES ('ANA@EXAMPLE.COM', 'other')"))
                with self.assertRaises(TargetIdentityError):
                    self.persist()
                with self.engine.begin() as db:
                    db.execute(text(f"DELETE FROM {table}"))
        self.assertEqual(self.rows("usuarios"), [])

    def test_uid_conflict_in_admin_detected_after_firebase_lookup(self):
        with self.engine.begin() as db:
            db.execute(text("INSERT INTO admin VALUES ('admin@example.com', 'uid-1')"))
            repo = TargetUserRepository(db)
            self.assertIsNone(repo.resolve_identity(prepare_user(self.raw)))
        with self.assertRaises(TargetIdentityError):
            self.persist()
        self.assertEqual(self.rows("workspaces"), [])

    def test_stale_mapping_fails_instead_of_recreating(self):
        self.persist()
        with self.engine.begin() as db:
            db.execute(text("DELETE FROM usuarios"))
        with self.assertRaises(TargetIdentityError):
            self.persist()

    def test_same_organization_names_are_scoped_to_workspace(self):
        self.persist()
        other = replace(self.raw, id_funcionario=2, cpf="01234567890",
                        empresa_cnpj="04252011000110", empresa_nome="Empresa B",
                        emails=(LegacyEmail(1, "bruno@example.com"),))
        self.persist(other, "uid-2")
        self.assertEqual(len(self.rows("workspaces")), 2)
        self.assertEqual(len(self.rows("cargos")), 2)
        self.assertEqual(len(self.rows("unidades")), 2)
        self.assertNotEqual(self.rows("cargos")[0]["workspace_id"], self.rows("cargos")[1]["workspace_id"])

    def test_cpf_and_email_pointing_at_two_accounts_fail(self):
        self.persist()
        other = replace(self.raw, id_funcionario=2, cpf="01234567890",
                        emails=(LegacyEmail(1, "bruno@example.com"),))
        self.persist(other, "uid-2")
        with self.assertRaises(TargetIdentityError):
            self.persist(replace(self.raw, emails=other.emails))
        self.assertEqual(len(self.rows("usuarios")), 2)

    def test_constraint_failure_after_relations_rolls_them_back(self):
        with self.engine.begin() as db:
            db.execute(text("""CREATE TRIGGER reject_user BEFORE INSERT ON usuarios
                BEGIN SELECT RAISE(ABORT, 'rejeitado'); END"""))
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            self.persist()
        for table in ("workspaces", "cargos", "unidades", "usuarios", "rpa_user_links"):
            self.assertEqual(self.rows(table), [])

    def test_existing_control_upgrade_adds_links_without_resetting_history(self):
        with self.engine.begin() as db:
            user_links.drop(db)
            db.execute(text("INSERT INTO rpa_sync_control VALUES ('usuarios_legado', '2020-01-01')"))
        metadata.create_all(self.engine)
        self.assertEqual(self.rows("rpa_sync_control")[0]["last_synced_at"], "2020-01-01")
        self.persist()
        self.assertEqual(len(self.rows("rpa_user_links")), 1)

    def test_pipeline_commits_uid_relations_and_hash_together(self):
        firebase = FirebaseUserService(object(), dry_run=False)
        processor = UserSyncProcessor(firebase,
            resolve_identity=lambda user, db: TargetUserRepository(db).resolve_identity(user),
            persist_user=lambda user, uid, db: TargetUserRepository(db).persist(user, uid))
        source = Mock()
        source.iter_batches.side_effect = lambda _: iter([[self.raw]])
        with patch.object(firebase, "ensure_user", return_value=SimpleNamespace(uid="uid-1")) as ensure:
            for expected in (1, 0):
                with self.engine.begin() as db:
                    summary = ChangeTrackingService(source, SyncStateRepository(db)).run(100, process_user=processor)
                    self.assertEqual(summary.confirmed, expected)
            ensure.assert_called_once()
        self.assertEqual(len(self.rows("rpa_sync_users")), 1)
        self.assertEqual(len(self.rows("rpa_user_links")), 1)
        self.assertIsNotNone(self.rows("rpa_sync_control")[0]["last_synced_at"])

    def test_late_failure_rolls_back_all_related_tables_and_checkpoint(self):
        source = Mock()
        source.iter_batches.return_value = [[self.raw]]
        def process(user, db):
            TargetUserRepository(db).persist(user, "uid-1")
            raise RuntimeError("falha depois de gravar relações")
        with self.assertRaises(RuntimeError), self.engine.begin() as db:
            ChangeTrackingService(source, SyncStateRepository(db)).run(100, process_user=process)
        for table in ("workspaces", "cargos", "unidades", "usuarios", "rpa_user_links", "rpa_sync_users", "rpa_sync_control"):
            self.assertEqual(self.rows(table), [])

    def test_main_activates_real_processor_only_when_not_dry_run(self):
        for dry_run in ("true", "false"):
            with self.subTest(dry_run=dry_run):
                settings = test_settings.SettingsTests().load(SYNC_DRY_RUN=dry_run)
                with patch("app.main.Settings", return_value=settings), patch("app.main.open_integrations") as integrations, \
                     patch("app.main.LegacyUserRepository") as source, \
                     patch("app.services.firebase_user_service.auth.get_user_by_email", return_value=SimpleNamespace(uid="uid-1", email="ana@example.com", disabled=False)) as find:
                    integrations.return_value.__enter__.return_value.target = self.engine
                    source.return_value.iter_batches.return_value = iter([[self.raw]])
                    with self.assertLogs(level="INFO"):
                        self.assertEqual(main(), 0)
                    self.assertEqual(find.call_count, 0 if dry_run == "true" else 1)
                self.assertEqual(len(self.rows("usuarios")), 0 if dry_run == "true" else 1)
