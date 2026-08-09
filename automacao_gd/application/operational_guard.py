from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from automacao_gd.domain.errors import OperationalBlockError
from automacao_gd.infrastructure.locking import ExecutionLock, ExecutionLockError


MAX_AUTHORIZED_PROTOCOLS = 5
OPTION4_OPERATION = "option4"
OPTION4_AUTHORIZATION_SCOPE = "CONTROLLED_PRODUCTION_OPTION4"
DIRECT_ROUTE_AUTHORIZATION_SCOPE = "CONTROLLED_DIRECT_ROUTE"
MIGRATION_OPERATION = "migrate_operational_data"
DOWNLOAD_CDP_OPERATION = "download_completed_budgets_cdp"
PROCESS_FIRST_CDP_OPERATION = "process_first_solicitation_cdp"
CONNECT_EXISTING_EDGE_OPERATION = "connect_existing_edge"
INSPECT_PORTAL_OPERATION = "inspect_portal_table"
_PROTOCOL_PATTERN = re.compile(r"(?<!\d)(\d{10})(?!\d)")


@dataclass(frozen=True)
class FrozenOfflinePdf:
    path: Path
    protocol: str
    fingerprint: str


@dataclass(frozen=True)
class FrozenOfflineBatch:
    requested_limit: int
    items: tuple[FrozenOfflinePdf, ...]
    digest: str

    @property
    def pdf_paths(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.items)

    @property
    def protocols(self) -> tuple[str, ...]:
        return tuple(item.protocol for item in self.items)


@dataclass(frozen=True)
class OfflineOperationAuthorization:
    batch: FrozenOfflineBatch
    confirmation: str
    execution_id: str


@dataclass(frozen=True)
class DirectRouteAuthorization:
    operation: str
    confirmation: str
    execution_id: str
    requested_limit: int | None = None


@dataclass(frozen=True)
class OperationalLockProof:
    lock: ExecutionLock
    path: Path
    execution_id: str
    operation: str
    requested_limit: int
    authorization_scope: str


def build_option4_strong_confirmation(protocol_limit: int) -> str:
    limit = _validated_limit(protocol_limit)
    return f"APLICAR OPCAO 4 EM {limit} PROTOCOLOS"


def prepare_offline_batch(
    downloads_root: Path,
    *,
    requested_limit: int,
    selections: Sequence[tuple[str | Path, str]] | None = None,
) -> FrozenOfflineBatch:
    limit = _validated_limit(requested_limit)
    if not selections:
        raise _scope_violation("A execução real exige seleção explícita de PDFs.")
    if len(selections) != limit:
        raise _scope_violation("A quantidade selecionada diverge do limite autorizado.")
    root = Path(downloads_root).resolve(strict=True)
    items: list[FrozenOfflinePdf] = []
    seen_paths: set[Path] = set()
    seen_protocols: set[str] = set()
    for raw_path, declared_protocol in selections:
        relative = Path(raw_path)
        protocol = str(declared_protocol).strip()
        if relative.is_absolute() or ".." in relative.parts:
            raise _scope_violation("O PDF selecionado deve ser relativo à pasta de downloads.")
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise _scope_violation("O PDF selecionado não pertence ao escopo autorizado.") from exc
        if not path.is_file() or path.suffix.casefold() != ".pdf":
            raise _scope_violation("O item selecionado não é um PDF válido.")
        if not _PROTOCOL_PATTERN.fullmatch(protocol):
            raise _scope_violation("O protocolo declarado não possui formato válido.")
        if _protocol_from_path(path) != protocol:
            raise _scope_violation("O protocolo declarado diverge do nome do PDF.")
        if path in seen_paths:
            raise _scope_violation("O lote offline contém PDF duplicado.")
        if protocol in seen_protocols:
            raise _scope_violation("O lote offline contém protocolo duplicado.")
        seen_paths.add(path)
        seen_protocols.add(protocol)
        items.append(
            FrozenOfflinePdf(
                path=path.resolve(strict=True),
                protocol=protocol,
                fingerprint=_file_fingerprint(path),
            )
        )
    frozen_items = tuple(items)
    return FrozenOfflineBatch(
        requested_limit=limit,
        items=frozen_items,
        digest=_batch_digest(limit, frozen_items),
    )


def build_direct_route_confirmation(
    operation: str,
    *,
    requested_limit: int | None = None,
) -> str:
    normalized_operation = str(operation).strip().upper()
    if not normalized_operation:
        raise ValueError("Operação direta obrigatória.")
    if requested_limit is None:
        return f"AUTORIZAR ROTA {normalized_operation}"
    limit = _validated_limit(requested_limit)
    return f"AUTORIZAR ROTA {normalized_operation} EM {limit} PROTOCOLOS"


