import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from app.repositories.firestore_sync_state import FirestoreStateError, FirestoreSyncState


class FirestoreSyncStateTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.integration = self.client.collection.return_value.document.return_value
        self.state = FirestoreSyncState(self.client)

    def test_missing_and_existing_control(self):
        snapshot = self.integration.get.return_value
        snapshot.exists = False
        self.assertIsNone(self.state.last_synced_at())
        snapshot.exists = True
        now = datetime.now(timezone.utc)
        snapshot.to_dict.return_value = {"last_synced_at": now}
        self.assertEqual(self.state.last_synced_at(), now)
        snapshot.to_dict.return_value = {"last_synced_at": datetime(2026, 1, 1)}
        with self.assertRaises(FirestoreStateError):
            self.state.last_synced_at()

    def test_reads_existing_hashes_and_ignores_missing_documents(self):
        found = Mock(id="-2", exists=True)
        found.to_dict.return_value = {"data_hash": "a" * 64}
        missing = Mock(id="3", exists=False)
        self.client.get_all.return_value = [missing, found]
        self.assertEqual(self.state.confirmed_hashes([-2, 3, -2]), {-2: "a" * 64})
        self.assertEqual(len(self.client.get_all.call_args.args[0]), 2)

    def test_corrupt_hash_and_provider_error_fail_closed(self):
        found = Mock(id="1", exists=True)
        found.to_dict.return_value = {"data_hash": "invalid"}
        self.client.get_all.return_value = [found]
        with self.assertRaises(FirestoreStateError):
            self.state.confirmed_hashes([1])
        self.client.get_all.side_effect = RuntimeError("credencial sigilosa")
        with self.assertRaises(FirestoreStateError) as caught:
            self.state.confirmed_hashes([1])
        self.assertNotIn("credencial", str(caught.exception))

    def test_empty_and_invalid_ids_do_not_read(self):
        self.assertEqual(self.state.confirmed_hashes([]), {})
        with self.assertRaises(ValueError):
            self.state.confirmed_hashes([True])
        self.client.get_all.assert_not_called()
