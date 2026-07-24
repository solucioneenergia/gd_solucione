from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.infrastructure.log_privacy import (
    audit_log_files,
    sanitize_log_text,
    sanitize_log_with_backup,
)
from automacao_gd.infrastructure.logging import logger, setup_logger


@pytest.mark.parametrize(
    "sensitive",
    [
        r"C:\\Clientes\\Pessoa Exemplo\\documento.pdf",
        r"Z:\\Empresa\\planilha.xlsx",
        r"\\servidor\\clientes\\arquivo.pdf",
        "/home/usuario/clientes/arquivo.xlsx",
        "/workspace/projeto/clientes/arquivo.pdf",
        "pessoa@example.com",
        "CPF: 123.456.789-00",
        "cpf=12345678900",
        "CNPJ: 12.345.678/0001-90",
        "Authorization: Bearer segredo-super-secreto",
        "cookie=sessionid=segredo",
        "token=segredo",
    ],
)
def test_sanitizes_sensitive_log_values(sensitive: str) -> None:
    message = f"protocolo=2509231679 operation=pdf_read value={sensitive}"
    sanitized = sanitize_log_text(message)

    assert "2509231679" in sanitized
    assert sensitive not in sanitized


def test_sanitizes_nested_settings_and_messages() -> None:
    message = repr(
        {
            "settings": {
                "workbook": r"Z:\\Empresa\\planilha.xlsx",
                "token": "segredo",
            },
            "nested": {"email": "pessoa@example.com"},
        }
    )
    sanitized = sanitize_log_text(message)
    assert "planilha.xlsx" not in sanitized
    assert "segredo" not in sanitized
    assert "pessoa@example.com" not in sanitized


def test_sanitizes_exception_and_traceback_paths() -> None:
    traceback_text = (
        'Traceback (most recent call last):\n'
        '  File "C:\\Projeto\\parser.py", line 10, in read\n'
        '  File "/workspace/projeto/parser.py", line 20, in parse\n'
        "FileNotFoundError: Z:\\Clientes\\Pessoa\\arquivo.pdf"
    )
    sanitized = sanitize_log_text(traceback_text)
    assert "Traceback" in sanitized
    assert "FileNotFoundError" in sanitized
    assert "C:\\" not in sanitized
    assert "Z:\\" not in sanitized
    assert "/workspace/" not in sanitized
    assert ".pdf" not in sanitized


def test_preserves_labeled_protocol_even_when_it_has_eleven_digits() -> None:
    sanitized = sanitize_log_text(
        "protocolo=25092316791 cpf=12345678900 path=/workspace/projeto/arquivo.pdf"
    )
    assert "protocolo=25092316791" in sanitized
    assert "12345678900" not in sanitized
    assert "/workspace/" not in sanitized


def test_read_only_log_audit_reports_only_counts(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    original = (
        "2026-07-22 | INFO | protocolo=2509231679 C:\\Clientes\\arquivo.pdf\n"
        "2026-07-22 | ERROR | pessoa@example.com token=segredo\n"
    ).encode()
    log.write_bytes(original)

    report = audit_log_files(tmp_path)

    assert log.read_bytes() == original
    assert report.files_analyzed == 1
    assert report.absolute_paths_detected == 1
    assert report.email_patterns == 1
    assert report.tokens_or_cookies == 1
    assert "2509231679" not in repr(report)


def test_historical_sanitization_requires_strong_confirmation(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    original = b"2026-07-22 | INFO | C:\\Clientes\\arquivo.pdf\n"
    log.write_bytes(original)

    with pytest.raises(ValueError, match="SANITIZAR LOGS"):
        sanitize_log_with_backup(log, confirmation="sim")

    assert log.read_bytes() == original
    assert not list(tmp_path.glob("*.backup*"))


def test_historical_sanitization_creates_backup_and_verifies_result(
    tmp_path: Path,
) -> None:
    log = tmp_path / "app.log"
    original = b"2026-07-22 | INFO | C:\\Clientes\\arquivo.pdf\n"
    log.write_bytes(original)

    report = sanitize_log_with_backup(log, confirmation="SANITIZAR LOGS")

    assert report.backup_created
    assert report.remaining_sensitive_entries == 0
    assert any(path.read_bytes() == original for path in tmp_path.glob("*.backup*"))
    sanitized = log.read_text(encoding="utf-8")
    assert "2026-07-22 | INFO" in sanitized
    assert "C:\\" not in sanitized
    assert ".pdf" not in sanitized


def test_repeated_historical_sanitization_never_overwrites_backup(
    tmp_path: Path,
) -> None:
    log = tmp_path / "app.log"
    original = b"2026-07-22 | INFO | C:\\Clientes\\arquivo.pdf\n"
    log.write_bytes(original)

    sanitize_log_with_backup(log, confirmation="SANITIZAR LOGS")
    first_sanitized = log.read_bytes()
    sanitize_log_with_backup(log, confirmation="SANITIZAR LOGS")

    backups = sorted(tmp_path.glob("*.backup-*"))
    assert len(backups) == 2
    assert original in {path.read_bytes() for path in backups}
    assert first_sanitized in {path.read_bytes() for path in backups}


def test_persistent_sink_sanitizes_message_and_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.get_settings",
        lambda: SimpleNamespace(LOG_LEVEL="DEBUG", logs_dir_path=tmp_path),
    )
    setup_logger(tmp_path)
    try:
        logger.error(
            "protocolo=2509231679 path={} token={}",
            r"Z:\\Clientes\\Pessoa\\arquivo.pdf",
            "segredo",
        )
        try:
            raise FileNotFoundError(r"C:\\Projeto\\planilha.xlsx")
        except FileNotFoundError:
            logger.exception("operation=workbook_read protocolo=2509231679")
        logger.complete()
    finally:
        logger.remove()

    persisted = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "2509231679" in persisted
    for forbidden in (
        "C:\\",
        "Z:\\",
        "\\\\servidor\\",
        "/home/",
        "@",
        "Bearer",
        "cookie",
        ".pdf",
        ".xlsx",
        "segredo",
    ):
        assert forbidden not in persisted