def authorize_direct_route(
    operation: str,
    confirmation: str,
    *,
    requested_limit: int | None = None,
) -> DirectRouteAuthorization:
    expected = build_direct_route_confirmation(
        operation,
        requested_limit=requested_limit,
    )
    if confirmation != expected:
        raise OperationalBlockError(
            code="STRONG_CONFIRMATION_MISMATCH",
            user_message="Confirmação forte da rota direta não confere.",
            stage="confirmação operacional",
            technical_cause="STRONG_CONFIRMATION_MISMATCH",
        )
    return DirectRouteAuthorization(
        operation=operation,
        confirmation=confirmation,
        execution_id=uuid4().hex,
        requested_limit=requested_limit,
    )


def validate_direct_route_authorization(
    authorization: DirectRouteAuthorization | None,
    *,
    operation: str,
    requested_limit: int | None = None,
) -> DirectRouteAuthorization:
    if not isinstance(authorization, DirectRouteAuthorization):
        raise OperationalBlockError(
            code="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
            user_message="A rota importável exige autorização operacional tipada.",
            stage="autorização operacional",
            technical_cause="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
        )
    if (
        authorization.operation != operation
        or authorization.requested_limit != requested_limit
    ):
        raise OperationalBlockError(
            code="DIRECT_ROUTE_SCOPE_MISMATCH",
            user_message="A autorização não corresponde à rota solicitada.",
            stage="autorização operacional",
            technical_cause="DIRECT_ROUTE_SCOPE_MISMATCH",
        )
    expected = build_direct_route_confirmation(
        operation,
        requested_limit=requested_limit,
    )
    if authorization.confirmation != expected:
        raise OperationalBlockError(
            code="STRONG_CONFIRMATION_MISMATCH",
            user_message="Confirmação forte da rota direta não confere.",
            stage="confirmação operacional",
            technical_cause="STRONG_CONFIRMATION_MISMATCH",
        )
    return authorization


@contextmanager
def guard_direct_route(
    settings: object,
    authorization: DirectRouteAuthorization | None,
    *,
    operation: str,
    requested_limit: int | None = None,
) -> Iterator[OperationalLockProof]:
    validated = validate_direct_route_authorization(
        authorization,
        operation=operation,
        requested_limit=requested_limit,
    )
    with acquire_global_execution_lock(
        settings,
        execution_id=validated.execution_id,
        operation=operation,
        requested_limit=requested_limit or 0,
        authorization_scope=DIRECT_ROUTE_AUTHORIZATION_SCOPE,
    ) as proof:
        validate_direct_route_authorization(
            validated,
            operation=operation,
            requested_limit=requested_limit,
        )
        yield proof


@contextmanager
def guard_offline_operation(
    settings: object,
    authorization: OfflineOperationAuthorization | None,
) -> Iterator[tuple[FrozenOfflineBatch, OperationalLockProof]]:
    batch = validate_offline_authorization(authorization)
    assert authorization is not None
    with acquire_global_execution_lock(
        settings,
        execution_id=authorization.execution_id,
        operation=OPTION4_OPERATION,
        requested_limit=batch.requested_limit,
        authorization_scope=OPTION4_AUTHORIZATION_SCOPE,
    ) as proof:
        batch = validate_offline_authorization(authorization)
        yield batch, proof


@contextmanager
def acquire_global_execution_lock(
    settings: object,
    *,
    execution_id: str,
    operation: str,
    requested_limit: int,
    authorization_scope: str,
) -> Iterator[OperationalLockProof]:
    path = global_execution_lock_path(settings)
    try:
        with ExecutionLock(
            path,
            execution_id=execution_id,
            operation=operation,
            requested_batch_limit=requested_limit,
            authorization_scope=authorization_scope,
        ) as lock:
            yield OperationalLockProof(
                lock=lock,
                path=path.resolve(strict=False),
                execution_id=execution_id,
                operation=operation,
                requested_limit=requested_limit,
                authorization_scope=authorization_scope,
            )
    except ExecutionLockError as exc:
        raise OperationalBlockError(
            code=exc.code,
            user_message=str(exc),
            stage="lock global de execução",
            technical_cause=exc.code,
        ) from exc


def validate_operational_lock_proof(
    proof: OperationalLockProof | None,
    settings: object,
    *,
    allowed_operations: set[str],
) -> OperationalLockProof:
    if not isinstance(proof, OperationalLockProof) or not proof.lock.acquired:
        raise OperationalBlockError(
            code="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
            user_message="Execução real exige prova tipada do lock global ativo.",
            stage="lock global de execução",
            technical_cause="DIRECT_ROUTE_AUTHORIZATION_REQUIRED",
        )
    expected_path = global_execution_lock_path(settings).resolve(strict=False)
    metadata = proof.lock.metadata
    if (
        proof.path != expected_path
        or proof.operation not in allowed_operations
        or metadata.execution_id != proof.execution_id
        or metadata.operation != proof.operation
        or metadata.requested_batch_limit != proof.requested_limit
        or metadata.authorization_scope != proof.authorization_scope
    ):
        raise OperationalBlockError(
            code="DIRECT_ROUTE_SCOPE_MISMATCH",
            user_message="A prova do lock global não corresponde à operação real.",
            stage="lock global de execução",
            technical_cause="DIRECT_ROUTE_SCOPE_MISMATCH",
        )
    return proof


