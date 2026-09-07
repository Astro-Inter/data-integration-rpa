"""Histórico confirmado da integração no banco destino."""

from datetime import datetime

from sqlalchemy import Connection, DateTime, bindparam, select, text, update

from app.database.sync_schema import sync_control, sync_users


INTEGRATION_KEY = "usuarios_legado"


class SyncStateRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def last_synced_at(self) -> datetime | None:
        return self.connection.execute(
            select(sync_control.c.last_synced_at).where(
                sync_control.c.integration_key == INTEGRATION_KEY
            )
        ).scalar_one_or_none()

    def confirmed_hashes(self, legacy_ids: list[int]) -> dict[int, str]:
        if not legacy_ids:
            return {}
        rows = self.connection.execute(
            select(sync_users.c.legacy_id, sync_users.c.data_hash).where(
                sync_users.c.integration_key == INTEGRATION_KEY,
                sync_users.c.legacy_id.in_(legacy_ids),
            )
        )
        return dict(rows.tuples().all())

    def lock_sync(self) -> None:
        """Serializa execuções com escrita até commit/rollback do chamador."""
        self.connection.execute(text("""
            INSERT INTO rpa_sync_control (integration_key, last_synced_at)
            VALUES (:key, NULL) ON CONFLICT (integration_key) DO NOTHING
        """), {"key": INTEGRATION_KEY})
        self.connection.execute(
            select(sync_control.c.integration_key).where(
                sync_control.c.integration_key == INTEGRATION_KEY
            ).with_for_update()
        ).scalar_one()

    def confirm_user(self, legacy_id: int, data_hash: str, synced_at: datetime) -> None:
        self.connection.execute(text("""
            INSERT INTO rpa_sync_users (integration_key, legacy_id, data_hash, synced_at)
            VALUES (:key, :legacy_id, :data_hash, :synced_at)
            ON CONFLICT (integration_key, legacy_id) DO UPDATE
            SET data_hash = EXCLUDED.data_hash, synced_at = EXCLUDED.synced_at
        """).bindparams(bindparam("synced_at", type_=DateTime(timezone=True))),
            {"key": INTEGRATION_KEY, "legacy_id": legacy_id,
               "data_hash": data_hash, "synced_at": synced_at})

    def confirm_run(self, synced_at: datetime) -> None:
        self.connection.execute(
            update(sync_control).where(sync_control.c.integration_key == INTEGRATION_KEY)
            .values(last_synced_at=synced_at)
        )
