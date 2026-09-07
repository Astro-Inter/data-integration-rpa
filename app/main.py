"""Ponto de entrada para configuração e verificação das integrações do RPA."""

import logging

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import Settings
from app.services.integrations import IntegrationError, open_integrations
from app.repositories.legacy_user_repository import LegacyReadError, LegacyUserRepository
from app.repositories.sync_state_repository import SyncStateRepository
from app.services.change_tracking_service import ChangeTrackingService


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
            with integrations.legacy.connect() as connection, integrations.target.begin() as target:
                repository = LegacyUserRepository(connection)
                summary = ChangeTrackingService(repository, SyncStateRepository(target)).run(
                    settings.sync_batch_size, dry_run=settings.sync_dry_run,
                )
            logger.info("Consulta do legado concluída: %s funcionários encontrados.", summary.total)
            logger.info("Novos: %s; alterados: %s; sem alteração: %s.",
                        summary.new, summary.changed, summary.unchanged)
            logger.info("Última sincronização confirmada: %s.", summary.last_synced_at or "nenhuma")
            logger.warning("Sincronização ainda não implementada. Nenhum registro foi alterado.")
    except (IntegrationError, LegacyReadError) as exc:
        logger.error("%s", exc)
        return 1
    except SQLAlchemyError:
        logger.error("Falha de acesso aos bancos ou ao controle. Verifique as tabelas rpa_sync_control e rpa_sync_users no destino.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
