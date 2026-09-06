"""Ponto de entrada para configuração e verificação das integrações do RPA."""

import logging

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import Settings
from app.services.integrations import IntegrationError, open_integrations
from app.repositories.legacy_user_repository import LegacyReadError, LegacyUserRepository


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    try:
        settings = Settings()
    except ValidationError as exc:
        # Exibe apenas campos e tipos de erro, nunca valores fornecidos.
        for error in exc.errors(include_input=False, include_url=False):
            logger.error(
                "Configuração inválida: %s (%s)",
                ".".join(str(part) for part in error["loc"]),
                error["type"],
            )
        return 1

    logging.getLogger().setLevel(settings.log_level)
    # DEBUG do RPA não habilita logs de SQL, tokens ou respostas dos provedores.
    for name in ("sqlalchemy", "firebase_admin", "google", "urllib3", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logger.info("Configuração inicial validada.")
    logger.info(
        "Lote: %s; modo de teste solicitado: %s.",
        settings.sync_batch_size,
        settings.sync_dry_run,
    )
    try:
        with open_integrations(settings) as integrations:
            logger.info("Conexões PostgreSQL e acesso ao Firebase Authentication validados.")
            total = 0
            with integrations.legacy.connect() as connection:
                repository = LegacyUserRepository(connection)
                for batch in repository.iter_batches(settings.sync_batch_size):
                    total += len(batch)
                    logger.debug("Lote consultado: %s funcionários.", len(batch))
            logger.info("Consulta do legado concluída: %s funcionários encontrados.", total)
            logger.warning("Sincronização ainda não implementada. Nenhum registro foi alterado.")
    except (IntegrationError, LegacyReadError) as exc:
        logger.error("%s", exc)
        return 1
    except SQLAlchemyError:
        logger.error("Falha de conexão durante a leitura do legado. Verifique acesso ao banco.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
