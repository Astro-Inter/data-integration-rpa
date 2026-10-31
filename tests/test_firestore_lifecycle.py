import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from app.repositories.firestore_sync_state import FirestoreSyncState, FirestoreStateError
from memory_firestore import MemoryFirestore


class FirestoreLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryFirestore()
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as db:
            db.execute(text("CREATE TABLE usuario (id INTEGER PRIMARY KEY)"))

    def stage(self, state):
        state.lock_sync()
        state.stage_link(1, 10, "uid-1")
        state.confirm_user(1, "a" * 64, datetime.now(timezone.utc))
        state.confirm_run(datetime.now(timezone.utc))

    def test_publish_after_database_commit(self):
        with FirestoreSyncState(self.store) as state:
            with self.engine.begin() as db:
                self.stage(state)
                db.execute(text("INSERT INTO usuario VALUES (10)"))
                self.assertNotIn("rpa_integracoes/usuarios_legado/usuarios/1", self.store.data)
        self.assertEqual(FirestoreSyncState(self.store).confirmed_hashes([1]), {1: "a" * 64})
        self.assertNotIn("rpa_locks/usuarios_legado", self.store.data)

    def test_rollback_never_publishes_hash_or_link(self):
        with self.assertRaises(RuntimeError):
            with FirestoreSyncState(self.store) as state:
                with self.engine.begin() as db:
                    self.stage(state)
                    db.execute(text("INSERT INTO usuario VALUES (10)"))
                    raise RuntimeError("failure")
        self.assertEqual(self.store.data, {})

    def test_late_firestore_failure_keeps_sql_and_allows_retry(self):
        self.store.fail_commit = True
        with self.assertRaises(FirestoreStateError):
            with FirestoreSyncState(self.store) as state:
                with self.engine.begin() as db:
                    self.stage(state)
                    db.execute(text("INSERT INTO usuario VALUES (10)"))
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM usuario")).scalar_one(), 1)
        self.assertEqual(self.store.data, {})
        self.store.fail_commit = False
        with FirestoreSyncState(self.store) as state:
            self.stage(state)
        self.assertIsNotNone(state.get_link(1))

    def test_exclusive_lock_and_conflicting_links(self):
        with FirestoreSyncState(self.store) as first:
            first.lock_sync()
            with self.assertRaises(FirestoreStateError):
                FirestoreSyncState(self.store).lock_sync()
            first.stage_link(1, 10, "uid-1")
            with self.assertRaises(FirestoreStateError):
                first.stage_link(2, 10, "uid-2")
            with self.assertRaises(FirestoreStateError):
                first.stage_link(2, 11, "uid-1")
