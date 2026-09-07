"""Ponto de entrada para configuração e verificação das integrações do RPA."""

import logging
import signal
import threading
from time import monotonic
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import Settings
from app.services.integrations import IntegrationError, open_integrations
from app.repositories.legacy_user_repository import LegacyReadError, LegacyUserRepository
from app.repositories.sync_state_repository import SyncStateRepository
from app.services.change_tracking_service import ChangeTrackingService
from app.services.user_preparation_service import UserDataError
from app.services.firebase_user_service import FirebaseUserError, FirebaseUserService
from app.services.user_sync_processor import UserSyncProcessor
from app.repositories.target_user_repository import TargetIdentityError, TargetUserRepository


def run() -> int:
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
                processor = UserSyncProcessor(
                    FirebaseUserService(integrations.firebase, dry_run=settings.sync_dry_run),
                    resolve_identity=lambda user, db: TargetUserRepository(db).resolve_identity(user),
                    persist_user=lambda user, uid, db: TargetUserRepository(db).persist(user, uid),
                )
                summary = ChangeTrackingService(repository, SyncStateRepository(target)).run(
                    settings.sync_batch_size, dry_run=settings.sync_dry_run,
                    process_user=processor,
                )
            logger.info("Consulta do legado concluída: %s funcionários encontrados.", summary.total)
            logger.info("Novos: %s; alterados: %s; sem alteração: %s.",
                        summary.new, summary.changed, summary.unchanged)
            logger.info("Última sincronização confirmada: %s.", summary.last_synced_at or "nenhuma")
            logger.info("Candidatos válidos: %s; inválidos: %s.", summary.validated, summary.invalid)
            for field, count in sorted(summary.validation_errors.items()):
                logger.error("Falha de validação em %s: %s registros.", field, count)
            if settings.sync_dry_run:
                logger.info("Simulação concluída. Nenhum registro foi alterado.")
            else:
                logger.info("Sincronização concluída: %s usuários confirmados.", summary.confirmed)
            if summary.invalid:
                return 1
    except (IntegrationError, LegacyReadError, UserDataError, FirebaseUserError, TargetIdentityError) as exc:
        logger.error("%s", exc)
        return 1
    except SQLAlchemyError:
        logger.error("Falha de acesso aos bancos ou ao controle. Verifique as tabelas rpa_sync_control e rpa_sync_users no destino.")
        return 1
    return 0


class RunInterrupted(BaseException):
    """Interrupção que faz rollback e não é convertida em erro pelos adaptadores."""


def _terminate(signum, frame):
    raise RunInterrupted()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)
    run_id = uuid4().hex
    started = monotonic()
    code = 1
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _terminate)
    logger.info("Execução iniciada: run_id=%s.", run_id)
    try:
        code = run()
    except RunInterrupted:
        code = 143
        logger.error("Execução interrompida por SIGTERM; transações abertas serão revertidas.")
    except KeyboardInterrupt:
        code = 130
        logger.error("Execução interrompida pelo operador.")
    except Exception:
        # Inclusive falhas inesperadas no encerramento: nunca despejar dados de SDK/SQL.
        logger.error("Falha inesperada na execução. Verifique serviços e configuração; nenhuma confirmação adicional será realizada.")
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        logger.log(logging.INFO if code == 0 else logging.ERROR,
                   "Execução encerrada: run_id=%s exit_code=%s duracao_segundos=%.3f.",
                   run_id, code, monotonic() - started)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
