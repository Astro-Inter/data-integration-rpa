"""Tabelas de controle no destino, independentes das tabelas de negócio."""

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table

metadata = MetaData()

sync_control = Table(
    "rpa_sync_control", metadata,
    Column("integration_key", String(100), primary_key=True),
    Column("last_synced_at", DateTime(timezone=True), nullable=True),
)

sync_users = Table(
    "rpa_sync_users", metadata,
    Column("integration_key", String(100), primary_key=True),
    Column("legacy_id", BigInteger, primary_key=True),
    Column("data_hash", String(64), nullable=False),
    Column("synced_at", DateTime(timezone=True), nullable=False),
)
