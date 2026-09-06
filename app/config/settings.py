"""Carrega e valida variáveis de ambiente sem expor credenciais."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    firebase_project_id: str = Field(min_length=1)
    firebase_credentials_base64: SecretStr = Field(min_length=1)

    legacy_db_host: str = Field(min_length=1)
    legacy_db_port: int = Field(default=5432, ge=1, le=65535)
    legacy_db_name: str = Field(min_length=1)
    legacy_db_user: str = Field(min_length=1)
    legacy_db_password: SecretStr = Field(min_length=1)

    target_db_host: str = Field(min_length=1)
    target_db_port: int = Field(default=5432, ge=1, le=65535)
    target_db_name: str = Field(min_length=1)
    target_db_user: str = Field(min_length=1)
    target_db_password: SecretStr = Field(min_length=1)

    sync_batch_size: int = Field(default=100, gt=0)
    sync_dry_run: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()
