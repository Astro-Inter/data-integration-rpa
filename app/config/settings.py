"""Carrega e valida variáveis de ambiente sem expor credenciais."""

from typing import Literal

from pydantic import EmailStr, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )


class EmailSettings(EnvironmentSettings):
    email_alerts_enabled: bool = True
    smtp_host: str = Field(default="smtp.gmail.com", min_length=1)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: EmailStr = "app.4str0@gmail.com"
    smtp_password: SecretStr = SecretStr("")
    email_from: EmailStr = "app.4str0@gmail.com"
    email_to: EmailStr = "app.4str0@gmail.com"


class FirebaseSettings(EnvironmentSettings):
    firebase_project_id: str = Field(min_length=1)
    firebase_credentials_base64: SecretStr = Field(min_length=1)
    firestore_database_id: str = Field(default="(default)", min_length=1, pattern=r"^[^/]+$")


class Settings(FirebaseSettings, EmailSettings):
    firestore_logs_enabled: bool = True

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
