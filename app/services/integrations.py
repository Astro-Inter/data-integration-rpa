"""Ciclo de vida e verificação das integrações externas."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from collections.abc import Iterator

import firebase_admin
from sqlalchemy import Engine

from app.config.settings import Settings
from app.database.connections import create_database_engine, check_database_connection
from app.services.firebase_service import initialize_firebase, check_firebase_connection


class IntegrationError(RuntimeError):
    """Erro seguro para logs, sem mensagem original dos provedores."""


@dataclass
class Integrations:
    legacy: Engine
    target: Engine
    firebase: firebase_admin.App


@contextmanager
def open_integrations(settings: Settings) -> Iterator[Integrations]:
    stage = "inicialização do Firebase"
    with ExitStack() as stack:
        try:
            app = initialize_firebase(settings)
            stack.callback(firebase_admin.delete_app, app)
            stage = "conexão com PostgreSQL legado"
            legacy = create_database_engine(settings, legacy=True)
            stack.callback(legacy.dispose)
            check_database_connection(legacy)
            stage = "conexão com PostgreSQL destino"
            target = create_database_engine(settings, legacy=False)
            stack.callback(target.dispose)
            check_database_connection(target)
            stage = "acesso ao Firebase Authentication"
            check_firebase_connection(app)
        except Exception:
            # Exceções de drivers/SDK podem conter credenciais ou dados pessoais.
            raise IntegrationError(f"Falha na {stage}. Verifique configuração e acesso.") from None
        yield Integrations(legacy=legacy, target=target, firebase=app)
