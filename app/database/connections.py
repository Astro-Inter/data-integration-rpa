"""Engines independentes para os bancos, sem conexões durante importação."""

from sqlalchemy import URL, Engine, create_engine, text

from app.config.settings import Settings


def create_database_engine(settings: Settings, *, legacy: bool) -> Engine:
    prefix = "legacy" if legacy else "target"
    url = URL.create(
        "postgresql+psycopg",
        username=getattr(settings, f"{prefix}_db_user"),
        password=getattr(settings, f"{prefix}_db_password").get_secret_value(),
        host=getattr(settings, f"{prefix}_db_host"),
        port=getattr(settings, f"{prefix}_db_port"),
        database=getattr(settings, f"{prefix}_db_name"),
    )
    options = "-c statement_timeout=30000"
    if legacy or settings.sync_dry_run:
        options += " -c default_transaction_read_only=on"
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        hide_parameters=True,
        connect_args={"connect_timeout": 10, "options": options},
    )


def check_database_connection(engine: Engine) -> None:
    """Consulta constante; não lê nem modifica tabelas da aplicação."""
    with engine.connect() as connection:
        if connection.execute(text("SELECT 1")).scalar_one() != 1:
            raise RuntimeError("Resposta inesperada do PostgreSQL.")