def authorize_offline_batch(
    batch: FrozenOfflineBatch,
    confirmation: str,
) -> OfflineOperationAuthorization:
    validate_frozen_batch(batch)
    expected = build_option4_strong_confirmation(batch.requested_limit)
    if confirmation != expected:
        raise OperationalBlockError(
            code="STRONG_CONFIRMATION_MISMATCH",
            user_message="Confirmação forte não corresponde ao lote offline.",
            stage="confirmação operacional",
            technical_cause="STRONG_CONFIRMATION_MISMATCH",
        )
    return OfflineOperationAuthorization(
        batch=batch,
        confirmation=confirmation,
        execution_id=uuid4().hex,
    )


def validate_offline_authorization(
    authorization: OfflineOperationAuthorization | None,
) -> FrozenOfflineBatch:
    if authorization is None:
        raise OperationalBlockError(
            code="BATCH_LIMIT_NOT_AUTHORIZED",
            user_message="Execução real exige lote offline autorizado.",
            stage="autorização do lote",
            technical_cause="BATCH_LIMIT_NOT_AUTHORIZED",
        )
    expected = build_option4_strong_confirmation(
        authorization.batch.requested_limit
    )
    if authorization.confirmation != expected:
        raise OperationalBlockError(
            code="STRONG_CONFIRMATION_MISMATCH",
            user_message="Confirmação forte não corresponde ao lote offline.",
            stage="confirmação operacional",
            technical_cause="STRONG_CONFIRMATION_MISMATCH",
        )
    validate_frozen_batch(authorization.batch)
    return authorization.batch


def validate_frozen_batch(batch: FrozenOfflineBatch) -> None:
    _validated_limit(batch.requested_limit)
    if not batch.items or len(batch.items) > batch.requested_limit:
        raise _scope_violation("Quantidade do lote offline divergiu do limite congelado.")
    protocols = [item.protocol for item in batch.items]
    if len(protocols) != len(set(protocols)):
        raise _scope_violation("O lote offline contém protocolo duplicado.")
    for item in batch.items:
        try:
            current_path = item.path.resolve(strict=True)
            fingerprint = _file_fingerprint(current_path)
        except OSError as exc:
            raise _scope_violation("Um PDF congelado não está mais disponível.") from exc
        if (
            current_path != item.path
            or _protocol_from_path(current_path) != item.protocol
            or fingerprint != item.fingerprint
        ):
            raise _scope_violation("Nome, protocolo ou fingerprint do lote foi alterado.")
    if _batch_digest(batch.requested_limit, batch.items) != batch.digest:
        raise _scope_violation("O digest do lote offline não confere.")


def global_execution_lock_path(settings: object) -> Path:
    configured = getattr(settings, "option5_execution_lock_path", None)
    if configured is not None:
        return Path(configured)
    logs_dir = getattr(settings, "logs_dir_path", None)
    if logs_dir is None:
        logs_dir = getattr(settings, "LOGS_DIR", Path("data/logs"))
    return Path(str(logs_dir)) / "option5_execution.lock"


def _validated_limit(value: int) -> int:
    limit = int(value or 0)
    if limit < 1 or limit > MAX_AUTHORIZED_PROTOCOLS:
        raise OperationalBlockError(
            code="BATCH_LIMIT_NOT_AUTHORIZED",
            user_message="Limite offline deve estar entre 1 e 5 protocolos.",
            stage="autorização do lote",
            technical_cause="BATCH_LIMIT_NOT_AUTHORIZED",
        )
    return limit


def _protocol_from_path(path: Path) -> str | None:
    match = _PROTOCOL_PATTERN.search(path.stem)
    return match.group(1) if match else None


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _batch_digest(
    requested_limit: int,
    items: tuple[FrozenOfflinePdf, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(f"limit={requested_limit}\n".encode())
    for item in items:
        digest.update(item.path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.protocol.encode("ascii"))
        digest.update(b"\0")
        digest.update(item.fingerprint.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _scope_violation(message: str) -> OperationalBlockError:
    return OperationalBlockError(
        code="FROZEN_BATCH_SCOPE_VIOLATION",
        user_message=message,
        stage="lote congelado",
        technical_cause="FROZEN_BATCH_SCOPE_VIOLATION",
    )
