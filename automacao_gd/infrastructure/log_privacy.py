from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_LABELED_DIGITS_RE = re.compile(
    r"(?i)\b(?:cpf|cnpj|documento|tax_id)\s*[:=]\s*\d{11,14}\b"
)
_LABELED_PHONE_RE = re.compile(
    r"(?i)(\b(?:telefone|celular|whatsapp|phone|mobile)\s*[:=]\s*)"
    r"(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}"
)
_LABELED_PROTOCOL_RE = re.compile(
    r"(?i)(\b(?:protocol|protocolo|protocol_number|numero_protocolo|"
    r"protocolo_neoenergia)\s*[:=]\s*)(\d{6,})"
)
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:['\"])?\b(?:authorization|bearer|token|access_token|refresh_token|"
    r"cookie|set-cookie|sessionid|password|senha)\b(?:['\"])?\s*[:=]?\s*"
    r"(?:['\"])?[^\s,;)}\]'\"]+(?:['\"])?"
)
_RAW_TEXT_RE = re.compile(
    r"(?is)(?:['\"]?raw_text['\"]?\s*[:=]\s*)(?:.+?)(?=(?:[,}]\s*)?$)"
)
_WINDOWS_PATH_RE = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:\\|[A-Z]:/)(?:[^\r\n\"'|<>]+)"
)
_UNC_PATH_RE = re.compile(r"\\\\[^\r\n\"'|<>]+")
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:])/(?:[^/\r\n\"'|<>]+/)+[^/\r\n\"'|<>]+"
)
_DOCUMENT_NAME_RE = re.compile(r"(?i)(?<![\w])[^\s,;)}\]]+\.(?:pdf|xlsx)\b")
_UNSTRUCTURED_RE = re.compile(r"^\s*(?!\d{4}-\d{2}-\d{2}).+", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class LogPrivacyAuditReport:
    files_analyzed: int
    total_bytes: int
    absolute_paths_detected: int
    email_patterns: int
    cpf_patterns: int
    cnpj_patterns: int
    tokens_or_cookies: int
    unstructured_messages: int


@dataclass(frozen=True, slots=True)
class LogSanitizationReport:
    entries_sanitized: int
    backup_created: bool
    remaining_sensitive_entries: int


def sanitize_log_text(value: object) -> str:
    """Defensively redact sensitive values without removing event metadata."""
    text, protected_protocols = _protect_labeled_protocols(str(value))
    text = _RAW_TEXT_RE.sub("raw_text=[CONTENT REDACTED]", text)
    text = _CREDENTIAL_RE.sub("[CREDENTIAL REDACTED]", text)
    text = _EMAIL_RE.sub("[EMAIL REDACTED]", text)
    text = _CNPJ_RE.sub("[DOCUMENT REDACTED]", text)
    text = _CPF_RE.sub("[DOCUMENT REDACTED]", text)
    text = _LABELED_DIGITS_RE.sub("document=[DOCUMENT REDACTED]", text)
    text = _LABELED_PHONE_RE.sub(r"\1[PHONE REDACTED]", text)
    text = _UNC_PATH_RE.sub("<redacted>", text)
    text = _WINDOWS_PATH_RE.sub("<redacted>", text)
    text = _POSIX_PATH_RE.sub("<redacted>", text)
    text = _DOCUMENT_NAME_RE.sub("<redacted>", text)
    for placeholder, protocol in protected_protocols.items():
        text = text.replace(placeholder, protocol)
    return text


def audit_log_files(path: Path) -> LogPrivacyAuditReport:
    """Read log files and return counts only; never return matched values."""
    files = _log_files(path)
    counts = {
        "absolute_paths_detected": 0,
        "email_patterns": 0,
        "cpf_patterns": 0,
        "cnpj_patterns": 0,
        "tokens_or_cookies": 0,
        "unstructured_messages": 0,
    }
    total_bytes = 0
    for file_path in files:
        raw = file_path.read_bytes()
        total_bytes += len(raw)
        text = raw.decode("utf-8", errors="replace")
        personal_text, _ = _protect_labeled_protocols(text)
        counts["absolute_paths_detected"] += sum(
            len(pattern.findall(text))
            for pattern in (_WINDOWS_PATH_RE, _UNC_PATH_RE, _POSIX_PATH_RE)
        )
        counts["email_patterns"] += len(_EMAIL_RE.findall(personal_text))
        counts["cpf_patterns"] += len(_CPF_RE.findall(personal_text)) + len(
            _LABELED_DIGITS_RE.findall(personal_text)
        )
        counts["cnpj_patterns"] += len(_CNPJ_RE.findall(personal_text))
        counts["tokens_or_cookies"] += len(_CREDENTIAL_RE.findall(text))
        counts["unstructured_messages"] += len(_UNSTRUCTURED_RE.findall(text))
    return LogPrivacyAuditReport(
        files_analyzed=len(files), total_bytes=total_bytes, **counts
    )


def sanitize_log_with_backup(
    path: Path,
    *,
    confirmation: str,
) -> LogSanitizationReport:
    """Explicitly sanitize one historical log after a byte-for-byte backup."""
    if confirmation != "SANITIZAR LOGS":
        raise ValueError("Confirmação obrigatória: SANITIZAR LOGS")
    source = Path(path)
    original = source.read_bytes()
    original_text = original.decode("utf-8", errors="replace")
    sanitized = sanitize_log_text(original_text)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = _next_backup_path(source, suffix)
    shutil.copy2(source, backup)
    if backup.read_bytes() != original:
        raise OSError("Falha ao verificar backup do log")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.", suffix=".tmp", dir=source.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(sanitized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source)
    finally:
        temporary.unlink(missing_ok=True)
    remaining = _sensitive_count(source.read_text(encoding="utf-8", errors="replace"))
    if remaining:
        raise OSError("A verificação detectou entradas sensíveis remanescentes")
    return LogSanitizationReport(
        entries_sanitized=_sensitive_count(original_text),
        backup_created=True,
        remaining_sensitive_entries=remaining,
    )


def _next_backup_path(source: Path, suffix: str) -> Path:
    candidate = source.with_name(f"{source.name}.backup-{suffix}")
    counter = 1
    while candidate.exists():
        candidate = source.with_name(f"{source.name}.backup-{suffix}-{counter}")
        counter += 1
    return candidate


def _log_files(path: Path) -> tuple[Path, ...]:
    target = Path(path)
    if target.is_file():
        return (target,)
    if not target.exists():
        return ()
    return tuple(
        sorted(
            candidate
            for candidate in target.glob("*.log*")
            if candidate.is_file() and ".backup-" not in candidate.name
        )
    )


def _sensitive_count(text: str) -> int:
    personal_text, _ = _protect_labeled_protocols(text)
    return sum(
        len(pattern.findall(personal_text))
        for pattern in (
            _EMAIL_RE,
            _CNPJ_RE,
            _CPF_RE,
            _LABELED_DIGITS_RE,
            _CREDENTIAL_RE,
            _WINDOWS_PATH_RE,
            _UNC_PATH_RE,
            _POSIX_PATH_RE,
            _DOCUMENT_NAME_RE,
        )
    )


def _protect_labeled_protocols(value: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        placeholder = f"__PROTOCOL_RETAINED_{len(protected)}__"
        protected[placeholder] = match.group(2)
        return match.group(1) + placeholder

    return _LABELED_PROTOCOL_RE.sub(replace, value), protected
