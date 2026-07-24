"""Validação não destrutiva da disponibilidade de planilhas XLSX."""

from __future__ import annotations

import errno
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


class WorkbookAvailabilityCode(str, Enum):
    AVAILABLE = "WORKBOOK_AVAILABLE"
    NETWORK_DRIVE_UNAVAILABLE = "NETWORK_DRIVE_UNAVAILABLE"
    WORKBOOK_NOT_FOUND = "WORKBOOK_NOT_FOUND"
    WORKBOOK_LOCKED = "WORKBOOK_LOCKED"
    WORKBOOK_PERMISSION_DENIED = "WORKBOOK_PERMISSION_DENIED"
    WORKBOOK_TEMPORARILY_UNAVAILABLE = "WORKBOOK_TEMPORARILY_UNAVAILABLE"
    WORKBOOK_OPEN_FAILED = "WORKBOOK_OPEN_FAILED"
    WORKBOOK_INVALID = "WORKBOOK_INVALID"
    WORKBOOK_TEMP_CREATE_FAILED = "WORKBOOK_TEMP_CREATE_FAILED"
    WORKBOOK_ATOMIC_REPLACE_FAILED = "WORKBOOK_ATOMIC_REPLACE_FAILED"


@dataclass(frozen=True, slots=True)
class WorkbookAvailabilityResult:
    ok: bool
    code: WorkbookAvailabilityCode
    user_message: str
    technical_cause: str | None = None
    stage: str = "pré-voo"


_DRIVE_PATH = re.compile(r"^([A-Za-z]:)[\\/]")
_NETWORK_WINERRORS = {53, 64, 67, 121, 1231}
_LOCK_WINERRORS = {32, 33}
_TEMPORARY_ERRNOS = {
    errno.EBUSY,
    errno.EIO,
    getattr(errno, "ENETDOWN", 100),
    getattr(errno, "ENETRESET", 102),
    getattr(errno, "ENETUNREACH", 101),
    getattr(errno, "ETIMEDOUT", 110),
}


def windows_drive_root(path: str | Path) -> str | None:
    match = _DRIVE_PATH.match(str(path))
    return f"{match.group(1).upper()}\\" if match else None


def validate_workbook_availability(
    workbook_path: str | Path,
    *,
    require_writable: bool = False,
    stage: str = "pré-voo",
) -> WorkbookAvailabilityResult:
    path = Path(workbook_path)
    drive_root = windows_drive_root(workbook_path)
    if drive_root:
        drive_error = _validate_drive_root(drive_root, stage)
        if drive_error is not None:
            return drive_error

    parent = path.parent
    try:
        if not parent.exists() or not parent.is_dir():
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_TEMPORARILY_UNAVAILABLE,
                "A planilha ou o diretório está temporariamente indisponível. "
                "Tente novamente após restabelecer o acesso.",
                stage,
                f"Diretório pai indisponível: {parent}",
            )
    except OSError as exc:
        return _classify_os_error(exc, operation="parent", stage=stage)

    try:
        if not path.exists() or not path.is_file():
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_NOT_FOUND,
                "Planilha não encontrada no caminho configurado.",
                stage,
                f"Arquivo inexistente: {path}",
            )
    except OSError as exc:
        return _classify_os_error(exc, operation="file", stage=stage)

    if path.suffix.lower() != ".xlsx":
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_INVALID,
            "A planilha configurada não é um arquivo .xlsx válido.",
            stage,
            f"Extensão inválida: {path.suffix}",
        )

    if not os.access(path, os.R_OK):
        return _permission_denied(stage, f"Sem permissão de leitura: {path}")

    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        return _classify_os_error(exc, operation="read", stage=stage)

    workbook = None
    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
    except (InvalidFileException, BadZipFile, KeyError, EOFError) as exc:
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_INVALID,
            "A planilha configurada está inválida ou corrompida.",
            stage,
            _technical(exc, "openpyxl-invalid"),
        )
    except OSError as exc:
        return _classify_os_error(exc, operation="openpyxl", stage=stage)
    except Exception as exc:
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_OPEN_FAILED,
            "Não foi possível abrir a planilha configurada com segurança.",
            stage,
            _technical(exc, "openpyxl-open"),
        )
    finally:
        if workbook is not None:
            workbook.close()

    if not require_writable:
        return WorkbookAvailabilityResult(
            True,
            WorkbookAvailabilityCode.AVAILABLE,
            "Planilha disponível.",
            stage=stage,
        )

    if not os.access(path, os.W_OK) or not os.access(parent, os.W_OK):
        return _permission_denied(stage, f"Sem permissão de escrita: {path}")

    try:
        with path.open("r+b"):
            pass
    except OSError as exc:
        return _classify_os_error(exc, operation="real-file-write", stage=stage)

    return _validate_disposable_atomic_replace(path, stage)


def classify_workbook_write_error(
    exc: OSError, *, stage: str = "gravação da planilha"
) -> WorkbookAvailabilityResult:
    """Classifica uma falha de acesso ao arquivo real sem alterar seu conteúdo."""
    return _classify_os_error(exc, operation="real-file-write", stage=stage)


