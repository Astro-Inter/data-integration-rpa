"""Double Firestore compartilhado para testar o ciclo de commit sem rede."""
from types import SimpleNamespace
from copy import deepcopy
from google.api_core.exceptions import AlreadyExists


class MemoryFirestore:
    def __init__(self):
        self.data = {}
        self.writes = []
        self.fail_commit = False

    def collection(self, path):
        return Collection(self, path)

    def get_all(self, refs, **kwargs):
        return [ref.get() for ref in refs]

    def batch(self):
        store = self
        class Batch:
            def __init__(self): self.pending = []
            def set(self, ref, data): self.pending.append((ref, data))
            def commit(self, **kwargs):
                if store.fail_commit: raise RuntimeError("provider secret")
                for ref, data in self.pending: ref.set(data)
        return Batch()


class Collection:
    def __init__(self, client, path): self.client, self.path = client, path
    def document(self, key): return Document(self.client, self.path + "/" + key)


class Document(Collection):
    def collection(self, path): return Collection(self.client, self.path + "/" + path)
    def get(self, **kwargs):
        data = deepcopy(self.client.data.get(self.path))
        return SimpleNamespace(exists=data is not None, id=self.path.split("/")[-1], to_dict=lambda: data)
    def set(self, data, merge=False, **kwargs):
        old = self.client.data.get(self.path, {}) if merge else {}
        self.client.data[self.path] = deepcopy(old | data)
        self.client.writes.append(self.path)
    def create(self, data, **kwargs):
        if self.path in self.client.data: raise AlreadyExists("exists")
        self.set(data)
        return SimpleNamespace(update_time="version")
    def delete(self, **kwargs): self.client.data.pop(self.path, None)
