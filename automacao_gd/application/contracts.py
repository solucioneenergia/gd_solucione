"""Contratos usados pela camada de aplicação.

A apresentação depende destes tipos e não de Playwright, openpyxl ou Tkinter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import logging
import unicodedata
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class OperationStatus(str, Enum):
    BLOQUEADO = "BLOQUEADO"
    PARCIAL = "PARCIAL"
    FALHOU = "FALHOU"
    SUCESSO = "SUCESSO"


@dataclass(slots=True)
class OperationResult:
    success: bool
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: OperationStatus | str | None = None

    def __post_init__(self) -> None:
        if self.status is None:
            payload_status = self.payload.get("status")
            self.status = (
                OperationStatus(str(payload_status))
                if payload_status
                else OperationStatus.SUCESSO if self.success else OperationStatus.FALHOU
            )
        elif not isinstance(self.status, OperationStatus):
            self.status = OperationStatus(str(self.status))
        self.success = self.status is OperationStatus.SUCESSO
        self.payload["status"] = self.status.value


class UserConfirmation(Protocol):
    def __call__(self, message: str) -> bool: ...


def _clamp_percent(value: int | float | None) -> int:
    if value is None:
        return 0
    return max(0, min(100, int(value)))


@dataclass(slots=True)
class ProgressEvent:
    overall_percent: int
    stage_percent: int
    stage: str
    message: str = ""
    protocol_percent: int | None = None
    protocol: str | None = None
    current: int | None = None
    total: int | None = None

    def __post_init__(self) -> None:
        self.overall_percent = _clamp_percent(self.overall_percent)
        self.stage_percent = _clamp_percent(self.stage_percent)
        if self.protocol_percent is not None:
            self.protocol_percent = _clamp_percent(self.protocol_percent)
        self.stage = str(self.stage)
        self.message = str(self.message or "")
        self.protocol = str(self.protocol) if self.protocol is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_percent": self.overall_percent,
            "stage_percent": self.stage_percent,
            "protocol_percent": self.protocol_percent,
            "stage": self.stage,
            "protocol": self.protocol,
            "message": self.message,
            "current": self.current,
            "total": self.total,
        }


ProgressCallback = Callable[[ProgressEvent], None]


MACRO_PROGRESS_STAGES = {
    "preflight": (0, 5),
    "cdp_connection": (5, 10),
    "portal_read": (10, 25),
    "protocol_selection": (25, 30),
    "protocol_processing": (30, 90),
    "report_generation": (90, 98),
    "finalization": (98, 100),
}


def protocol_progress_stages() -> list[str]:
    return [
        "localizar_pagina_protocolo",
        "abrir_detalhe",
        "baixar_ou_reutilizar_pdf",
        "extrair_pdf",
        "localizar_pasta",
        "atualizar_planilha",
        "arquivar_documento",
        "salvar_checkpoint",
    ]


def protocol_progress_stage_keys() -> list[str]:
    return [
        "locate_protocol",
        "open_detail",
        "download_or_reuse_pdf",
        "parse_pdf",
        "resolve_client_folder",
        "update_excel",
        "archive_pdf",
        "save_checkpoint",
    ]


@dataclass
class ProgressTracker:
    callback: ProgressCallback | None = None
    last_overall_percent: int = 0
    events: list[ProgressEvent] = field(default_factory=list)

    def start(self, message: str = "Iniciando operação.") -> ProgressEvent:
        return self.emit(
            ProgressEvent(
                overall_percent=0,
                stage_percent=0,
                protocol_percent=None,
                stage="preflight",
                message=message,
            )
        )

    def emit(self, event: ProgressEvent) -> ProgressEvent:
        if event.overall_percent < self.last_overall_percent:
            event = ProgressEvent(
                overall_percent=self.last_overall_percent,
                stage_percent=event.stage_percent,
                protocol_percent=event.protocol_percent,
                stage=event.stage,
                protocol=event.protocol,
                message=event.message,
                current=event.current,
                total=event.total,
            )
        self.last_overall_percent = event.overall_percent
        self.events.append(event)
        if self.callback is not None:
            try:
                self.callback(event)
            except Exception as exc:
                logger.warning(f"Falha em progress_callback ignorada: {exc}")
        return event

    def advance(
        self,
        *,
        stage: str,
        overall_percent: int,
        stage_percent: int,
        message: str,
        protocol_percent: int | None = None,
        protocol: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> ProgressEvent:
        return self.emit(
            ProgressEvent(
                overall_percent=overall_percent,
                stage_percent=stage_percent,
                protocol_percent=protocol_percent,
                stage=stage,
                protocol=protocol,
                message=message,
                current=current,
                total=total,
            )
        )

    def finish_success(self, message: str = "Operação concluída.") -> ProgressEvent:
        return self.advance(
            stage="finalization",
            overall_percent=100,
            stage_percent=100,
            message=message,
        )

    def finish_failed(self, message: str = "Operação interrompida.") -> ProgressEvent:
        safe_percent = min(self.last_overall_percent, 99)
        return self.advance(
            stage="finalization",
            overall_percent=safe_percent,
            stage_percent=100,
            message=message,
        )


class ErrorSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(slots=True)
class OperationError:
    run_id: str | None
    protocol: str | None
    stage: str
    code: str
    user_message: str
    technical_cause: str | None = None
    recoverable: bool = True
    action_taken: str | None = None
    suggested_action: str | None = None
    traceback_ref: str | None = None
    severity: str = ErrorSeverity.ERROR.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "protocol": self.protocol,
            "stage": self.stage,
            "code": self.code,
            "user_message": self.user_message,
            "technical_cause": self.technical_cause,
            "recoverable": self.recoverable,
            "action_taken": self.action_taken,
            "suggested_action": self.suggested_action,
            "traceback_ref": self.traceback_ref,
            "severity": self.severity,
        }


class StepError(OperationError):
    pass


class ProcessError(OperationError):
    pass


def map_exception_to_operation_error(
    exception: BaseException,
    stage: str,
    protocol: str | None = None,
    run_id: str | None = None,
    traceback_ref: str | None = None,
) -> OperationError:
    message = str(exception)
    lowered = message.casefold()
    search_text = _strip_accents(lowered)

    if isinstance(exception, PermissionError) or "planilha parece estar aberta" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="EXCEL_LOCKED",
            user_message="A planilha está aberta ou bloqueada.",
            technical_cause=message,
            recoverable=True,
            action_taken="A atualização da linha foi interrompida com segurança.",
            suggested_action="Feche a planilha e execute novamente.",
            traceback_ref=traceback_ref,
        )
    if "ambig" in search_text and "pasta" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="CLIENT_FOLDER_AMBIGUOUS",
            user_message="Há mais de uma pasta possível para o cliente.",
            technical_cause=message,
            recoverable=True,
            action_taken="O protocolo foi mantido para conferência.",
            suggested_action="Conferir a pasta correta do cliente.",
            traceback_ref=traceback_ref,
            severity=ErrorSeverity.WARNING.value,
        )
    if "assinatura pdf" in search_text or "assinatura" in search_text and "pdf" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="PDF_INVALID_SIGNATURE",
            user_message="O arquivo não possui assinatura PDF válida.",
            technical_cause=message,
            recoverable=True,
            action_taken="O PDF foi ignorado para evitar processamento incorreto.",
            suggested_action="Verificar se o arquivo é um orçamento de conexão válido.",
            traceback_ref=traceback_ref,
        )
    if "timeout" in search_text and "download" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="DOWNLOAD_TIMEOUT",
            user_message="O download do orçamento excedeu o tempo limite.",
            technical_cause=message,
            recoverable=True,
            action_taken="O protocolo não foi concluído nesta execução.",
            suggested_action="Executar novamente ou conferir instabilidade do portal.",
            traceback_ref=traceback_ref,
        )
    if "cdp" in search_text or "edge" in search_text and "conex" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="CDP_CONNECTION_FAILED",
            user_message="Não foi possível conectar ao Edge via CDP.",
            technical_cause=message,
            recoverable=True,
            action_taken="A automação do portal não foi iniciada.",
            suggested_action="Abrir o Edge com a porta CDP e refazer a conexão.",
            traceback_ref=traceback_ref,
        )
    if "access denied" in search_text or "acesso negado" in search_text and "portal" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="PORTAL_ACCESS_DENIED",
            user_message="O portal recusou o acesso.",
            technical_cause=message,
            recoverable=True,
            action_taken="A execução foi interrompida para evitar novas tentativas automáticas.",
            suggested_action="Reabrir o Edge manualmente, autenticar e testar CDP.",
            traceback_ref=traceback_ref,
        )
    if "equipamento" in search_text or "extracao tecnica" in search_text:
        return OperationError(
            run_id=run_id,
            protocol=protocol,
            stage=stage,
            code="EQUIPMENT_PARSE_WARNING",
            user_message="Há pendência na extração técnica dos equipamentos.",
            technical_cause=message,
            recoverable=True,
            action_taken="O protocolo deve ser conferido antes de conclusão operacional.",
            suggested_action="Conferir Placa e Inversor no orçamento.",
            traceback_ref=traceback_ref,
            severity=ErrorSeverity.WARNING.value,
        )
    return OperationError(
        run_id=run_id,
        protocol=protocol,
        stage=stage,
        code="UNKNOWN_ERROR",
        user_message="Ocorreu um erro inesperado.",
        technical_cause=message,
        recoverable=False,
        action_taken="A etapa foi interrompida.",
        suggested_action="Consultar log técnico.",
        traceback_ref=traceback_ref,
    )


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))
