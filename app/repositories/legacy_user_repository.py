"""Leitura paginada de funcionários e suas referências no banco legado."""

from collections.abc import Iterator

from sqlalchemy import Connection, bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.models.legacy_user import LegacyEmail, LegacyUser


class LegacyReadError(RuntimeError):
    """Falha de consulta que pode ser exibida sem dados pessoais ou credenciais."""


USER_COLUMNS = """
    SELECT f.id_funcionario, f.cpf, f.nome, f.cargo, f.id_empresa,
           e.nome AS empresa_nome, e.cnpj AS empresa_cnpj,
           f.id_departamento, d.nome AS departamento_nome,
           d.id_empresa AS departamento_id_empresa
    FROM funcionario AS f
    LEFT JOIN empresa AS e ON e.id_empresa = f.id_empresa
    LEFT JOIN departamento AS d ON d.id_departamento = f.id_departamento
"""

FIRST_PAGE = text(USER_COLUMNS + """
    WHERE f.id_funcionario <= :upper_id
    ORDER BY f.id_funcionario
    LIMIT :batch_size
""")

NEXT_PAGE = text(USER_COLUMNS + """
    WHERE f.id_funcionario > :last_id AND f.id_funcionario <= :upper_id
    ORDER BY f.id_funcionario
    LIMIT :batch_size
""")

EMAILS = text("""
    SELECT id_funcionario, id_email, email
    FROM email
    WHERE id_funcionario IN :user_ids
    ORDER BY id_funcionario, id_email
""").bindparams(bindparam("user_ids", expanding=True))


class LegacyUserRepository:
    def __init__(self, connection: Connection):
        # O chamador mantém e encerra a conexão, inclusive ao interromper a leitura.
        self.connection = connection

    def iter_batches(self, batch_size: int) -> Iterator[list[LegacyUser]]:
        """Varre os IDs existentes até o limite inicial, sem checkpoint persistido."""
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("O tamanho do lote deve ser um inteiro positivo.")

        try:
            upper_id = self.connection.execute(
                text("SELECT MAX(id_funcionario) FROM funcionario")
            ).scalar_one()
            if upper_id is None:
                return
            last_id = None
            while True:
                parameters = {"upper_id": upper_id, "batch_size": batch_size}
                query = FIRST_PAGE
                if last_id is not None:
                    parameters["last_id"] = last_id
                    query = NEXT_PAGE
                rows = self.connection.execute(query, parameters).mappings().all()
                if not rows:
                    return

                emails: dict[int, list[LegacyEmail]] = {
                    row["id_funcionario"]: [] for row in rows
                }
                email_rows = self.connection.execute(
                    EMAILS, {"user_ids": list(emails)}
                ).mappings()
                for row in email_rows:
                    emails[row["id_funcionario"]].append(
                        LegacyEmail(id_email=row["id_email"], email=row["email"])
                    )
                batch = [
                    LegacyUser(**row, emails=tuple(emails[row["id_funcionario"]]))
                    for row in rows
                ]
                last_id = rows[-1]["id_funcionario"]
                yield batch
                if len(rows) < batch_size or last_id == upper_id:
                    return
        except SQLAlchemyError:
            raise LegacyReadError(
                "Falha ao consultar funcionários no legado. Verifique acesso e estrutura das tabelas."
            ) from None
