"""Settings from environment variables (prefix HUB_). Secrets are read from files (`*_FILE`), never from the
command line, so they do not show up in process listings or shell history."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret(path: Path | None) -> SecretStr | None:
    """The first line of a secret file, or None when no file is configured."""
    if path is None:
        return None
    if not path.is_file():
        raise ValueError(f"secret file {path} does not exist; run `make bootstrap` first")
    return SecretStr(path.read_text(encoding="utf-8").strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", env_file=".env", extra="ignore")

    database_url: SecretStr = Field(default=SecretStr("postgresql://hub:hub@127.0.0.1:55432/hub"))
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # which adapters are live
    crm: Literal["espocrm", "salesforce"] = "espocrm"
    tickets: Literal["zammad", "github", "jira", "none"] = "zammad"

    odoo_url: str = "http://127.0.0.1:8069"
    odoo_database: str = "odoo"
    odoo_api_key_file: Path | None = Path(".secrets/odoo_api_key")

    espocrm_url: str = "http://127.0.0.1:8080"
    espocrm_api_key_file: Path | None = Path(".secrets/espocrm_api_key")
    espocrm_webhook_secret_file: Path | None = Path(".secrets/espocrm_webhook_secret")

    salesforce_instance_url: str | None = None
    salesforce_client_id: str | None = None
    salesforce_client_secret_file: Path | None = None
    salesforce_api_version: str = "v66.0"

    zammad_url: str = "http://127.0.0.1:8081"
    zammad_token_file: Path | None = Path(".secrets/zammad_token")
    zammad_group: str = "Finance Ops"

    jira_url: str | None = None
    jira_email: str | None = None
    jira_token_file: Path | None = None
    jira_project: str = "FIN"

    github_repo: str | None = None  # owner/name; the token comes from GITHUB_TOKEN

    api_key: SecretStr = Field(default=SecretStr(""), description="the key callers send as X-API-Key")
    rate_limit: str = "120/minute"

    ollama_url: str = "http://127.0.0.1:11434"
    triage_model: str = "qwen2.5:7b"
    embed_model: str = "nomic-embed-text"
    langfuse_url: str = "http://127.0.0.1:3000"
    refund_approval_threshold: float = 500.0

    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025

    # sync tuning
    sync_page_size: int = Field(default=100, ge=1, le=200)
    sync_workers: int = Field(default=4, ge=1, le=16, description="accounts processed at once (ADR 0003)")
    sync_overlap_seconds: int = Field(default=300, ge=0, description="re-read window behind the cursor")
    http_timeout_seconds: float = Field(default=20.0, gt=0)
    http_retries: int = Field(default=4, ge=0, le=10)

    @field_validator("odoo_database")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"not a plain database name: {value!r}")
        return value

    def odoo_api_key(self) -> SecretStr:
        key = read_secret(self.odoo_api_key_file)
        if key is None:
            raise ValueError("HUB_ODOO_API_KEY_FILE is not set")
        return key

    def espocrm_api_key(self) -> SecretStr:
        key = read_secret(self.espocrm_api_key_file)
        if key is None:
            raise ValueError("HUB_ESPOCRM_API_KEY_FILE is not set")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
