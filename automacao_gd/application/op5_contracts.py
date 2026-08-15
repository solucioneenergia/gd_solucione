import hashlib
from dataclasses import dataclass
from pathlib import Path


CONTROLLED_PRODUCTION_AUTHORIZATION_SCOPE = "CONTROLLED_PRODUCTION_V2_0_1"
CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE = (
    "CONTROLLED_PRODUCTION_OPTION5_UP_TO_60"
)
SYNTHETIC_BATCH10_AUTHORIZATION_SCOPE = "SYNTHETIC_BATCH10_VALIDATION"


class BatchAuthorizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StrongConfirmationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FrozenBatchScopeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BatchAuthorizationPolicy:
    authorized_max_protocols: int
    authorization_scope: str


@dataclass(frozen=True)
class BatchAuthorization:
    requested_batch_limit: int
    authorized_batch_limit: int
    authorization_scope: str


@dataclass(frozen=True)
class FrozenProtocolBatch:
    requested_limit: int
    authorized_limit: int
    authorization_scope: str
    protocols: tuple[str, ...]
    unique_before_limit: int
    dropped_by_limit: int
    duplicate_protocols_in_frozen_batch: int = 0
    protocols_added_after_freeze: int = 0

    def __post_init__(self) -> None:
        if self.requested_limit <= 0 or self.requested_limit > self.authorized_limit:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Limites do lote congelado são inválidos.",
            )
        if len(self.protocols) > self.requested_limit:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Lote congelado excede o limite solicitado.",
            )
        if len(self.protocols) != len(set(self.protocols)):
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                "Lote congelado contém protocolos duplicados.",
            )

    def validate_phase_protocols(self, protocols: list[str], *, phase: str) -> None:
        unknown = sorted({protocol for protocol in protocols if protocol not in self.protocols})
        if unknown:
            raise FrozenBatchScopeError(
                "FROZEN_BATCH_SCOPE_VIOLATION",
                f"Fase {phase} tentou processar protocolos fora do lote congelado.",
            )


@dataclass(frozen=True)
class FrozenPdfArtifact:
    protocol: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class FrozenPdfScope:
    artifacts: tuple[FrozenPdfArtifact, ...]
    digest: str

    def validate(self) -> None:
        for artifact in self.artifacts:
            if not artifact.path.is_file() or file_sha256(artifact.path) != artifact.sha256:
                raise FrozenBatchScopeError(
                    "FROZEN_BATCH_SCOPE_VIOLATION",
                    "PDF do lote congelado foi removido ou alterado.",
                )


@dataclass(frozen=True)
class LimitedProtocolSelection:
    summary: dict
    frozen_batch: FrozenProtocolBatch


@dataclass(frozen=True)
class FrozenDryRunPlan:
    summary: dict
    frozen_batch: FrozenProtocolBatch
    frozen_pdf_scope: FrozenPdfScope
    source_path: Path
    source_kind: str = "legacy_pipeline_report"


def default_batch_authorization_policy() -> BatchAuthorizationPolicy:
    return BatchAuthorizationPolicy(
        authorized_max_protocols=5,
        authorization_scope=CONTROLLED_PRODUCTION_AUTHORIZATION_SCOPE,
    )


def batch_authorization_policy_from_settings(settings: object) -> BatchAuthorizationPolicy:
    raw_authorized = getattr(settings, "OPTION5_AUTHORIZED_MAX_PROTOCOLS", 5)
    try:
        authorized_max_protocols = int(raw_authorized or 0)
    except (TypeError, ValueError) as exc:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite autorizado para opção 5 deve ser numérico.",
        ) from exc

    if 1 <= authorized_max_protocols <= 5:
        return default_batch_authorization_policy()
    if 5 < authorized_max_protocols <= 60:
        return BatchAuthorizationPolicy(
            authorized_max_protocols=authorized_max_protocols,
            authorization_scope=CONTROLLED_PRODUCTION_UP_TO_60_AUTHORIZATION_SCOPE,
        )
    raise BatchAuthorizationError(
        "BATCH_LIMIT_NOT_AUTHORIZED",
        "Limite autorizado para opção 5 deve estar entre 1 e 60.",
    )


def validate_requested_batch_limit(
    requested_batch_limit: int,
    policy: BatchAuthorizationPolicy | None = None,
) -> BatchAuthorization:
    active_policy = policy or default_batch_authorization_policy()
    requested = int(requested_batch_limit or 0)
    authorized = int(active_policy.authorized_max_protocols or 0)
    if requested <= 0 or authorized <= 0 or requested > authorized:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite de protocolos solicitado não está autorizado.",
        )
    return BatchAuthorization(
        requested_batch_limit=requested,
        authorized_batch_limit=authorized,
        authorization_scope=active_policy.authorization_scope,
    )


def build_option5_strong_confirmation(protocol_limit: int) -> str:
    limit = int(protocol_limit)
    if limit <= 0:
        raise BatchAuthorizationError(
            "BATCH_LIMIT_NOT_AUTHORIZED",
            "Limite de protocolos inválido para confirmação forte.",
        )
    return f"APLICAR OPÇÃO 5 COM CONCLUSÃO EM {limit} PROTOCOLOS"


def validate_option5_strong_confirmation(
    confirmation: str,
    authorization: BatchAuthorization,
) -> bool:
    expected = build_option5_strong_confirmation(authorization.requested_batch_limit)
    if confirmation != expected:
        raise StrongConfirmationError(
            "STRONG_CONFIRMATION_MISMATCH",
            "Confirmação forte não corresponde ao lote autorizado.",
        )
    return True


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
