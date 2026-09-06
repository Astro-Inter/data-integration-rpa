"""Dados brutos do legado; regras de validação e transformação virão depois."""

from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class LegacyEmail:
    id_email: int
    email: str | None


@dataclass(frozen=True, repr=False)
class LegacyUser:
    id_funcionario: int
    cpf: str
    nome: str
    cargo: str
    id_empresa: int
    empresa_nome: str | None
    empresa_cnpj: str | None
    id_departamento: int
    departamento_nome: str | None
    departamento_id_empresa: int | None
    emails: tuple[LegacyEmail, ...]
