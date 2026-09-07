"""Contratos normalizados para as próximas etapas de criação e persistência."""

import re
import unicodedata
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt


def clean_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("texto_obrigatorio")
    value = unicodedata.normalize("NFC", value)
    if any(unicodedata.category(char).startswith("C") and not char.isspace() for char in value):
        raise ValueError("caractere_invalido")
    return " ".join(value.split())


def normalize_document(value: str, kind: str) -> str:
    if not isinstance(value, str):
        raise ValueError("documento_obrigatorio")
    # Aceita apenas dígitos ASCII e caracteres de formatação conhecidos.
    if re.fullmatch(r"[0-9. /\-\s]+", value) is None:
        raise ValueError("documento_invalido")
    digits = re.sub(r"[. /\-\s]", "", value)
    size = 11 if kind == "cpf" else 14
    if len(digits) != size or len(set(digits)) == 1:
        raise ValueError("documento_invalido")
    weights = ([10, 9, 8, 7, 6, 5, 4, 3, 2], [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]) if kind == "cpf" else (
        [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
    )
    for index, sequence in enumerate(weights):
        remainder = sum(int(digit) * weight for digit, weight in zip(digits, sequence)) % 11
        expected = 0 if remainder < 2 else 11 - remainder
        if int(digits[size - 2 + index]) != expected:
            raise ValueError("documento_invalido")
    return digits


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("email_obrigatorio")
    try:
        result = validate_email(
            value.strip().lower(), check_deliverability=False, allow_smtputf8=False,
        )
    except EmailNotValidError:
        raise ValueError("email_invalido") from None
    return result.ascii_email


Name = Annotated[str, BeforeValidator(clean_text), Field(min_length=2, max_length=255)]
Email = Annotated[str, BeforeValidator(normalize_email), Field(min_length=1, max_length=254)]
Cpf = Annotated[str, BeforeValidator(lambda value: normalize_document(value, "cpf"))]
Cnpj = Annotated[str, BeforeValidator(lambda value: normalize_document(value, "cnpj"))]


class PreparedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class WorkspaceData(PreparedModel):
    legacy_empresa_id: StrictInt
    nome: Name
    cnpj: Cnpj


class UnitData(PreparedModel):
    legacy_departamento_id: StrictInt
    nome: Name
    ativo: bool = True


class JobData(PreparedModel):
    nome: Name
    ativo: bool = True


class UserData(PreparedModel):
    nome: Name
    email: Email
    cpf: Cpf


class OrganizationPayload(PreparedModel):
    workspace_id: Annotated[StrictInt, Field(gt=0)]
    nome: Name
    ativo: bool = True


class TargetPolicy(PreparedModel):
    """Valores de criação conforme constraints fornecidas; não aplicar em updates."""
    tipo: Literal["FUNCIONARIO"] = "FUNCIONARIO"
    status: Literal["PRE_CADASTRADO"] = "PRE_CADASTRADO"
    modalidade: Annotated[str, BeforeValidator(clean_text), Field(min_length=2, max_length=50)] | None = None


class TargetUserPayload(UserData):
    firebase_uid: Annotated[str, Field(min_length=1, max_length=128)]
    cargo_id: Annotated[StrictInt, Field(gt=0)]
    unidade_id: Annotated[StrictInt, Field(gt=0)]
    tipo: Literal["FUNCIONARIO"]
    status: Literal["PRE_CADASTRADO"]
    modalidade: Annotated[str, Field(min_length=2, max_length=50)] | None = None


class PreparedUser(PreparedModel):
    legacy_id: StrictInt
    workspace: WorkspaceData
    unidade: UnitData
    cargo: JobData
    usuario: UserData

    def workspace_payload(self) -> dict:
        return self.workspace.model_dump(exclude={"legacy_empresa_id"})

    def unit_insert_payload(self, *, workspace_id: int) -> dict:
        return OrganizationPayload(workspace_id=workspace_id, nome=self.unidade.nome).model_dump()

    def job_insert_payload(self, *, workspace_id: int) -> dict:
        return OrganizationPayload(workspace_id=workspace_id, nome=self.cargo.nome).model_dump()

    def firebase_payload(self) -> dict:
        # Sem senha fictícia, UID fabricado ou declaração de e-mail verificado.
        return {"email": self.usuario.email, "display_name": self.usuario.nome,
                "email_verified": False}

    def target_insert_payload(
        self, *, firebase_uid: str, cargo_id: int, unidade_id: int,
    ) -> dict:
        return TargetUserPayload(
            **self.usuario.model_dump(), **TargetPolicy().model_dump(), firebase_uid=firebase_uid,
            cargo_id=cargo_id, unidade_id=unidade_id,
        ).model_dump(exclude_none=True)

    def target_update_payload(self, *, firebase_uid: str, cargo_id: int, unidade_id: int) -> dict:
        payload = self.target_insert_payload(
            firebase_uid=firebase_uid, cargo_id=cargo_id, unidade_id=unidade_id,
        )
        # Perfil, situação, modalidade e data de criação pertencem ao aplicativo.
        return {key: value for key, value in payload.items() if key not in ("tipo", "status")}
