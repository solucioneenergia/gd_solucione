from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.infrastructure.log_privacy import (
    audit_log_files,
    sanitize_log_text,
    sanitize_log_with_backup,
)
from automacao_gd.infrastructure.logging import logger, setup_logger


_PRIVATE_PROTOCOL = "25092" + "31679"
_PRIVATE_PROTOCOL_11 = _PRIVATE_PROTOCOL + "1"
_PRIVATE_CPF = "123.456." + "789-00"
_PRIVATE_CPF_DIGITS = "123456" + "78900"
_PRIVATE_CNPJ = "12.345." + "678/0001-90"
_PRIVATE_POSIX_PATH = "/" + "home/usuario/clientes/arquivo.xlsx"
_PRIVATE_UNC_PATH = "\\" * 2 + "servidor\\clientes\\arquivo.pdf"
_PRIVATE_WINDOWS_PATH = "C:" + "\\Clientes\\arquivo.pdf"


@pytest.mark.parametrize(
    "sensitive",
    [
        r"C:\\Clientes\\Pessoa Exemplo\\documento.pdf",
        r"Z:\\Empresa\\planilha.xlsx",
        _PRIVATE_UNC_PATH,
        _PRIVATE_POSIX_PATH,
        "/workspace/projeto/clientes/arquivo.pdf",
        "pessoa@example.com",
        f"CPF: {_PRIVATE_CPF}",
        f"cpf={_PRIVATE_CPF_DIGITS}",
        f"CNPJ: {_PRIVATE_CNPJ}",
        "Authorization: Bearer segredo-super-secreto",
        "cookie=sessionid=segredo",
        "token=segredo",
    ],
)
def test_sanitizes_sensitive_log_values(sensitive: str) -> None:
    message = f"protocolo={_PRIVATE_PROTOCOL} operation=pdf_read value={sensitive}"
    sanitized = sanitize_log_text(message)

    assert _PRIVATE_PROTOCOL in sanitized
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
        f"protocolo={_PRIVATE_PROTOCOL_11} cpf={_PRIVATE_CPF_DIGITS} "
        "path=/workspace/projeto/arquivo.pdf"
    )
    assert f"protocolo={_PRIVATE_PROTOCOL_11}" in sanitized
    assert _PRIVATE_CPF_DIGITS not in sanitized
    assert "/workspace/" not in sanitized


def test_read_only_log_audit_reports_only_counts(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    original = (
        f"2026-07-22 | INFO | protocolo={_PRIVATE_PROTOCOL} "
        f"{_PRIVATE_WINDOWS_PATH}\n"
        "2026-07-22 | ERROR | pessoa@example.com token=segredo\n"
    ).encode()
    log.write_bytes(original)

    report = audit_log_files(tmp_path)

    assert log.read_bytes() == original
    assert report.files_analyzed == 1
    assert report.absolute_paths_detected == 1
    assert report.email_patterns == 1
    assert report.tokens_or_cookies == 1
    assert _PRIVATE_PROTOCOL not in repr(report)


def test_historical_sanitization_requires_strong_confirmation(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    original = f"2026-07-22 | INFO | {_PRIVATE_WINDOWS_PATH}\n".encode()
    log.write_bytes(original)

    with pytest.raises(ValueError, match="SANITIZAR LOGS"):
        sanitize_log_with_backup(log, confirmation="sim")

    assert log.read_bytes() == original
    assert not list(tmp_path.glob("*.backup*"))


def test_historical_sanitization_creates_backup_and_verifies_result(
    tmp_path: Path,
) -> None:
    log = tmp_path / "app.log"
    original = f"2026-07-22 | INFO | {_PRIVATE_WINDOWS_PATH}\n".encode()
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
    original = f"2026-07-22 | INFO | {_PRIVATE_WINDOWS_PATH}\n".encode()
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
            f"protocolo={_PRIVATE_PROTOCOL} path={{}} token={{}}",
            r"Z:\\Clientes\\Pessoa\\arquivo.pdf",
            "segredo",
        )
        try:
            raise FileNotFoundError(r"C:\\Projeto\\planilha.xlsx")
        except FileNotFoundError:
            logger.exception(
                f"operation=workbook_read protocolo={_PRIVATE_PROTOCOL}"
            )
        logger.complete()
    finally:
        logger.remove()

    persisted = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert _PRIVATE_PROTOCOL in persisted
    for forbidden in (
        "C:\\",
        "Z:\\",
        "\\" * 2 + "servidor\\",
        "/" + "home/",
        "@",
        "Bearer",
        "cookie",
        ".pdf",
        ".xlsx",
        "segredo",
    ):
        assert forbidden not in persisted


def test_setup_logger_does_not_add_missing_console_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.get_settings",
        lambda: SimpleNamespace(LOG_LEVEL="INFO", logs_dir_path=tmp_path),
    )
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "__stderr__", None)

    setup_logger()
    try:
        logger.info("bootstrap manual sem console")
        logger.complete()
    finally:
        logger.remove()

    assert (tmp_path / "app.log").is_file()


def test_setup_logger_uses_safe_frozen_fallback_without_configured_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "__stderr__", None)
    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.get_settings",
        lambda: SimpleNamespace(
            LOG_LEVEL="INFO",
            LOGS_DIR=Path("data/logs"),
            logs_dir_path=None,
        ),
    )

    setup_logger()
    try:
        logger.info("bootstrap frozen com log local")
        logger.complete()
    finally:
        logger.remove()

    assert (local_app_data / "AutomacaoGDNeoenergia" / "logs" / "app.log").is_file()


def test_persistent_app_log_does_not_configure_windows_unsafe_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    added_sinks: list[tuple[object, dict[str, object]]] = []

    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.get_settings",
        lambda: SimpleNamespace(LOG_LEVEL="INFO", logs_dir_path=tmp_path),
    )

    def capture_add(sink: object, **kwargs: object) -> int:
        added_sinks.append((sink, kwargs))
        return len(added_sinks)

    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.logger.remove",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "automacao_gd.infrastructure.logging.logger.add",
        capture_add,
    )

    setup_logger(tmp_path)

    file_sinks = [
        (sink, kwargs)
        for sink, kwargs in added_sinks
        if Path(str(sink)).name == "app.log"
    ]
    assert len(file_sinks) == 1
    assert file_sinks[0][1].get("rotation") is None
