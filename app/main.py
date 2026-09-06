"""Ponto de entrada para configuração e verificação das integrações do RPA."""

import logging

from pydantic import ValidationError

from app.config.settings import Settings
from app.services.integrations import IntegrationError, open_integrations


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
        with open_integrations(settings):
            logger.info("Conexões PostgreSQL e acesso ao Firebase Authentication validados.")
            logger.warning("Sincronização ainda não implementada. Nenhum registro foi alterado.")
    except IntegrationError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
