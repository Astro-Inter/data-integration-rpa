"""Controle Firestore, publicado somente após commit no PostgreSQL."""

from datetime import datetime, timezone
from uuid import uuid4
from hashlib import sha256


class FirestoreStateError(RuntimeError):
    """Estado indisponível ou inválido, sem expor dados do provedor."""


class FirestoreSyncState:
    def __init__(self, client, integration_key: str = "usuarios_legado"):
        if not integration_key or "/" in integration_key:
            raise ValueError("Identificador de integração inválido.")
        self.pending = {}
        self.links = {}
        self.completed_at = None
        self.lock_version = None
        self.connection = None
        self.client = client
        self.integration = client.collection("rpa_integracoes").document(integration_key)

    def last_synced_at(self) -> datetime | None:
        try:
            snapshot = self.integration.get(retry=None, timeout=10)
            data = snapshot.to_dict() if snapshot.exists else None
        except Exception:
            raise FirestoreStateError("Falha ao consultar controle no Firestore.") from None
        if data is None:
            return None
        value = data.get("last_synced_at")
        if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
            raise FirestoreStateError("Data de sincronização inválida no Firestore.")
        return value

    def confirmed_hashes(self, legacy_ids: list[int]) -> dict[int, str]:
        if any(isinstance(value, bool) or not isinstance(value, int) for value in legacy_ids):
            raise ValueError("Identificador de funcionário inválido.")
        ids = list(dict.fromkeys(legacy_ids))
        if not ids:
            return {}
        collection = self.integration.collection("usuarios")
        references = [collection.document(str(value)) for value in ids]
        try:
            snapshots = list(self.client.get_all(references, retry=None, timeout=10))
        except Exception:
            raise FirestoreStateError("Falha ao consultar usuários no Firestore.") from None
        allowed = {str(value): value for value in ids}
        hashes = {}
        for snapshot in snapshots:
            if not snapshot.exists:
                continue
            if snapshot.id not in allowed:
                raise FirestoreStateError("Identificador inesperado no Firestore.")
            data = snapshot.to_dict()
            fingerprint = data.get("data_hash") if isinstance(data, dict) else None
            if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(
                char not in "0123456789abcdef" for char in fingerprint
            ):
                raise FirestoreStateError("Hash de sincronização inválido no Firestore.")
            hashes[allowed[snapshot.id]] = fingerprint
        return hashes


    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None and self.lock_version is not None:
                self.flush()
        finally:
            self.release()

    def lock_sync(self):
        from google.api_core.exceptions import AlreadyExists
        self.lock_ref = self.client.collection("rpa_locks").document("usuarios_legado")
        try:
            result = self.lock_ref.create({"owner": uuid4().hex, "started_at": datetime.now(timezone.utc)}, retry=None, timeout=10)
            self.lock_version = result.update_time
        except AlreadyExists:
            raise FirestoreStateError("Outra execução detém o lock do RPA; verifique execuções interrompidas.") from None
        except Exception:
            raise FirestoreStateError("Falha ao adquirir lock no Firestore; confira rpa_locks antes de tentar novamente.") from None

    def release(self):
        if self.lock_version is None:
            return
        from google.cloud.firestore_v1 import LastUpdateOption
        try:
            self.lock_ref.delete(option=LastUpdateOption(self.lock_version), retry=None, timeout=10)
        except Exception:
            raise FirestoreStateError("Falha ao liberar lock no Firestore; confira rpa_locks.") from None
        finally:
            self.lock_version = None

    def _get(self, collection, key):
        try:
            snapshot = self.integration.collection(collection).document(str(key)).get(retry=None, timeout=10)
            return snapshot.to_dict() if snapshot.exists else None
        except Exception:
            raise FirestoreStateError("Falha ao consultar vínculos no Firestore.") from None

    def get_link(self, legacy_id):
        return self.links.get(legacy_id) or self._get("usuarios", legacy_id)

    def check_link(self, legacy_id, target_id, uid):
        for other_id, link in self.links.items():
            if other_id != legacy_id and (link["target_id"] == target_id or link["firebase_uid"] == uid):
                raise FirestoreStateError("Identidade destino já vinculada a outro funcionário.")
        for collection, key in (("destinos", str(target_id)), ("uids", sha256(uid.encode()).hexdigest())):
            other = self._get(collection, key)
            if other and other.get("legacy_id") != legacy_id:
                raise FirestoreStateError("Identidade destino já vinculada a outro funcionário.")

    def stage_link(self, legacy_id, target_id, uid):
        if self.lock_version is None:
            raise FirestoreStateError("Escrita de vínculo exige lock do RPA.")
        self.check_link(legacy_id, target_id, uid)
        self.links[legacy_id] = {"target_id": target_id, "firebase_uid": uid}

    def confirm_user(self, legacy_id, data_hash, synced_at):
        self.pending[legacy_id] = {"data_hash": data_hash, "synced_at": synced_at}

    def confirm_run(self, synced_at):
        self.completed_at = synced_at

    def flush(self):
        if self.lock_version is None:
            raise FirestoreStateError("Confirmação exige lock do RPA.")
        try:
            # Cada usuário e seus índices são atômicos; o marcador da execução é o último.
            for legacy_id, values in self.pending.items():
                link = self.links[legacy_id]
                batch = self.client.batch()
                batch.set(self.integration.collection("usuarios").document(str(legacy_id)), values | link)
                batch.set(self.integration.collection("destinos").document(str(link["target_id"])), {"legacy_id": legacy_id})
                batch.set(self.integration.collection("uids").document(sha256(link["firebase_uid"].encode()).hexdigest()), {"legacy_id": legacy_id})
                batch.commit(retry=None, timeout=10)
            if self.completed_at is not None:
                self.integration.set({"last_synced_at": self.completed_at}, merge=True, retry=None, timeout=10)
        except Exception:
            raise FirestoreStateError("PostgreSQL confirmado, mas controle Firestore incompleto; execute novamente para reconciliar.") from None
