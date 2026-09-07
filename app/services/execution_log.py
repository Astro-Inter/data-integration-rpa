"""Histórico operacional no Firestore, independente da transação de negócio."""

from datetime import datetime, timezone
from enum import StrEnum
import logging

import firebase_admin
from firebase_admin import firestore

from app.config.settings import Settings
from app.services.firebase_service import initialize_firebase


class Event(StrEnum):
    STARTED = "EXECUCAO_INICIADA"
    CONNECTING = "VALIDANDO_CONEXOES"
    CONNECTED = "CONEXOES_VALIDADAS"
    PROCESSING = "PROCESSANDO_USUARIOS"
    COMMITTED = "TRANSACAO_CONCLUIDA"
    INTEGRATION_ERROR = "FALHA_INTEGRACAO"
    LEGACY_ERROR = "FALHA_CONSULTA_LEGADO"
    VALIDATION_ERROR = "FALHA_VALIDACAO"
    FIREBASE_ERROR = "FALHA_FIREBASE_AUTH"
    IDENTITY_ERROR = "CONFLITO_IDENTIDADE_DESTINO"
    DATABASE_ERROR = "FALHA_POSTGRESQL"
    UNEXPECTED_ERROR = "FALHA_INESPERADA"
    INTERRUPTED = "EXECUCAO_INTERROMPIDA"
    FINISHED = "EXECUCAO_ENCERRADA"
    LOG_CHECK = "VERIFICACAO_HISTORICO"


class ExecutionLog:
    """Aceita apenas eventos conhecidos e métricas, nunca texto de exceções.

    Uma app Firebase própria permite registrar falhas durante a abertura ou o
    fechamento das integrações. O histórico tem tamanho limitado por execução.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._app = None
        self._client = None
        self._document = None
        self._failed = False
        self._data = {
            "run_id": run_id,
            "iniciado_em": datetime.now(timezone.utc),
            "status": "EM_EXECUCAO",
            "eventos": [],
        }

    @property
    def available(self) -> bool:
        return self._document is not None and not self._failed

    def configure(self, settings: Settings) -> None:
        if not settings.firestore_logs_enabled:
            return
        self._data["dry_run"] = settings.sync_dry_run
        try:
            self._app = initialize_firebase(settings)
            self._client = firestore.client(self._app, database_id=settings.firestore_database_id)
            self._document = self._client.collection("rpa_execucoes").document(self.run_id)
            self.event(Event.STARTED)
        except Exception:
            self._disable()

    def _disable(self) -> None:
        if not self._failed:
            logging.getLogger(__name__).warning(
                "Histórico Firestore indisponível: run_id=%s. Consulte os logs do terminal; "
                "verifique banco e permissão da conta de serviço. A sincronização continuará.",
                self.run_id,
            )
        self._failed = True

    def _save(self) -> None:
        if not self.available:
            return
        try:
            # Sem retry automático: indisponibilidade não atrasa cada etapa do RPA.
            self._document.set(self._data, retry=None, timeout=3)
        except Exception:
            self._disable()

    def event(self, event: Event) -> None:
        if not self.available:
            return
        if not isinstance(event, Event):
            raise ValueError("Evento operacional inválido")
        now = datetime.now(timezone.utc)
        self._data["ultima_etapa"] = event.value
        self._data["atualizado_em"] = now
        if len(self._data["eventos"]) < 50:
            self._data["eventos"].append({"codigo": event.value, "em": now})
        self._save()

    def summary(self, summary) -> None:
        if not self.available:
            return
        self._data["contagens"] = {
            key: int(getattr(summary, key))
            for key in ("total", "new", "changed", "unchanged", "confirmed", "validated", "invalid")
        }
        self._save()

    def finish(self, exit_code: int, duration: float) -> None:
        self._data.update({
            "status": "SUCESSO" if exit_code == 0 else (
                "INTERROMPIDA" if exit_code in (130, 143) else "FALHA"
            ),
            "exit_code": exit_code,
            "duracao_segundos": round(duration, 3),
            "encerrado_em": datetime.now(timezone.utc),
        })
        self.event(Event.FINISHED)

    def close(self) -> None:
        # Falha de telemetria/limpeza não pode alterar o resultado do negócio.
        client, self._client = self._client, None
        app, self._app = self._app, None
        self._document = None
        try:
            if client is not None:
                client.close()
        except Exception:
            self._disable()
        finally:
            if app is not None:
                try:
                    firebase_admin.delete_app(app)
                except Exception:
                    self._disable()
