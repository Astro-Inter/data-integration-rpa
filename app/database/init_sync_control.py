"""Preparação explícita das tabelas de controle no PostgreSQL destino."""

import logging

from app.config.settings import Settings
from app.database.connections import create_database_engine
from app.database.sync_schema import metadata


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    engine = None
    try:
        settings = Settings()
        if settings.sync_dry_run:
            logging.error("A preparação do controle exige SYNC_DRY_RUN=false.")
            return 1
        engine = create_database_engine(settings, legacy=False)
        with engine.begin() as connection:
            metadata.create_all(connection)
        logging.info("Tabelas de controle preparadas no destino.")
        return 0
    except Exception:
        logging.error("Falha ao preparar controle. Verifique configuração e permissão de criação no destino.")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
