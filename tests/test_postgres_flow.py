"""Teste PostgreSQL real, opt-in via TEST_POSTGRES_URL em banco descartável."""

import os
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from uuid import uuid4

from firebase_admin import auth
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.database.sync_schema import metadata
from app.main import main
from app.repositories.sync_state_repository import SyncStateRepository
import test_settings


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL não configurada")
class PostgresFlowTests(unittest.TestCase):
    def setUp(self):
        url = os.environ["TEST_POSTGRES_URL"]
        self.admin = create_engine(url)
        self.addCleanup(self.admin.dispose)
        self.schemas = ["rpa_test_" + uuid4().hex for _ in range(2)]
        with self.admin.begin() as db:
            for schema in self.schemas:
                db.execute(text(f'CREATE SCHEMA "{schema}"'))
        self.addCleanup(self.drop_schemas)
        self.legacy = create_engine(url, connect_args={"options": f"-c search_path={self.schemas[0]} -c default_transaction_read_only=on"})
        self.target = create_engine(url, connect_args={"options": f"-c search_path={self.schemas[1]}"})
        self.addCleanup(self.legacy.dispose)
        self.addCleanup(self.target.dispose)
        with self.admin.begin() as db:
            db.execute(text(f'SET LOCAL search_path TO "{self.schemas[0]}"'))
            for ddl in (
                "CREATE TABLE empresa (id_empresa INT PRIMARY KEY, nome TEXT, cnpj TEXT)",
                "CREATE TABLE departamento (id_departamento INT PRIMARY KEY, nome TEXT, id_empresa INT REFERENCES empresa)",
                "CREATE TABLE funcionario (id_funcionario INT PRIMARY KEY, nome TEXT, cpf TEXT UNIQUE, cargo TEXT, id_empresa INT REFERENCES empresa, id_departamento INT REFERENCES departamento)",
                "CREATE TABLE email (id_email INT PRIMARY KEY, id_funcionario INT REFERENCES funcionario, email TEXT)",
                "INSERT INTO empresa VALUES (1,'Empresa A','11222333000181')",
                "INSERT INTO departamento VALUES (10,'Operação',1)",
                "INSERT INTO funcionario VALUES (1,'Ana','52998224725','Analista',1,10)",
                "INSERT INTO email VALUES (1,1,'ana@example.com')",
            ):
                db.execute(text(ddl))
        with self.target.begin() as db:
            for ddl in (
                "CREATE TABLE workspaces (id_workspace BIGSERIAL PRIMARY KEY, nome VARCHAR(255) NOT NULL, cnpj VARCHAR(14) UNIQUE NOT NULL)",
                "CREATE TABLE cargos (id_cargo BIGSERIAL PRIMARY KEY, workspace_id BIGINT REFERENCES workspaces NOT NULL, nome VARCHAR(255) NOT NULL, ativo BOOLEAN DEFAULT TRUE, UNIQUE(workspace_id,nome))",
                "CREATE TABLE unidades (id_unidade BIGSERIAL PRIMARY KEY, workspace_id BIGINT REFERENCES workspaces NOT NULL, nome VARCHAR(255) NOT NULL, ativo BOOLEAN DEFAULT TRUE, UNIQUE(workspace_id,nome))",
                "CREATE TABLE conta (nome VARCHAR(255), email VARCHAR(255) NOT NULL, firebase_uid VARCHAR(128) UNIQUE NOT NULL)",
                """CREATE TABLE usuarios (id_usuario BIGSERIAL PRIMARY KEY, cpf CHAR(11) UNIQUE,
                   cargo_id BIGINT NOT NULL REFERENCES cargos, unidade_id BIGINT NOT NULL REFERENCES unidades,
                   tipo TEXT NOT NULL CHECK(tipo IN ('FUNCIONARIO','GESTOR','GESTOR_WORKSPACE')),
                   status TEXT DEFAULT 'PRE_CADASTRADO' NOT NULL, modalidade TEXT,
                   criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(email), UNIQUE(firebase_uid)) INHERITS (conta)""",
                "CREATE TABLE admin (id_admin BIGSERIAL PRIMARY KEY, UNIQUE(email), UNIQUE(firebase_uid)) INHERITS (conta)",
            ):
                db.execute(text(ddl))
            metadata.create_all(db)
        self.accounts = {}
        self.creates = 0

    def drop_schemas(self):
        # Nomes são UUIDs gerados exclusivamente pelo teste; nunca schema da aplicação.
        with self.admin.begin() as db:
            for schema in self.schemas:
                if not schema.startswith("rpa_test_") or len(schema) != 41:
                    raise RuntimeError("Schema de teste inválido")
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

    def find(self, email, app):
        if email not in self.accounts:
            raise auth.UserNotFoundError("ausente")
        return self.accounts[email]

    def create(self, *, email, display_name, email_verified, app):
        self.creates += 1
        result = SimpleNamespace(uid="test-uid-" + str(self.creates), email=email, disabled=False)
        self.accounts[email] = result
        return result

    def execute_rpa(self, dry_run=False):
        settings = test_settings.SettingsTests().load(
            SYNC_DRY_RUN=str(dry_run).lower(), SYNC_BATCH_SIZE="1",
        )
        with patch("app.main.Settings", return_value=settings), \
             patch("app.services.integrations.initialize_firebase", return_value=object()), \
             patch("app.services.integrations.firebase_admin.delete_app"), \
             patch("app.services.integrations.check_firebase_connection"), \
             patch("app.services.integrations.create_database_engine", side_effect=lambda _, legacy: self.legacy if legacy else self.target), \
             patch("app.services.firebase_user_service.auth.get_user_by_email", side_effect=self.find), \
             patch("app.services.firebase_user_service.auth.create_user", side_effect=self.create), \
             patch("app.services.firebase_user_service.auth.get_user", side_effect=lambda uid, app: next(x for x in self.accounts.values() if x.uid == uid)):
            with self.assertLogs(level="INFO"):
                return main()

    def count(self, table):
        with self.target.connect() as db:
            return db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()

    def test_full_flow_repeat_update_and_inheritance(self):
        self.assertEqual(self.execute_rpa(dry_run=True), 0)
        self.assertEqual(self.creates, 0)
        self.assertEqual(self.count("usuarios"), 0)
        self.assertEqual(self.execute_rpa(), 0)
        self.assertEqual(self.execute_rpa(), 0)
        self.assertEqual(self.creates, 1)
        self.assertEqual(self.count("usuarios"), 1)
        self.assertEqual(self.count("ONLY conta"), 0)
        self.assertEqual(self.count("conta"), 1)
        with self.admin.begin() as db:
            db.execute(text(f'UPDATE "{self.schemas[0]}".funcionario SET nome=\'Ana Silva\', cargo=\'Gestora\''))
        with self.target.begin() as db:
            db.execute(text("UPDATE usuarios SET status='DESATIVADO', tipo='GESTOR', modalidade='Híbrida'"))
        self.assertEqual(self.execute_rpa(), 0)
        with self.target.connect() as db:
            row = db.execute(text("SELECT nome,tipo,status,modalidade FROM usuarios")).one()
            self.assertEqual(tuple(row), ("Ana Silva", "GESTOR", "DESATIVADO", "Híbrida"))
        self.assertEqual(self.count("cargos"), 2)
        self.assertEqual(self.count("rpa_user_links"), 1)

    def test_postgres_failure_after_firebase_creation_rolls_back_and_retries(self):
        with self.admin.begin() as db:
            db.execute(text(f'SET LOCAL search_path TO "{self.schemas[0]}"'))
            db.execute(text("INSERT INTO funcionario VALUES (2,'Bruno','11144477735','Analista',1,10)"))
            db.execute(text("INSERT INTO email VALUES (2,2,'bruno@example.com')"))
        with self.target.begin() as db:
            db.execute(text("ALTER TABLE usuarios ADD CONSTRAINT reject_test CHECK (nome <> 'Bruno')"))
        self.assertEqual(self.execute_rpa(), 1)
        self.assertEqual(self.creates, 2)
        for table in ("usuarios", "workspaces", "cargos", "unidades", "rpa_sync_users", "rpa_sync_control", "rpa_user_links"):
            self.assertEqual(self.count(table), 0)
        with self.target.begin() as db:
            db.execute(text("ALTER TABLE usuarios DROP CONSTRAINT reject_test"))
        self.assertEqual(self.execute_rpa(), 0)
        self.assertEqual(self.creates, 2)
        self.assertEqual(self.count("usuarios"), 2)
        self.assertEqual(self.count("rpa_sync_users"), 2)

    def test_lock_serializes_concurrent_writers(self):
        with self.target.begin() as db:
            SyncStateRepository(db).lock_sync()
        with self.target.begin() as first:
            SyncStateRepository(first).lock_sync()
            with self.assertRaises(DBAPIError), self.target.begin() as second:
                second.execute(text("SET LOCAL lock_timeout = '100ms'"))
                SyncStateRepository(second).lock_sync()
        with self.target.begin() as db:
            SyncStateRepository(db).lock_sync()

    def test_legacy_connection_is_read_only(self):
        with self.assertRaises(DBAPIError), self.legacy.begin() as db:
            db.execute(text("UPDATE funcionario SET nome='não permitido'"))
