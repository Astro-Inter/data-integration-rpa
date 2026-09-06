"""Inicialização do SDK com credenciais em memória."""

import base64
import binascii
import json
from uuid import uuid4

import firebase_admin
from firebase_admin import auth, credentials

from app.config.settings import Settings


def initialize_firebase(settings: Settings) -> firebase_admin.App:
    try:
        decoded = base64.b64decode(
            settings.firebase_credentials_base64.get_secret_value(), validate=True
        )
        document = json.loads(decoded)
        if not isinstance(document, dict):
            raise ValueError
        if document.get("type") != "service_account":
            raise ValueError
        if document.get("project_id") != settings.firebase_project_id:
            raise ValueError
        credential = credentials.Certificate(document)
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise ValueError(
            "Credenciais Firebase inválidas ou incompatíveis com FIREBASE_PROJECT_ID."
        ) from None

    # Cada execução possui sua instância e pode encerrá-la sem afetar outras apps.
    return firebase_admin.initialize_app(
        credential,
        options={"projectId": settings.firebase_project_id, "httpTimeout": 10},
        name=f"rpa-{uuid4().hex}",
    )


def check_firebase_connection(app: firebase_admin.App) -> None:
    """Verifica acesso ao Auth por leitura limitada, sem registrar dados pessoais."""
    auth.list_users(max_results=1, app=app)
