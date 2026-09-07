"""Localização/criação idempotente no Firebase, sem persistência no PostgreSQL."""

from dataclasses import dataclass
from typing import Literal

import firebase_admin
from firebase_admin import auth, exceptions
from google.auth.exceptions import GoogleAuthError, TransportError
from requests.exceptions import RequestException

from app.models.prepared_user import PreparedUser, normalize_email


class FirebaseUserError(RuntimeError):
    """Erro seguro para exibição; não contém resposta bruta do provedor."""


@dataclass(frozen=True, repr=False)
class FirebaseUserResult:
    uid: str | None
    outcome: Literal["existing", "created", "recovered", "would_create"]
    disabled: bool | None


class FirebaseUserService:
    def __init__(self, app: firebase_admin.App, *, dry_run: bool):
        if app is None or not isinstance(dry_run, bool):
            raise ValueError("Informe uma instância Firebase e um modo de execução booleano.")
        self.app = app
        self.dry_run = dry_run

    def ensure_user(self, user: PreparedUser, *, known_uid: str | None = None) -> FirebaseUserResult:
        """known_uid deve vir do vínculo previamente validado no destino.

        Localizar por e-mail não comprova que o CPF pertence à conta: o chamador
        deve validar conflitos no destino antes de vincular/persistir esse UID.
        """
        if not isinstance(user, PreparedUser):
            raise FirebaseUserError("A operação exige dados de usuário preparados e validados.")
        stage = "consulta"
        try:
            if known_uid is not None:
                if not isinstance(known_uid, str) or not 1 <= len(known_uid) <= 128:
                    raise FirebaseUserError("UID previamente vinculado é inválido.")
                try:
                    record = auth.get_user(known_uid, app=self.app)
                except auth.UserNotFoundError:
                    raise FirebaseUserError("A conta Firebase vinculada não existe. Revise o vínculo antes de recriar.") from None
                if record.uid != known_uid:
                    raise FirebaseUserError("O Firebase retornou um UID diferente do vínculo informado.")
                return self._result(record, user, "existing")

            record = self._find_by_email(user.usuario.email)
            if record is not None:
                return self._result(record, user, "existing")
            if self.dry_run:
                return FirebaseUserResult(uid=None, outcome="would_create", disabled=None)

            stage = "criação"
            try:
                record = auth.create_user(**user.firebase_payload(), app=self.app)
            except auth.EmailAlreadyExistsError:
                # Outra execução pode criar entre a consulta e o create_user.
                return self._recover(user)
            except exceptions.FirebaseError as exc:
                if exc.code not in {"DEADLINE_EXCEEDED", "UNAVAILABLE", "INTERNAL", "UNKNOWN"}:
                    raise
                # A criação pode ter sido concluída antes da falha de resposta.
                return self._recover(user)
            except (TransportError, RequestException, TimeoutError):
                return self._recover(user)
            return self._result(record, user, "created")
        except FirebaseUserError:
            raise
        except (exceptions.FirebaseError, GoogleAuthError, RequestException, TimeoutError, ValueError):
            raise FirebaseUserError(f"Falha na {stage} da conta Firebase. Verifique acesso e disponibilidade.") from None

    def _find_by_email(self, email: str):
        try:
            return auth.get_user_by_email(email, app=self.app)
        except auth.UserNotFoundError:
            return None

    def _recover(self, user: PreparedUser) -> FirebaseUserResult:
        record = self._find_by_email(user.usuario.email)
        if record is None:
            raise FirebaseUserError("Não foi possível confirmar a conta Firebase após a tentativa de criação. Tente novamente depois.")
        return self._result(record, user, "recovered")

    @staticmethod
    def _result(record, user: PreparedUser, outcome: str) -> FirebaseUserResult:
        if not isinstance(record.uid, str) or not 1 <= len(record.uid) <= 128:
            raise FirebaseUserError("O Firebase retornou uma conta sem UID válido.")
        try:
            matches = normalize_email(record.email) == user.usuario.email
        except ValueError:
            matches = False
        if not matches:
            raise FirebaseUserError("O e-mail da conta Firebase diverge do usuário preparado. Revise a identidade antes de alterar o vínculo.")
        # Não atualiza nome, senha, e-mail, verificação, claims ou disabled de contas existentes.
        return FirebaseUserResult(uid=record.uid, outcome=outcome, disabled=record.disabled)
