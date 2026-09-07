"""Tabelas de controle no destino, independentes das tabelas de negócio."""

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, UniqueConstraint

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

user_links = Table(
    "rpa_user_links", metadata,
    Column("integration_key", String(100), primary_key=True),
    Column("legacy_id", BigInteger, primary_key=True),
    Column("target_id", BigInteger, nullable=False),
    Column("firebase_uid", String(128), nullable=False),
    UniqueConstraint("target_id", name="uq_rpa_user_links_target"),
    UniqueConstraint("firebase_uid", name="uq_rpa_user_links_uid"),
)
