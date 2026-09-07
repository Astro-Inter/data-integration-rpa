"""Mapeamento do legado para workspace, unidade, cargo e usuário."""

from pydantic import ValidationError

from app.models.legacy_user import LegacyUser
from app.models.prepared_user import PreparedUser, normalize_email


class UserDataError(ValueError):
    def __init__(self, fields: tuple[str, ...], *, legacy_id=None, empresa_id=None, details=None):
        self.fields = fields
        self.legacy_id, self.empresa_id = legacy_id, empresa_id
        self.details = details or tuple(f"{field}: valor inválido" for field in fields)
        super().__init__("Dados inválidos nos campos: " + ", ".join(fields))


REASONS = {
    "documento_obrigatorio": "documento ausente ou não recebido como texto",
    "documento_caracteres_invalidos": "documento contém caracteres não aceitos",
    "documento_tamanho_invalido": "quantidade de dígitos incorreta (CPF: 11; CNPJ: 14)",
    "documento_digitos_repetidos": "documento com todos os dígitos iguais",
    "documento_verificadores_invalidos": "dígitos verificadores inválidos",
    "texto_obrigatorio": "texto obrigatório ausente ou de tipo inválido",
    "caractere_invalido": "texto contém caractere de controle não aceito",
    "email_invalido": "formato de e-mail inválido",
    "string_too_short": "texto menor que o tamanho mínimo permitido",
    "string_too_long": "texto maior que o tamanho máximo permitido",
    "email.formato": "há endereço com formato inválido",
    "email.obrigatorio": "nenhum e-mail utilizável encontrado",
    "email.ambiguo": "mais de um e-mail distinto encontrado",
    "departamento.empresa": "departamento ausente ou vinculado a outra empresa",
}


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
        fields = tuple(sorted(set(errors)))
        raise UserDataError(fields, legacy_id=user.id_funcionario, empresa_id=user.id_empresa,
                            details=tuple(f"{field}: {REASONS[field]}" for field in fields))
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
        details = []
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"])
            # Só envia a descrição de códigos reconhecidos, nunca a mensagem bruta.
            token = str(error.get("ctx", {}).get("error", error["type"]))
            details.append(f"{field}: {REASONS.get(token, 'valor ausente, inválido ou incompatível com o contrato')}")
        raise UserDataError(fields, legacy_id=user.id_funcionario, empresa_id=user.id_empresa,
                            details=tuple(details)) from None
