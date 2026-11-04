import os
import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

Mode = Literal["observe", "dry-run", "live", "off"]


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    user_id: str
    token_file: str
    enabled: bool = True
    task_id: str | None = None  # Ancienne habitude v0.1, conservée mais plus utilisée pour les scores.

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", value):
            raise ValueError("Nom de compte invalide")
        return value

    @field_validator("user_id", "task_id")
    @classmethod
    def valid_uuid(cls, value):
        if value is not None:
            return str(UUID(value))
        return value

    def token(self) -> str:
        try:
            value = Path(self.token_file).read_text().strip()
        except OSError:
            raise ValueError("Fichier de clé API inaccessible") from None
        if not value or "\n" in value:
            raise ValueError("Clé API absente ou invalide")
        return value


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    database: str = "/data/hpm.sqlite3"
    initial_mode: Mode = "observe"
    admin_token: SecretStr
    webhook_token: SecretStr
    master: Account
    companions: list[Account]
    technical_alias: str = "hpm-progression-kaell"
    technical_title: str = "⚙️ Progression Kaell"
    habit_priority: float = Field(
        default=1, ge=0.1, le=2
    )  # Compatibilité TOML v0.1 ; routage par priorité source.
    habit_reset: Literal["daily", "weekly", "monthly"] = "daily"
    request_timeout: float = Field(default=10, ge=1, le=60)
    worker_interval: float = Field(default=0.5, ge=0.1, le=60)
    max_attempts: int = Field(default=6, ge=1, le=20)
    body_limit: int = Field(default=1048576, ge=1024, le=8388608)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("base_url doit être l'origine HTTP(S), sans chemin ni identifiants")
        return value.rstrip("/")

    @field_validator("habit_priority")
    @classmethod
    def valid_priority(cls, value):
        if value not in {0.1, 1, 1.5, 2}:
            raise ValueError("Difficulté Habitica : 0.1, 1, 1.5 ou 2")
        return value

    @field_validator("technical_alias")
    @classmethod
    def valid_alias(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("Alias technique invalide")
        return value

    @model_validator(mode="after")
    def unique_accounts_and_secrets(self):
        accounts = [self.master, *self.companions]
        if len({x.name for x in accounts}) != len(accounts):
            raise ValueError("Noms de comptes dupliqués")
        if len({x.user_id for x in accounts}) != len(accounts):
            raise ValueError("Comptes dupliqués ou compte maître utilisé comme compagnon")
        for secret in (self.admin_token, self.webhook_token):
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret.get_secret_value()):
                raise ValueError("Jetons admin et webhook : 32 caractères aléatoires minimum, URL-safe")
        if self.admin_token == self.webhook_token:
            raise ValueError("Utiliser deux jetons distincts")
        return self


def load_settings() -> Settings:
    path = os.environ.get("HPM_CONFIG", "/config/config.toml")
    with open(path, "rb") as stream:
        data = tomllib.load(stream)
    for key in ("admin_token", "webhook_token"):
        env_name = "HPM_" + key.upper()
        file = os.environ.get(env_name + "_FILE", "/run/secrets/" + key)
        try:
            data[key] = os.environ.get(env_name) or Path(file).read_text().strip()
        except OSError:
            raise ValueError(f"Secret {env_name}_FILE inaccessible") from None
    try:
        return Settings.model_validate(data)
    except ValidationError as error:
        fields = [".".join(str(part) for part in item["loc"]) or "configuration" for item in error.errors()]
        raise ValueError("Configuration HPM invalide : " + ", ".join(fields)) from None
