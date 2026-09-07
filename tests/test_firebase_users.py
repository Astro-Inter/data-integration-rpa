"""Cenários do Admin SDK simulados; nenhuma conta real é acessada."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from firebase_admin import auth, exceptions
from sqlalchemy import create_engine, select, text

from app.database.sync_schema import metadata, sync_users
from app.repositories.sync_state_repository import SyncStateRepository
from app.services.change_tracking_service import ChangeTrackingService
from app.services.firebase_user_service import FirebaseUserError, FirebaseUserService
from app.services.user_preparation_service import prepare_user
from app.services.user_sync_processor import UserSyncProcessor
import test_change_tracking


def record(uid="firebase-uid", email="ana@example.com", disabled=False):
    return SimpleNamespace(uid=uid, email=email, disabled=disabled)


class FirebaseUserTests(unittest.TestCase):
    def setUp(self):
        self.app = object()
        self.user = prepare_user(test_change_tracking.user())
        self.service = FirebaseUserService(self.app, dry_run=False)
        patcher = patch("app.services.firebase_user_service.auth", wraps=auth)
        self.sdk = patcher.start()
        self.addCleanup(patcher.stop)
        # Exception classes must remain real for except clauses.
        self.sdk.UserNotFoundError = auth.UserNotFoundError
        self.sdk.EmailAlreadyExistsError = auth.EmailAlreadyExistsError

    def test_existing_returns_uid_and_preserves_disabled_account(self):
        self.sdk.get_user_by_email.return_value = record(disabled=True)
        result = self.service.ensure_user(self.user)
        self.assertEqual((result.uid, result.outcome, result.disabled), ("firebase-uid", "existing", True))
        self.sdk.get_user_by_email.assert_called_once_with("ana@example.com", app=self.app)
        self.sdk.create_user.assert_not_called()
        self.sdk.update_user.assert_not_called()
        self.sdk.delete_user.assert_not_called()

    def test_missing_user_created_with_only_prepared_fields(self):
        self.sdk.get_user_by_email.side_effect = auth.UserNotFoundError("ausente")
        self.sdk.create_user.return_value = record()
        result = self.service.ensure_user(self.user)
        self.assertEqual((result.uid, result.outcome), ("firebase-uid", "created"))
        self.sdk.create_user.assert_called_once_with(
            email="ana@example.com", display_name="Ana", email_verified=False, app=self.app,
        )

    def test_simulation_returns_existing_or_would_create_without_fake_uid(self):
        service = FirebaseUserService(self.app, dry_run=True)
        self.sdk.get_user_by_email.return_value = record()
        self.assertEqual(service.ensure_user(self.user).uid, "firebase-uid")
        self.sdk.get_user_by_email.side_effect = auth.UserNotFoundError("ausente")
        result = service.ensure_user(self.user)
        self.assertEqual((result.uid, result.outcome), (None, "would_create"))
        self.sdk.create_user.assert_not_called()

    def test_concurrent_creation_is_recovered_by_email(self):
        self.sdk.get_user_by_email.side_effect = [auth.UserNotFoundError("ausente"), record()]
        self.sdk.create_user.side_effect = auth.EmailAlreadyExistsError("conflito", None, None)
        result = self.service.ensure_user(self.user)
        self.assertEqual((result.uid, result.outcome), ("firebase-uid", "recovered"))
        self.assertEqual(self.sdk.create_user.call_count, 1)
        self.assertEqual(self.sdk.get_user_by_email.call_count, 2)

    def test_ambiguous_timeout_reconciles_without_second_create(self):
        for failure in (TimeoutError("segredo"), exceptions.FirebaseError("UNAVAILABLE", "segredo")):
            with self.subTest(failure=type(failure).__name__):
                self.sdk.reset_mock()
                self.sdk.get_user_by_email.side_effect = [auth.UserNotFoundError("ausente"), record()]
                self.sdk.create_user.side_effect = failure
                self.assertEqual(self.service.ensure_user(self.user).outcome, "recovered")
                self.sdk.create_user.assert_called_once()

    def test_unconfirmed_creation_fails_without_uid_or_second_write(self):
        self.sdk.get_user_by_email.side_effect = auth.UserNotFoundError("segredo")
        self.sdk.create_user.side_effect = TimeoutError("segredo")
        with self.assertRaises(FirebaseUserError) as caught:
            self.service.ensure_user(self.user)
        self.assertNotIn("segredo", str(caught.exception))
        self.sdk.create_user.assert_called_once()

    def test_lookup_errors_never_trigger_creation(self):
        self.sdk.get_user_by_email.side_effect = exceptions.FirebaseError("PERMISSION_DENIED", "token-secreto")
        with self.assertRaises(FirebaseUserError) as caught:
            self.service.ensure_user(self.user)
        self.assertNotIn("token-secreto", str(caught.exception))
        self.sdk.create_user.assert_not_called()

    def test_permanent_create_failure_is_not_retried(self):
        self.sdk.get_user_by_email.side_effect = auth.UserNotFoundError("ausente")
        self.sdk.create_user.side_effect = exceptions.FirebaseError("INVALID_ARGUMENT", "email-secreto")
        with self.assertRaises(FirebaseUserError) as caught:
            self.service.ensure_user(self.user)
        self.assertNotIn("email-secreto", str(caught.exception))
        self.sdk.get_user_by_email.assert_called_once()

    def test_known_uid_is_used_before_email(self):
        self.sdk.get_user.return_value = record()
        self.assertEqual(self.service.ensure_user(self.user, known_uid="firebase-uid").uid, "firebase-uid")
        self.sdk.get_user.assert_called_once_with("firebase-uid", app=self.app)
        self.sdk.get_user_by_email.assert_not_called()
        self.sdk.create_user.assert_not_called()

    def test_known_uid_missing_or_email_changed_does_not_create_new_account(self):
        for account in (None, record(email="outra@example.com"), record(uid="outro-uid")):
            with self.subTest(account=account):
                self.sdk.get_user.side_effect = auth.UserNotFoundError("ausente") if account is None else None
                self.sdk.get_user.return_value = account
                with self.assertRaises(FirebaseUserError):
                    self.service.ensure_user(self.user, known_uid="firebase-uid")
        self.sdk.create_user.assert_not_called()
        self.sdk.get_user_by_email.assert_not_called()

    def test_invalid_response_is_not_accepted(self):
        for account in (record(uid=""), record(uid="x" * 129), record(email=None), record(email="outra@example.com")):
            with self.subTest(account=account):
                self.sdk.get_user_by_email.return_value = account
                with self.assertRaises(FirebaseUserError):
                    self.service.ensure_user(self.user)
        self.sdk.create_user.assert_not_called()

    def test_requires_validated_input_and_explicit_app_and_mode(self):
        with self.assertRaises(FirebaseUserError):
            self.service.ensure_user(test_change_tracking.user())
        for uid in ("", "x" * 129, 42):
            with self.subTest(uid=uid), self.assertRaises(FirebaseUserError):
                self.service.ensure_user(self.user, known_uid=uid)
        with self.assertRaises(ValueError):
            FirebaseUserService(None, dry_run=False)
        with self.assertRaises(ValueError):
            FirebaseUserService(self.app, dry_run="false")
        self.sdk.create_user.assert_not_called()


class FirebasePipelineTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE destino_teste (id INTEGER PRIMARY KEY, uid TEXT)"))
        self.firebase = FirebaseUserService(object(), dry_run=False)
        self.resolve = Mock(return_value=None)
        self.persist = Mock(side_effect=self.save)
        self.processor = UserSyncProcessor(self.firebase, resolve_identity=self.resolve, persist_user=self.persist)

    def save(self, user, uid, connection):
        connection.execute(text("INSERT INTO destino_teste VALUES (:id, :uid)"), {"id": user.legacy_id, "uid": uid})

    def run_sync(self, *, dry_run=False):
        source = Mock()
        source.iter_batches.return_value = [[test_change_tracking.user()]]
        with self.engine.begin() as connection:
            return ChangeTrackingService(source, SyncStateRepository(connection)).run(
                100, dry_run=dry_run, process_user=self.processor,
            )

    def test_db_failure_after_firebase_creation_reuses_uid_on_retry(self):
        with patch("app.services.firebase_user_service.auth.get_user_by_email") as find:
            with patch("app.services.firebase_user_service.auth.create_user", return_value=record()) as create:
                find.side_effect = [auth.UserNotFoundError("ausente"), record()]
                self.persist.side_effect = RuntimeError("destino indisponível")
                with self.assertRaises(RuntimeError):
                    self.run_sync()
                with self.engine.connect() as connection:
                    self.assertIsNone(SyncStateRepository(connection).last_synced_at())
                    self.assertEqual(connection.execute(select(sync_users)).all(), [])
                self.persist.side_effect = self.save
                self.assertEqual(self.run_sync().confirmed, 1)
                create.assert_called_once()
                with self.engine.connect() as connection:
                    self.assertEqual(connection.execute(text("SELECT uid FROM destino_teste")).scalar_one(), "firebase-uid")
                    self.assertIsNotNone(SyncStateRepository(connection).last_synced_at())

    def test_firebase_failure_never_calls_persistence_or_confirms_state(self):
        with patch("app.services.firebase_user_service.auth.get_user_by_email", side_effect=exceptions.FirebaseError("UNAVAILABLE", "segredo")):
            with self.assertRaises(FirebaseUserError):
                self.run_sync()
        self.persist.assert_not_called()
        with self.engine.connect() as connection:
            self.assertIsNone(SyncStateRepository(connection).last_synced_at())
            self.assertEqual(connection.execute(select(sync_users)).all(), [])

    def test_identity_conflict_prevents_firebase_access(self):
        self.resolve.side_effect = ValueError("Conflito de CPF no destino")
        with patch.object(self.firebase, "ensure_user") as ensure:
            with self.assertRaises(ValueError):
                self.run_sync()
            ensure.assert_not_called()
        self.persist.assert_not_called()

    def test_simulation_does_not_call_processor_or_advance_checkpoint(self):
        with patch.object(self.firebase, "ensure_user") as ensure:
            summary = self.run_sync(dry_run=True)
            self.assertEqual((summary.validated, summary.confirmed), (1, 0))
            ensure.assert_not_called()
        self.persist.assert_not_called()
        self.resolve.assert_not_called()

    def test_processor_refuses_dry_run_even_if_called_directly(self):
        self.firebase.dry_run = True
        with self.engine.begin() as connection, self.assertRaises(FirebaseUserError):
            self.processor(prepare_user(test_change_tracking.user()), connection)
        self.resolve.assert_not_called()
        self.persist.assert_not_called()
