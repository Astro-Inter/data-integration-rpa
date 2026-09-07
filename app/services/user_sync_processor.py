"""Liga o Firebase à persistência, sem confirmar sincronização prematura."""

from collections.abc import Callable

from sqlalchemy import Connection

from app.models.prepared_user import PreparedUser
from app.services.firebase_user_service import FirebaseUserError, FirebaseUserService


class UserSyncProcessor:
    def __init__(
        self, firebase: FirebaseUserService, *,
        resolve_identity: Callable[[PreparedUser, Connection], str | None],
        persist_user: Callable[[PreparedUser, str, Connection], None],
    ):
        # Os dois callbacks são obrigatórios: não existe modo "Firebase OK = sincronizado".
        self.firebase = firebase
        self.resolve_identity = resolve_identity
        self.persist_user = persist_user

    def __call__(self, user: PreparedUser, connection: Connection) -> None:
        if self.firebase.dry_run:
            raise FirebaseUserError("O processador de gravação não pode executar em modo de simulação.")
        if not connection.in_transaction():
            raise FirebaseUserError("O processador exige uma transação externa no destino.")
        # Deve verificar CPF/e-mail/UID e propriedade da conta no destino antes do Firebase.
        known_uid = self.resolve_identity(user, connection)
        result = self.firebase.ensure_user(user, known_uid=known_uid)
        if result.uid is None:
            raise FirebaseUserError("Não há UID confirmado para persistir o usuário.")
        self.persist_user(user, result.uid, connection)
