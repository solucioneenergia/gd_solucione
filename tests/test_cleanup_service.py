from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from automacao_gd.application.use_cases.cleanup_temp import CleanupTemporaryFilesUseCase
from automacao_gd.infrastructure.config import Settings
from automacao_gd.infrastructure.files.cleanup import cleanup_temp_files
from automacao_gd.presentation.operational_output import format_cleanup_summary


def _write(path: Path, content: str = "synthetic") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _create_outside_root_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            raise AssertionError("Symlink indisponível neste ambiente de teste.") from exc

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Não foi possível criar symlink nem junction para o teste de cleanup: "
            f"{result.stderr or result.stdout}"
        )


def test_cleanup_dry_run_reports_candidates_without_deleting(tmp_path: Path) -> None:
    removable = [
        _write(tmp_path / ".atomic.target.x.tmp"),
        _write(tmp_path / "arquivo.temp"),
        _write(tmp_path / "download.crdownload"),
        _write(tmp_path / "download.part"),
        _write(tmp_path / "~$planilha.xlsx"),
    ]

    report = cleanup_temp_files(tmp_path)

    assert report["dry_run"] is True
    assert set(removable) <= set(report["would_remove"])
    assert all(path.exists() for path in removable)
    assert report["deleted_count"] == 0
    assert report["total_bytes_candidate"] > 0


def test_cleanup_apply_removes_only_allowed_temporary_patterns(tmp_path: Path) -> None:
    removable = [
        _write(tmp_path / ".a.tmp"),
        _write(tmp_path / ".b.temp"),
        _write(tmp_path / "download.crdownload"),
        _write(tmp_path / "download.part"),
        _write(tmp_path / "~$planilha.xlsx"),
    ]
    protected = [
        _write(tmp_path / "orcamento.pdf"),
        _write(tmp_path / "planilha.xlsx"),
        _write(tmp_path / "macro.xlsm"),
        _write(tmp_path / "pipeline_cdp_state.json"),
        _write(tmp_path / "client_folder_cache.json"),
        _write(tmp_path / "relatorio.md"),
        _write(tmp_path / "relatorio.json"),
        _write(tmp_path / "planilha_backup_20260719.xlsx"),
    ]

    report = cleanup_temp_files(tmp_path, dry_run=False)

    assert all(not path.exists() for path in removable)
    assert all(path.exists() for path in protected)
    assert report["deleted_count"] == len(removable)
    assert report["skipped_count"] == 0


def test_cleanup_protects_operational_data_directories(tmp_path: Path) -> None:
    protected = [
        _write(tmp_path / "data/state/.pipeline.tmp"),
        _write(tmp_path / "data/cache/cache.tmp"),
        _write(tmp_path / "data/downloads/2600000001/orcamento.tmp"),
        _write(tmp_path / "data/downloads/2600000001/orcamento.pdf"),
        _write(tmp_path / "data/logs/relatorio.json"),
        _write(tmp_path / "data/logs/relatorio.md"),
    ]
    removable = [
        _write(tmp_path / "data/downloads/2600000001/download.crdownload"),
        _write(tmp_path / "data/downloads/2600000001/download.part"),
        _write(tmp_path / "data/logs/.relatorio.tmp"),
    ]

    report = cleanup_temp_files(tmp_path, dry_run=False)

    assert all(path.exists() for path in protected)
    assert all(not path.exists() for path in removable)
    assert report["warnings"]


def test_cleanup_blocks_explicit_candidate_outside_allowed_root(tmp_path: Path) -> None:
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    outside = _write(tmp_path / "outside.tmp")

    with pytest.raises(ValueError):
        cleanup_temp_files(safe_root, candidates=[safe_root / ".." / "outside.tmp"], dry_run=False)

    assert outside.exists()


def test_cleanup_rejects_nonexistent_allowed_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        cleanup_temp_files(tmp_path / "missing", dry_run=True)


def test_cleanup_ignores_symlink_to_outside_allowed_root(tmp_path: Path) -> None:
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = _write(outside / "outside.tmp")
    link = safe_root / "linked.tmp"
    _create_outside_root_link(link, outside)

    report = cleanup_temp_files(safe_root, dry_run=False)

    assert outside.exists()
    assert outside_file.exists()
    assert link.exists()
    assert report["warnings"]
    assert not report["deleted_files"]


def test_cleanup_records_delete_error_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = _write(tmp_path / "blocked.tmp")
    removable = _write(tmp_path / "removable.part")

    def fake_delete(path: Path) -> None:
        if path == blocked:
            raise PermissionError("arquivo bloqueado")
        path.unlink()

    monkeypatch.setattr(
        "automacao_gd.infrastructure.files.cleanup._delete_candidate",
        fake_delete,
    )

    report = cleanup_temp_files(tmp_path, dry_run=False)

    assert blocked.exists()
    assert not removable.exists()
    assert report["deleted_files"] == [removable]
    assert blocked in report["skipped_files"]
    assert report["errors"]


def test_cleanup_can_remove_safe_empty_temporary_directory(tmp_path: Path) -> None:
    empty_temp_dir = tmp_path / "work.tmp"
    empty_temp_dir.mkdir()

    report = cleanup_temp_files(tmp_path, dry_run=False)

    assert not empty_temp_dir.exists()
    assert empty_temp_dir in report["deleted_files"]


def test_cleanup_report_contains_required_fields(tmp_path: Path) -> None:
    _write(tmp_path / "arquivo.tmp")

    report = cleanup_temp_files(tmp_path, dry_run=True)

    for key in [
        "dry_run",
        "scanned_count",
        "deleted_count",
        "skipped_count",
        "warnings",
        "errors",
    ]:
        assert key in report.to_dict()


def test_cleanup_report_has_json_serializable_payload(tmp_path: Path) -> None:
    removable = _write(tmp_path / "arquivo.tmp")

    report = cleanup_temp_files(tmp_path, dry_run=True)
    payload = report.to_serializable_dict()

    json.dumps(payload, ensure_ascii=False)
    assert payload["would_remove"] == [str(removable)]


def test_cleanup_use_case_returns_json_serializable_payload(tmp_path: Path) -> None:
    temp_root = tmp_path / "temp"
    _write(temp_root / "arquivo.tmp")
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        PORTAL_GD_URL="https://example.com/",
        PLANILHA_PATH=tmp_path / "planilha.xlsx",
        CLIENTES_ROOT=tmp_path / "clientes",
        DOWNLOADS_DIR=tmp_path / "downloads",
        LOGS_DIR=tmp_path / "logs",
        AUTH_STATE_PATH=tmp_path / "auth" / "state.json",
        BROWSER_PROFILE_DIR=tmp_path / "browser",
    )

    payload = CleanupTemporaryFilesUseCase(settings).execute(root=temp_root, dry_run=True)

    json.dumps(payload, ensure_ascii=False)
    assert payload["would_remove"] == [str(temp_root / "arquivo.tmp")]


def test_cleanup_summary_is_operational_and_compact(tmp_path: Path) -> None:
    _write(tmp_path / "arquivo.tmp")

    summary = format_cleanup_summary(cleanup_temp_files(tmp_path, dry_run=True))

    assert "Limpeza de temporários" in summary
    assert "Modo: Simulação" in summary
    assert "Arquivos analisados:" in summary