def _validate_drive_root(
    drive_root: str, stage: str
) -> WorkbookAvailabilityResult | None:
    try:
        if not os.path.exists(drive_root) or not os.path.isdir(drive_root):
            return _failure(
                WorkbookAvailabilityCode.NETWORK_DRIVE_UNAVAILABLE,
                f"Unidade de rede indisponível ou desconectada: {drive_root}",
                stage,
                f"Raiz da unidade indisponível: {drive_root}",
            )
        if not os.access(drive_root, os.R_OK):
            return _failure(
                WorkbookAvailabilityCode.NETWORK_DRIVE_UNAVAILABLE,
                f"Unidade de rede indisponível ou desconectada: {drive_root}",
                stage,
                f"Raiz da unidade sem acesso: {drive_root}",
            )
    except OSError as exc:
        return _failure(
            WorkbookAvailabilityCode.NETWORK_DRIVE_UNAVAILABLE,
            f"Unidade de rede indisponível ou desconectada: {drive_root}",
            stage,
            _technical(exc, "drive-root"),
        )
    return None


def _validate_disposable_atomic_replace(
    path: Path, stage: str
) -> WorkbookAvailabilityResult:
    created: list[Path] = []
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        try:
            source_fd, source_name = tempfile.mkstemp(
                prefix=f".{path.name}.availability-source-", suffix=".tmp", dir=path.parent
            )
            source = Path(source_name)
            created.append(source)
            destination_fd, destination_name = tempfile.mkstemp(
                prefix=f".{path.name}.availability-destination-",
                suffix=".tmp",
                dir=path.parent,
            )
            destination = Path(destination_name)
            created.append(destination)
        except OSError as exc:
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_TEMP_CREATE_FAILED,
                "Não foi possível criar um arquivo temporário seguro ao lado da planilha.",
                stage,
                _technical(exc, "temp-create"),
            )

        try:
            with os.fdopen(source_fd, "wb") as handle:
                source_fd = None
                handle.write(b"availability-probe")
                handle.flush()
                os.fsync(handle.fileno())
            os.close(destination_fd)
            destination_fd = None
        except OSError as exc:
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_TEMP_CREATE_FAILED,
                "Não foi possível gravar e sincronizar um arquivo temporário seguro.",
                stage,
                _technical(exc, "temp-write-sync"),
            )

        try:
            os.replace(source, destination)
        except OSError as exc:
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_ATOMIC_REPLACE_FAILED,
                "O diretório da planilha não permite substituição atômica segura.",
                stage,
                _technical(exc, "atomic-replace-probe"),
            )
        return WorkbookAvailabilityResult(
            True,
            WorkbookAvailabilityCode.AVAILABLE,
            "Planilha disponível para gravação segura.",
            stage=stage,
        )
    finally:
        for fd in (source_fd, destination_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for temp_path in created:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _classify_os_error(
    exc: OSError, *, operation: str, stage: str
) -> WorkbookAvailabilityResult:
    winerror = getattr(exc, "winerror", None)
    if operation == "real-file-write" and winerror in _LOCK_WINERRORS:
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_LOCKED,
            "A planilha está aberta ou bloqueada pelo Excel. Feche o arquivo e execute novamente.",
            stage,
            _technical(exc, operation),
        )
    if winerror in _NETWORK_WINERRORS:
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_TEMPORARILY_UNAVAILABLE,
            "A planilha ou o diretório está temporariamente indisponível. "
            "Tente novamente após restabelecer o acesso.",
            stage,
            _technical(exc, operation),
        )
    if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
        if operation == "real-file-write" and winerror not in {5}:
            return _failure(
                WorkbookAvailabilityCode.WORKBOOK_LOCKED,
                "A planilha está aberta ou bloqueada pelo Excel. Feche o arquivo e execute novamente.",
                stage,
                _technical(exc, operation),
            )
        return _permission_denied(stage, _technical(exc, operation))
    if exc.errno in _TEMPORARY_ERRNOS:
        return _failure(
            WorkbookAvailabilityCode.WORKBOOK_TEMPORARILY_UNAVAILABLE,
            "A planilha ou o diretório está temporariamente indisponível. "
            "Tente novamente após restabelecer o acesso.",
            stage,
            _technical(exc, operation),
        )
    return _failure(
        WorkbookAvailabilityCode.WORKBOOK_OPEN_FAILED,
        "Não foi possível abrir a planilha configurada com segurança.",
        stage,
        _technical(exc, operation),
    )


def _permission_denied(stage: str, cause: str) -> WorkbookAvailabilityResult:
    return _failure(
        WorkbookAvailabilityCode.WORKBOOK_PERMISSION_DENIED,
        "Sem permissão para ler, gravar ou substituir a planilha configurada.",
        stage,
        cause,
    )


def _failure(
    code: WorkbookAvailabilityCode,
    message: str,
    stage: str,
    cause: str | None,
) -> WorkbookAvailabilityResult:
    return WorkbookAvailabilityResult(False, code, message, cause, stage)


def _technical(exc: BaseException, operation: str) -> str:
    return (
        f"operation={operation}; type={type(exc).__name__}; "
        f"errno={getattr(exc, 'errno', None)}; winerror={getattr(exc, 'winerror', None)}"
    )
