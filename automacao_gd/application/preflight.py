"""Validações de prontidão antes de operações destrutivas ou externas."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from automacao_gd.domain.errors import PreflightBlockedError
from automacao_gd.infrastructure.config import Settings, get_settings
from automacao_gd.infrastructure.excel.availability import (
    validate_workbook_availability,
    windows_drive_root,
)

_CDP_PROBE_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class CheckResult:
    code: str
    ok: bool
    message: str
    blocking: bool = True
    technical_cause: str | None = None
    stage: str = "pré-voo"


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[CheckResult, ...]

    @property
    def ready(self) -> bool:
        return all(check.ok or not check.blocking for check in self.checks)

    @property
    def blocking_errors(self) -> list[str]:
        return [check.message for check in self.checks if check.blocking and not check.ok]

    @property
    def first_blocking_check(self) -> CheckResult | None:
        return next(
            (check for check in self.checks if check.blocking and not check.ok),
            None,
        )

    def raise_if_blocked(self) -> None:
        check = self.first_blocking_check
        if check is None:
            return
        raise PreflightBlockedError(
            code=check.code,
            user_message=check.message,
            technical_cause=check.technical_cause,
            stage=check.stage,
        )

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "blocking_errors": self.blocking_errors,
            "checks": [asdict(check) for check in self.checks],
        }


def run_preflight(
    settings: Settings | None = None,
    *,
    real_run: bool = False,
    require_cdp: bool = False,
) -> PreflightReport:
    settings = settings or get_settings()
    checks: list[CheckResult] = []

    if real_run and settings.APP_ENV != "production":
        checks.append(
            CheckResult(
                "ENVIRONMENT_NOT_PRODUCTION",
                False,
                "Execução real bloqueada: DRY_RUN=false exige APP_ENV=production.",
            )
        )
    else:
        checks.append(
            CheckResult(
                "environment",
                True,
                "Ambiente compatível com o modo solicitado.",
                blocking=real_run,
            )
        )

    checks.append(_directory_check("downloads_dir", settings.downloads_dir_path, create=True))
    checks.append(_directory_check("logs_dir", settings.logs_dir_path, create=True))

    if settings.APPLY_EXCEL:
        checks.append(_workbook_check(settings.planilha_path, require_writable=real_run))
    else:
        checks.append(CheckResult("workbook", True, "Atualização de Excel desabilitada.", False))

    if settings.APPLY_ARCHIVE:
        checks.append(_directory_check(
            "clientes_root",
            settings.clientes_root_path,
            create=False,
            require_writable=real_run,
        ))
    else:
        checks.append(CheckResult("clientes_root", True, "Arquivamento desabilitado.", False))

    parsed = urlparse(settings.CDP_ENDPOINT)
    local_endpoint = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    endpoint_allowed = (not require_cdp) or local_endpoint or settings.ALLOW_REMOTE_CDP
    checks.append(
        CheckResult(
            "cdp_endpoint",
            endpoint_allowed,
            "Endpoint CDP local e seguro."
            if local_endpoint
            else "Endpoint CDP remoto exige ALLOW_REMOTE_CDP=true.",
            blocking=require_cdp,
        )
    )
    if require_cdp and endpoint_allowed:
        checks.append(_cdp_connection_check(settings.CDP_ENDPOINT))

    if real_run:
        checks.append(
            CheckResult(
                "excel_backup",
                (not settings.APPLY_EXCEL) or settings.BACKUP_EXCEL,
                "Backup da planilha habilitado."
                if settings.BACKUP_EXCEL
                else "Execução real exige BACKUP_EXCEL=true.",
            )
        )

    dedicated_auth_dir = settings.auth_state_path.parent.name.lower() in {"auth", "session", "sessions"}
    checks.append(
        CheckResult(
            "auth_state_directory",
            dedicated_auth_dir,
            "Estado de autenticação usa diretório dedicado e ignorado pelo Git."
            if dedicated_auth_dir
            else "Use um diretório dedicado, como data/auth, para AUTH_STATE_PATH.",
            blocking=False,
        )
    )
    return PreflightReport(tuple(checks))


def _cdp_connection_check(endpoint: str) -> CheckResult:
    try:
        with urlopen(  # noqa: S310 - endpoint is validated by cdp_endpoint preflight check.
            _cdp_version_url(endpoint),
            timeout=_CDP_PROBE_TIMEOUT_SECONDS,
        ) as response:
            status = response.getcode()
            body = response.read(4096).decode("utf-8", errors="replace")
    except (OSError, TimeoutError, URLError, ValueError) as exc:
        return CheckResult(
            "cdp_connection",
            False,
            "Conexão CDP indisponível. Abra o Edge com CDP e tente novamente.",
            technical_cause=type(exc).__name__,
            stage="pré-voo",
        )

    if status == 200 and ("webSocketDebuggerUrl" in body or '"Browser"' in body):
        return CheckResult(
            "cdp_connection",
            True,
            "Conexão CDP respondendo.",
            stage="pré-voo",
        )
    return CheckResult(
        "cdp_connection",
        False,
        "Conexão CDP indisponível. Abra o Edge com CDP e tente novamente.",
        technical_cause=f"HTTP_{status}_INVALID_CDP_VERSION",
        stage="pré-voo",
    )


def _cdp_version_url(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    current_path = parsed.path.rstrip("/")
    if current_path == "/json/version":
        probe_path = parsed.path
    else:
        probe_path = f"{current_path}/json/version" if current_path else "/json/version"
    return parsed._replace(path=probe_path, params="", query="", fragment="").geturl()


def _workbook_check(path: Path, *, require_writable: bool) -> CheckResult:
    result = validate_workbook_availability(
        path,
        require_writable=require_writable,
        stage="pré-voo",
    )
    return CheckResult(
        "workbook" if result.ok else result.code.value,
        result.ok,
        result.user_message,
        technical_cause=result.technical_cause,
        stage=result.stage,
    )


def _directory_check(
    code: str,
    path: Path,
    *,
    create: bool,
    require_writable: bool = False,
) -> CheckResult:
    path = Path(path)
    drive_root = windows_drive_root(path)
    if drive_root:
        try:
            drive_available = os.path.exists(drive_root) and os.path.isdir(drive_root)
        except OSError as exc:
            return CheckResult(
                "NETWORK_DRIVE_UNAVAILABLE",
                False,
                f"Unidade de rede indisponível ou desconectada: {drive_root}",
                technical_cause=f"type={type(exc).__name__}; errno={exc.errno}; winerror={getattr(exc, 'winerror', None)}",
            )
        if not drive_available:
            return CheckResult(
                "NETWORK_DRIVE_UNAVAILABLE",
                False,
                f"Unidade de rede indisponível ou desconectada: {drive_root}",
            )
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(code, False, f"Não foi possível preparar {path}: {exc}")
    if not path.exists() or not path.is_dir():
        return CheckResult(code, False, f"Diretório não encontrado: {path}")
    if require_writable and not os.access(path, os.W_OK):
        return CheckResult(code, False, f"Diretório sem permissão de escrita: {path}")
    return CheckResult(code, True, f"Diretório disponível: {path}")
