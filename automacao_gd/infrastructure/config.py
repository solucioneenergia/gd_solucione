from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_ROOT  # compatibilidade com a API anterior
_USER_PROFILE = Path(os.environ.get("USERPROFILE", Path.home()))
_DEFAULT_PLANILHA = _USER_PROFILE / "Documents" / "planilha_gd.xlsx"
_DEFAULT_CLIENTES_ROOT = _USER_PROFILE / "Documents" / "Clientes"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    APP_ENV: str = Field(default="development")
    LOG_LEVEL: str = Field(default="WARNING")
    PORTAL_GD_URL: str = Field(
        default="https://gdneoenergiapernambuco.neoenergia.com/"
    )
    PLANILHA_PATH: Path = Field(default=_DEFAULT_PLANILHA)
    CLIENTES_ROOT: Path = Field(default=_DEFAULT_CLIENTES_ROOT)
    DOWNLOADS_DIR: Path = Field(default=Path("data/downloads"))
    LOGS_DIR: Path = Field(default=Path("data/logs"))
    AUTH_STATE_PATH: Path = Field(default=Path("data/auth/storage_state.json"))
    HEADLESS: bool = Field(default=False)
    DRY_RUN: bool = Field(default=True)
    BACKUP_EXCEL: bool = Field(default=True)
    CREATE_YEAR_SHEET_IF_MISSING: bool = Field(default=True)
    EXCEL_DEFAULT_TEMPLATE_SHEET: str = Field(default="2025")
    MOVE_WRONG_YEAR_ROWS: bool = Field(default=True)
    USE_PERSISTENT_CONTEXT: bool = Field(default=False)
    BROWSER_PROFILE_DIR: Path = Field(default=Path("data/browser_profile_edge"))
    BROWSER_CHANNEL: str | None = Field(default="msedge")
    CDP_MODE: bool = Field(default=True)
    CDP_ENDPOINT: str = Field(default="http://127.0.0.1:9222")
    ALLOW_REMOTE_CDP: bool = Field(default=False)
    MAX_COMPLETED_TO_PROCESS: int = Field(default=5, ge=0)
    OPTION5_AUTHORIZED_MAX_PROTOCOLS: int = Field(default=5, ge=1, le=60)
    ENABLE_PORTAL_PAGINATION: bool = Field(default=False)
    MAX_PORTAL_PAGES: int = Field(default=1, ge=0)
    MAX_PROTOCOL_NOT_FOUND_ERRORS: int = Field(default=3, ge=1)
    REPROCESS_EXISTING_PDFS: bool = Field(default=False)
    PROCESS_EXISTING_AFTER_SKIP: bool = Field(default=True)
    APPLY_EXCEL: bool = Field(default=True)
    APPLY_ARCHIVE: bool = Field(default=True)
    SYNC_COMPLETION_STATUS: bool = Field(default=False)
    APPLY_COMPLETION_STATUS: bool = Field(default=False)
    MAX_COMPLETION_PROTOCOLS_PER_RUN: int = Field(default=5, ge=0)
    RESUME_PIPELINE: bool = Field(default=True)
    SKIP_ALREADY_COMPLETED: bool = Field(default=True)
    CACHE_CLIENT_FOLDER_LOOKUP: bool = Field(default=True)
    CLIENT_FOLDER_CACHE_TTL_DAYS: int = Field(default=30, ge=1, le=3650)
    CLIENT_FOLDER_CACHE_MAX_ENTRIES: int = Field(default=5000, ge=10, le=100000)
    FORCE_REPROCESS_PROTOCOLS: str = Field(default="")
    RESET_PIPELINE_STATE: bool = Field(default=False)
    WORKBOOK_REPAIR_MODE: str = Field(default="visual_only")
    STRICT_WORKBOOK_VALIDATION: bool = Field(default=False)
    ARCHIVE_FALLBACK_MODE: str = Field(default="pending")
    ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE: str = Field(default="latest")
    ARCHIVE_ALLOW_EXISTING_LEGACY_GD: bool = Field(default=False)
    FUZZY_AUTO_MATCH_SCORE: int = Field(default=90, ge=0, le=100)
    FUZZY_REVIEW_MATCH_SCORE: int = Field(default=75, ge=0, le=100)
    MAX_PDF_SIZE_MB: int = Field(default=25, ge=1, le=500)

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("PLANILHA_PATH", "CLIENTES_ROOT", mode="before")
    @classmethod
    def validate_default_paths(
        cls, value: str | Path | None, info: ValidationInfo
    ) -> Path:
        if value is None or str(value).strip() == "":
            return _DEFAULT_PLANILHA if info.field_name == "PLANILHA_PATH" else _DEFAULT_CLIENTES_ROOT
        return Path(value)

    @field_validator("APP_ENV", mode="before")
    @classmethod
    def validate_app_env(cls, value: str | None) -> str:
        normalized = str(value or "development").strip().lower()
        if normalized not in {"development", "test", "production"}:
            raise ValueError("APP_ENV deve ser development, test ou production.")
        return normalized

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def validate_log_level(cls, value: str | None) -> str:
        normalized = str(value or "WARNING").strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "LOG_LEVEL deve ser DEBUG, INFO, WARNING, ERROR ou CRITICAL."
            )
        return normalized

    @field_validator("PORTAL_GD_URL")
    @classmethod
    def validate_portal_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("PORTAL_GD_URL deve ser uma URL HTTPS válida.")
        return value

    @field_validator("CDP_ENDPOINT")
    @classmethod
    def validate_cdp_endpoint_format(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("CDP_ENDPOINT deve ser uma URL HTTP(S) válida.")
        return value

    @field_validator("BROWSER_CHANNEL", mode="before")
    @classmethod
    def validate_browser_channel(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized in {"", "none", "null"}:
            return None
        if normalized not in {"msedge", "chrome", "chromium"}:
            raise ValueError(
                "BROWSER_CHANNEL deve ser msedge, chrome, chromium ou vazio."
            )
        return normalized

    @field_validator("WORKBOOK_REPAIR_MODE", mode="before")
    @classmethod
    def validate_workbook_repair_mode(cls, value: str | None) -> str:
        normalized = str(value or "visual_only").strip().lower()
        if normalized not in {"visual_only", "full", "validate_only"}:
            raise ValueError(
                "WORKBOOK_REPAIR_MODE deve ser visual_only, full ou validate_only."
            )
        return normalized

    @field_validator("ARCHIVE_FALLBACK_MODE", mode="before")
    @classmethod
    def validate_archive_fallback_mode(cls, value: str | None) -> str:
        normalized = str(value or "pending").strip().lower()
        if normalized not in {"pending", "legacy_gd", "disabled"}:
            raise ValueError(
                "ARCHIVE_FALLBACK_MODE deve ser pending, legacy_gd ou disabled."
            )
        return normalized

    @field_validator("ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE", mode="before")
    @classmethod
    def validate_archive_entry_folder_when_multiple(cls, value: str | None) -> str:
        normalized = str(value or "latest").strip().lower()
        if normalized not in {"latest", "pending"}:
            raise ValueError(
                "ARCHIVE_ENTRY_FOLDER_WHEN_MULTIPLE deve ser latest ou pending."
            )
        return normalized

    @model_validator(mode="after")
    def validate_security_relationships(self) -> "Settings":
        endpoint_host = (urlparse(self.CDP_ENDPOINT).hostname or "").lower()
        if self.CDP_MODE and not self.ALLOW_REMOTE_CDP and endpoint_host not in _LOOPBACK_HOSTS:
            raise ValueError(
                "CDP remoto bloqueado. Use endpoint local ou defina "
                "ALLOW_REMOTE_CDP=true de forma consciente."
            )
        if self.FUZZY_REVIEW_MATCH_SCORE > self.FUZZY_AUTO_MATCH_SCORE:
            raise ValueError(
                "FUZZY_REVIEW_MATCH_SCORE não pode ser maior que FUZZY_AUTO_MATCH_SCORE."
            )
        if self.APP_ENV == "production" and not self.DRY_RUN and not self.BACKUP_EXCEL:
            raise ValueError("BACKUP_EXCEL deve permanecer habilitado em produção.")
        return self

    def resolve_path(self, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def clientes_root_path(self) -> Path:
        return self.resolve_path(self.CLIENTES_ROOT)

    @property
    def planilha_path(self) -> Path:
        return self.resolve_path(self.PLANILHA_PATH)

    @property
    def downloads_dir_path(self) -> Path:
        return self.resolve_path(self.DOWNLOADS_DIR)

    @property
    def logs_dir_path(self) -> Path:
        return self.resolve_path(self.LOGS_DIR)

    @property
    def auth_state_path(self) -> Path:
        return self.resolve_path(self.AUTH_STATE_PATH)

    @property
    def browser_profile_dir_path(self) -> Path:
        return self.resolve_path(self.BROWSER_PROFILE_DIR)

    @property
    def force_reprocess_protocols(self) -> set[str]:
        return {item.strip() for item in self.FORCE_REPROCESS_PROTOCOLS.split(",") if item.strip()}

    @property
    def pipeline_state_path(self) -> Path:
        return self.resolve_path(Path("data/state/pipeline_cdp_state.json"))

    @property
    def real_run_execution_lock_path(self) -> Path:
        return self.resolve_path(Path("data/locks/real_run_execution.lock"))

    @property
    def option5_execution_lock_path(self) -> Path:
        return self.real_run_execution_lock_path

    @property
    def client_folder_cache_path(self) -> Path:
        return self.resolve_path(Path("data/cache/client_folder_cache.json"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
