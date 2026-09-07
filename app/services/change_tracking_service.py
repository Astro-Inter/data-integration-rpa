"""Detecção por hash dos dados brutos, com confirmação após persistência."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json

from sqlalchemy import Connection

from app.models.legacy_user import LegacyUser
from app.models.prepared_user import PreparedUser
from app.services.user_preparation_service import UserDataError, prepare_user
from app.repositories.legacy_user_repository import LegacyUserRepository
from app.repositories.sync_state_repository import SyncStateRepository


def user_fingerprint(user: LegacyUser) -> str:
    payload = asdict(user)
    payload["emails"] = sorted(payload["emails"], key=lambda email: email["id_email"])
    # A versão faz uma mudança futura do contrato provocar novo processamento.
    serialized = json.dumps(
        {"version": 3, "user": payload}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class ChangeSummary:
    total: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    confirmed: int = 0
    validated: int = 0
    invalid: int = 0
    validation_errors: dict[str, int] = field(default_factory=dict)
    last_synced_at: datetime | None = None


class ChangeTrackingService:
    def __init__(self, legacy: LegacyUserRepository, state: SyncStateRepository):
        self.legacy = legacy
        self.state = state

    def run(
        self, batch_size: int, *, dry_run: bool = False,
        process_user: Callable[[PreparedUser, Connection], None] | None = None,
        on_invalid: Callable[[UserDataError], None] | None = None,
    ) -> ChangeSummary:
        """Chamador deve usar target.begin(); falhas devem sair do bloco e dar rollback.

        process_user retorna apenas após Firebase e persistência no destino terem
        funcionado; deve usar a conexão recebida, sem commit próprio. Sem esse
        processador, a execução só detecta alterações, mesmo com dry_run=False.
        """
        if not self.state.connection.in_transaction():
            raise RuntimeError("O controle exige uma transação externa no destino.")
        confirm = process_user is not None and not dry_run
        if confirm:
            self.state.lock_sync()
        summary = ChangeSummary(last_synced_at=self.state.last_synced_at())
        for batch in self.legacy.iter_batches(batch_size):
            hashes = self.state.confirmed_hashes([user.id_funcionario for user in batch])
            for user in batch:
                summary.total += 1
                fingerprint = user_fingerprint(user)
                previous = hashes.get(user.id_funcionario)
                if previous == fingerprint:
                    summary.unchanged += 1
                    continue
                if previous is None:
                    summary.new += 1
                else:
                    summary.changed += 1
                try:
                    prepared = prepare_user(user)
                except UserDataError as exc:
                    if on_invalid is not None:
                        on_invalid(exc)
                    if confirm:
                        # A transação externa reverte inclusive usuários anteriores.
                        raise
                    summary.invalid += 1
                    for name in exc.fields:
                        summary.validation_errors[name] = summary.validation_errors.get(name, 0) + 1
                    continue
                summary.validated += 1
                if confirm:
                    process_user(prepared, self.state.connection)
                    self.state.confirm_user(
                        user.id_funcionario, fingerprint, datetime.now(timezone.utc)
                    )
                    summary.confirmed += 1
        if confirm:
            summary.last_synced_at = datetime.now(timezone.utc)
            self.state.confirm_run(summary.last_synced_at)
        return summary
