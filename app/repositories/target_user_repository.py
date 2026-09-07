"""Persistência do agregado de usuário, sob transação do chamador."""

from sqlalchemy import Connection, select, text

from app.database.sync_schema import user_links
from app.models.prepared_user import PreparedUser
from app.repositories.sync_state_repository import INTEGRATION_KEY


class TargetIdentityError(RuntimeError):
    """Conflito que exige revisão, sem valores pessoais na mensagem."""


class TargetUserRepository:
    def __init__(self, connection: Connection):
        self.connection = connection

    def _identity(self, user: PreparedUser, uid: str | None = None):
        if not self.connection.in_transaction():
            raise TargetIdentityError("A persistência exige transação externa.")
        params = {"cpf": user.usuario.cpf, "email": user.usuario.email, "uid": uid}
        link = self.connection.execute(select(user_links).where(
            user_links.c.integration_key == INTEGRATION_KEY,
            user_links.c.legacy_id == user.legacy_id,
        )).mappings().one_or_none()
        params["linked_id"] = link["target_id"] if link else None
        # Herança PostgreSQL não estende UNIQUE entre conta, usuarios e admin.
        # ONLY evita que a consulta de conta inclua os próprios usuarios.
        parent = "ONLY conta" if self.connection.dialect.name == "postgresql" else "conta"
        for table in ("admin", parent):
            if self.connection.execute(text(
                f"SELECT 1 FROM {table} WHERE lower(trim(email)) = :email OR firebase_uid = :uid LIMIT 1"
            ), params).first():
                raise TargetIdentityError("Identidade já utilizada por administrador ou conta independente.")
        query = """
            SELECT id_usuario, cpf, email, firebase_uid, cargo_id, unidade_id
            FROM usuarios WHERE cpf = :cpf OR lower(trim(email)) = :email
                OR firebase_uid = :uid OR id_usuario = :linked_id
        """
        if self.connection.dialect.name == "postgresql":
            query += " FOR UPDATE"
        rows = self.connection.execute(text(query), params).mappings().all()
        if len(rows) > 1:
            raise TargetIdentityError("CPF, e-mail ou UID apontam para usuários diferentes no destino.")
        row = rows[0] if rows else None
        if link and (row is None or row["id_usuario"] != link["target_id"]
                     or row["firebase_uid"] != link["firebase_uid"]):
            raise TargetIdentityError("O vínculo legado/destino diverge do cadastro atual.")
        if row:
            if row["cpf"] != user.usuario.cpf:
                raise TargetIdentityError("CPF diverge da identidade localizada. Revisão necessária.")
            if not row["firebase_uid"] or (uid is not None and row["firebase_uid"] != uid):
                raise TargetIdentityError("UID diverge da identidade localizada no destino.")
            other = self.connection.execute(select(user_links.c.legacy_id).where(
                user_links.c.target_id == row["id_usuario"],
                (user_links.c.legacy_id != user.legacy_id) |
                (user_links.c.integration_key != INTEGRATION_KEY),
            )).first()
            if other:
                raise TargetIdentityError("Usuário destino já vinculado a outro registro de origem.")
            workspaces = self.connection.execute(text("""
                SELECT wc.cnpj AS cargo_cnpj, wu.cnpj AS unidade_cnpj
                FROM cargos c JOIN workspaces wc ON wc.id_workspace = c.workspace_id
                JOIN unidades u ON u.id_unidade = :unit_id
                JOIN workspaces wu ON wu.id_workspace = u.workspace_id
                WHERE c.id_cargo = :job_id
            """), {"unit_id": row["unidade_id"], "job_id": row["cargo_id"]}).mappings().one_or_none()
            if workspaces is None or any(value != user.workspace.cnpj for value in workspaces.values()):
                raise TargetIdentityError("Mudança de workspace ou vínculo organizacional inconsistente exige revisão.")
        return row

    def resolve_identity(self, user: PreparedUser) -> str | None:
        row = self._identity(user)
        return row["firebase_uid"] if row else None

    def persist(self, user: PreparedUser, uid: str) -> None:
        row = self._identity(user, uid)
        # Vincular pelo CNPJ, preservando id_workspace e atualizando nome da origem.
        workspace_id = self.connection.execute(text("""
            INSERT INTO workspaces (nome, cnpj) VALUES (:nome, :cnpj)
            ON CONFLICT (cnpj) DO UPDATE SET nome = EXCLUDED.nome
            RETURNING id_workspace
        """), user.workspace_payload()).scalar_one()
        job_id = self._organization("cargos", "id_cargo", user.job_insert_payload(workspace_id=workspace_id))
        unit_id = self._organization("unidades", "id_unidade", user.unit_insert_payload(workspace_id=workspace_id))
        payload = user.target_insert_payload(firebase_uid=uid, cargo_id=job_id, unidade_id=unit_id)
        if row is None:
            target_id = self.connection.execute(text("""
                INSERT INTO usuarios (nome, email, cpf, firebase_uid, cargo_id, unidade_id, tipo, status)
                VALUES (:nome, :email, :cpf, :firebase_uid, :cargo_id, :unidade_id, :tipo, :status)
                RETURNING id_usuario
            """), payload).scalar_one()
        else:
            target_id = row["id_usuario"]
            updates = user.target_update_payload(firebase_uid=uid, cargo_id=job_id, unidade_id=unit_id)
            self.connection.execute(text("""
                UPDATE usuarios SET nome=:nome, email=:email, cpf=:cpf,
                    firebase_uid=:firebase_uid, cargo_id=:cargo_id, unidade_id=:unidade_id
                WHERE id_usuario=:id
            """), updates | {"id": target_id})
        if not self.connection.execute(select(user_links.c.target_id).where(
            user_links.c.integration_key == INTEGRATION_KEY,
            user_links.c.legacy_id == user.legacy_id,
        )).first():
            self.connection.execute(user_links.insert().values(
                integration_key=INTEGRATION_KEY, legacy_id=user.legacy_id,
                target_id=target_id, firebase_uid=uid,
            ))

    def _organization(self, table: str, id_column: str, payload: dict) -> int:
        # Identificadores são constantes internas, nunca conteúdo do legado.
        self.connection.execute(text(f"""
            INSERT INTO {table} (workspace_id, nome, ativo) VALUES (:workspace_id, :nome, :ativo)
            ON CONFLICT (workspace_id, nome) DO NOTHING
        """), payload)
        return self.connection.execute(text(f"""
            SELECT {id_column} FROM {table} WHERE workspace_id=:workspace_id AND nome=:nome
        """), payload).scalar_one()
