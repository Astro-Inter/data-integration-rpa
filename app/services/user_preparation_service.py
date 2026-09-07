"""Mapeamento do legado para workspace, unidade, cargo e usuário."""

from pydantic import ValidationError

from app.models.legacy_user import LegacyUser
from app.models.prepared_user import PreparedUser, normalize_email


class UserDataError(ValueError):
    def __init__(self, fields: tuple[str, ...]):
        self.fields = fields
        super().__init__("Dados inválidos nos campos: " + ", ".join(fields))


def prepare_user(user: LegacyUser) -> PreparedUser:
    errors = []
    if user.departamento_id_empresa is None or user.departamento_id_empresa != user.id_empresa:
        errors.append("departamento.empresa")
    emails = set()
    for item in user.emails:
        if item.email is None or (isinstance(item.email, str) and not item.email.strip()):
            continue
        try:
            emails.add(normalize_email(item.email))
        except ValueError:
            errors.append("email.formato")
    if not emails:
        errors.append("email.obrigatorio")
    elif len(emails) > 1:
        errors.append("email.ambiguo")
    if errors:
        raise UserDataError(tuple(sorted(set(errors))))
    try:
        return PreparedUser(
            legacy_id=user.id_funcionario,
            workspace={"legacy_empresa_id": user.id_empresa, "nome": user.empresa_nome,
                       "cnpj": user.empresa_cnpj},
            unidade={"legacy_departamento_id": user.id_departamento, "nome": user.departamento_nome},
            cargo={"nome": user.cargo},
            usuario={"nome": user.nome, "cpf": user.cpf, "email": next(iter(emails))},
        )
    except ValidationError as exc:
        fields = tuple(sorted({".".join(str(part) for part in error["loc"])
                               for error in exc.errors(include_input=False, include_url=False)}))
        raise UserDataError(fields) from None
