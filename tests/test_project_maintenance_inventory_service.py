from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from automacao_gd.application.project_maintenance_inventory_service import (
    FinalArtifactWriter,
    InMemoryArtifactWriter,
    MaintenanceClassification,
    ReferenceKind,
    Stage51DiagnosticError,
    build_stage51_artifact_bundle,
    capture_filesystem_snapshot,
    compare_filesystem_snapshots,
    resolve_canonical_project_root,
    run_project_maintenance_inventory,
    save_project_maintenance_artifacts,
)


def _write(path: Path, content: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def _item(result: dict, relative_path: str) -> dict:
    return next(item for item in result["inventory"]["files"] if item["relative_path"] == relative_path)


def _project_markers(root: Path) -> None:
    _write(root / "AGENTS.md", "agents")
    (root / "automacao_gd").mkdir()
    (root / "scripts").mkdir()
    (root / "tests").mkdir()
    (root / "data" / "logs").mkdir(parents=True)


def test_workbook_is_protected_absolute(tmp_path: Path) -> None:
    workbook = _write(tmp_path / "data" / "planilha.xlsx", b"PK\x03\x04workbook")

    result = run_project_maintenance_inventory(
        tmp_path,
        official_workbook_path=workbook,
        external_roots=[],
    )

    item = _item(result, "data/planilha.xlsx")
    assert item["classification"] == MaintenanceClassification.PROTECTED_ABSOLUTE
    assert item["delete_now"] is False


def test_file_referenced_by_report_is_protected_referenced(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "logs" / "report.md", "ver data/logs/evidence.json")
    _write(tmp_path / "data" / "logs" / "evidence.json", "{}")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, "data/logs/evidence.json")["classification"] == MaintenanceClassification.PROTECTED_REFERENCED


def test_orphan_temporary_candidate_has_all_actions_disabled(tmp_path: Path) -> None:
    tmp_file = _write(tmp_path / "data" / "temp" / "old.tmp", b"temporary")
    old = 1_600_000_000
    os.utime(tmp_file, (old, old))

    result = run_project_maintenance_inventory(tmp_path, external_roots=[], now_epoch=1_700_000_000)
    item = _item(result, "data/temp/old.tmp")

    assert item["classification"] == MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE
    assert item["delete_now"] is False
    assert item["move_now"] is False
    assert item["archive_now"] is False
    assert item["compress_now"] is False


def test_referenced_temporary_is_protected(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "ledger.md", "mantem data/temp/old.tmp")
    _write(tmp_path / "data" / "temp" / "old.tmp", b"temporary")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, "data/temp/old.tmp")["classification"] == MaintenanceClassification.PROTECTED_REFERENCED


def test_regenerable_cache_is_classified(tmp_path: Path) -> None:
    _write(tmp_path / ".pytest_cache" / "v" / "cache" / "nodeids", "[]")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, ".pytest_cache/v/cache/nodeids")["classification"] == MaintenanceClassification.REGENERABLE_CACHE


def test_identical_files_share_duplicate_group_and_recoverable_bytes(tmp_path: Path) -> None:
    _write(tmp_path / "outputs" / "a.bin", b"same")
    _write(tmp_path / "outputs" / "b.bin", b"same")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    a = _item(result, "outputs/a.bin")
    b = _item(result, "outputs/b.bin")
    group = result["duplicates"]["groups"][0]

    assert a["duplicate_group_id"] == b["duplicate_group_id"]
    assert group["potential_recoverable_bytes"] == 4


def test_protected_duplicate_is_not_delete_candidate(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "same")
    _write(tmp_path / "outputs" / "copy.md", "same")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, "README.md")["classification"] == MaintenanceClassification.PROTECTED_ABSOLUTE
    assert _item(result, "outputs/copy.md")["delete_now"] is False


def test_untracked_file_is_not_discarded_by_git_status_alone(tmp_path: Path) -> None:
    _write(tmp_path / "random.bin", b"unknown")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[], git_tracked=set(), git_untracked={"random.bin"})

    assert _item(result, "random.bin")["classification"] == MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED


def test_pipeline_state_is_active(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "state" / "pipeline_cdp_state.json", "{}")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, "data/state/pipeline_cdp_state.json")["classification"] == MaintenanceClassification.ACTIVE_PIPELINE_STATE


def test_authentication_content_is_not_read_or_reported(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "auth" / "storage_state.json", '{"cookie":"SECRET_TOKEN"}')

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    item = _item(result, "data/auth/storage_state.json")
    serialized = str(result)

    assert item["classification"] == MaintenanceClassification.ACTIVE_AUTHENTICATION
    assert item["sha256_status"] == "HASH_SKIPPED_SENSITIVE"
    assert "SECRET_TOKEN" not in serialized


def test_historical_artifact_cited_in_ledger_is_protected(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "codex_execution_ledger.md", "artefato data/logs/apply.json")
    _write(tmp_path / "data" / "logs" / "apply.json", "{}")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, "data/logs/apply.json")["classification"] == MaintenanceClassification.PROTECTED_REFERENCED


def test_broken_reference_is_reported(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "codex_execution_ledger.md", "artefato data/logs/missing.json")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert "data/logs/missing.json" in result["reference_graph"]["broken_references"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_external_symlink_is_not_followed(tmp_path: Path) -> None:
    external = tmp_path.parent / f"{tmp_path.name}_external"
    external.mkdir()
    _write(external / "secret.txt", "secret")
    try:
        (tmp_path / "linked").symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink privilege unavailable: {exc}")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    paths = {item["relative_path"] for item in result["inventory"]["files"]}
    assert "linked/secret.txt" not in paths


def test_file_changed_during_hash_is_marked_unstable(tmp_path: Path) -> None:
    changing = _write(tmp_path / "data" / "logs" / "changing.log", b"a")

    def mutate(path: Path) -> None:
        if path == changing:
            path.write_bytes(b"changed")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[], before_hash_hook=mutate)

    assert _item(result, "data/logs/changing.log")["classification"] == MaintenanceClassification.HASH_UNSTABLE_FILE


def test_recoverable_space_does_not_double_count_duplicate_group(tmp_path: Path) -> None:
    _write(tmp_path / "outputs" / "a.bin", b"abc")
    _write(tmp_path / "outputs" / "b.bin", b"abc")
    _write(tmp_path / "outputs" / "c.bin", b"abc")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert result["duplicates"]["groups"][0]["potential_recoverable_bytes"] == 6


def test_reports_use_sanitized_paths(tmp_path: Path) -> None:
    _write(tmp_path / "file.txt", "content")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[tmp_path.parent / "external"])

    serialized = str(result)
    assert str(tmp_path) not in serialized
    assert "<PROJECT_ROOT>" in serialized


def test_sensitive_data_is_absent_from_artifacts(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "auth" / "cookies.json", '{"token":"abc123"}')

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert "abc123" not in json_like(result)


def json_like(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_read_only_does_not_call_destructive_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "data" / "temp" / "old.tmp", "x")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("destructive operation called")

    monkeypatch.setattr(os, "remove", forbidden)
    monkeypatch.setattr(os, "unlink", forbidden)

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert result["safety"]["files_deleted"] == 0


def test_all_candidate_actions_are_disabled(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "temp" / "old.tmp", "x")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    for item in result["inventory"]["files"]:
        assert item["delete_now"] is False
        assert item["move_now"] is False
        assert item["archive_now"] is False
        assert item["compress_now"] is False


def test_every_file_has_single_primary_classification(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "readme")
    _write(tmp_path / "data" / "temp" / "old.tmp", "tmp")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    for item in result["inventory"]["files"]:
        assert isinstance(item["classification"], str)
        assert item["classification"]
        assert "secondary_classifications" not in item or item["classification"] not in item["secondary_classifications"]


def test_snapshot_is_memory_only_and_creates_no_file(tmp_path: Path) -> None:
    _write(tmp_path / "data" / "logs" / "existing.json", "{}")

    before = set(tmp_path.rglob("*"))
    snapshot = capture_filesystem_snapshot(tmp_path)
    after = set(tmp_path.rglob("*"))

    assert snapshot["data/logs/existing.json"].relative_path == "data/logs/existing.json"
    assert before == after
    assert not list(tmp_path.rglob("*.tmp"))


def test_canonical_root_uses_script_root_when_git_text_is_mojibake(tmp_path: Path) -> None:
    project = tmp_path / "Automação de projetos" / "gd_neoenergia"
    script = project / "scripts" / "diagnose_project_maintenance.py"
    _project_markers(project)
    _write(script, "script")

    def git_runner(cmd: list[str], cwd: Path):
        stdout = "true\n" if "--is-inside-work-tree" in cmd else str(tmp_path / "AutomaÃ§Ã£o de projetos" / "gd_neoenergia") + "\n"
        return subprocess_completed(stdout=stdout, returncode=0)

    root = resolve_canonical_project_root(script, git_runner=git_runner)

    assert root.is_valid is True
    assert root.resolved_path == project.resolve()
    assert root.git_root_text_status == "UNTRUSTED_ENCODING"


def test_canonical_root_rejects_missing_markers(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "diagnose_project_maintenance.py"
    _write(script, "script")

    root = resolve_canonical_project_root(script, git_runner=lambda _cmd, _cwd: subprocess_completed(stdout="", returncode=1))

    assert root.is_valid is False


def test_final_writer_blocks_logs_outside_root(tmp_path: Path) -> None:
    _project_markers(tmp_path)
    _write(tmp_path / "README.md", "x")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    outside = tmp_path.parent / "outside_logs"
    outside.mkdir()

    with pytest.raises(Stage51DiagnosticError, match="CANONICAL_LOG_DIRECTORY_UNAVAILABLE"):
        save_project_maintenance_artifacts(result, outside, timestamp="20260729T000000Z", writer=FinalArtifactWriter(tmp_path))


def test_final_writer_uses_exact_eight_destinations_without_temporaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_markers(tmp_path)
    _write(tmp_path / "README.md", "x")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("temporary writer called")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    outputs = save_project_maintenance_artifacts(
        result,
        tmp_path / "data" / "logs",
        timestamp="20260729T000001Z",
        writer=FinalArtifactWriter(tmp_path),
    )

    assert len(outputs) == 8
    assert all(path.exists() for path in outputs.values())
    assert not list(tmp_path.rglob("*.tmp"))


def test_final_writer_blocks_existing_destination_without_overwrite(tmp_path: Path) -> None:
    _project_markers(tmp_path)
    _write(tmp_path / "README.md", "x")
    _write(tmp_path / "data" / "logs" / "project_storage_inventory_stage5_1_20260729T000002Z.json", "{}")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    with pytest.raises(Stage51DiagnosticError, match="REPORT_DESTINATION_ALREADY_EXISTS"):
        save_project_maintenance_artifacts(
            result,
            tmp_path / "data" / "logs",
            timestamp="20260729T000002Z",
            writer=FinalArtifactWriter(tmp_path),
        )


def test_bundle_reuses_single_execution_identity_and_cleanup_hash(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "x")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    bundle = build_stage51_artifact_bundle(result, timestamp="20260729T000003Z", execution_id="exec-1")
    rendered = InMemoryArtifactWriter().write(bundle, tmp_path / "data" / "logs")
    cleanup_json = json_loads_bytes(rendered["cleanup_plan_json"])
    cleanup_md = rendered["cleanup_plan_md"].decode("utf-8")

    assert bundle.inventory_payload["metadata"]["execution_id"] == "exec-1"
    assert bundle.cleanup_plan_payload["metadata"]["execution_id"] == "exec-1"
    assert cleanup_json["cleanup_plan_hash"] == bundle.cleanup_plan_hash
    assert f"cleanup_plan_hash: `{bundle.cleanup_plan_hash}`" in cleanup_md


def test_utf8_roundtrip_preserves_accents_and_canonical_dash(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "violação decisão ação padronização Automação")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    result["decision"] = "STAGE5_1_REJECTED — FILESYSTEM_CHANGED_DURING_DIAGNOSTIC"

    bundle = build_stage51_artifact_bundle(result, timestamp="20260729T000004Z")
    rendered = InMemoryArtifactWriter().write(bundle, tmp_path / "data" / "logs")

    joined = b"\n".join(rendered.values()).decode("utf-8", errors="strict")
    assert "STAGE5_1_REJECTED — FILESYSTEM_CHANGED_DURING_DIAGNOSTIC" in joined
    assert "\ufffd" not in joined


def test_mojibake_decision_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "x")
    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    result["decision"] = "STAGE5_1_REJECTED ? FILESYSTEM_CHANGED"

    with pytest.raises(Stage51DiagnosticError, match="UTF8_OR_MOJIBAKE_DEFECT"):
        bundle = build_stage51_artifact_bundle(result, timestamp="20260729T000005Z")
        InMemoryArtifactWriter().write(bundle, tmp_path / "data" / "logs")


def test_test_fixture_reference_does_not_protect_target(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_example.py", "assert 'outputs/a.bin'")
    _write(tmp_path / "outputs" / "a.bin", b"x")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    records = result["reference_graph"]["reference_records"]

    assert any(record["kind"] == ReferenceKind.TEST_FIXTURE_REFERENCE and record["protects_target"] is False for record in records)
    assert _item(result, "outputs/a.bin")["classification"] != MaintenanceClassification.PROTECTED_REFERENCED


def test_documentation_example_directory_placeholder_and_malformed_are_not_authoritative(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "example.md",
        "exemplo apps/desktop/ docs/adr/NNNN-slug-em-kebab-case.md scripts/validate_ data//downloads",
    )

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])
    records = result["reference_graph"]["reference_records"]
    kinds = {record["raw_token"]: record["kind"] for record in records}

    assert kinds["apps/desktop/"] == ReferenceKind.DIRECTORY_REFERENCE
    assert kinds["docs/adr/NNNN-slug-em-kebab-case.md"] == ReferenceKind.PLACEHOLDER_REFERENCE
    assert kinds["scripts/validate_"] == ReferenceKind.MALFORMED_REFERENCE
    assert "apps/desktop" not in result["reference_graph"]["broken_authoritative_references"]


def test_authoritative_broken_reference_is_separated(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "codex_execution_ledger.md", "artefato data/logs/missing.json")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert "data/logs/missing.json" in result["reference_graph"]["broken_authoritative_references"]


def test_cache_cited_by_spec_remains_regenerable_cache(tmp_path: Path) -> None:
    _write(tmp_path / "specs" / "SPEC-007-safe-project-maintenance-and-cleanup.md", ".pytest_cache/v/cache/nodeids")
    _write(tmp_path / ".pytest_cache" / "v" / "cache" / "nodeids", "[]")

    result = run_project_maintenance_inventory(tmp_path, external_roots=[])

    assert _item(result, ".pytest_cache/v/cache/nodeids")["classification"] == MaintenanceClassification.REGENERABLE_CACHE


def test_filesystem_change_outside_allowed_reports_is_detected(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "x")
    before = capture_filesystem_snapshot(tmp_path)
    _write(tmp_path / "unexpected.tmp", "x")
    after = capture_filesystem_snapshot(tmp_path)

    diff = compare_filesystem_snapshots(before, after, allowed_new_files=[])

    assert diff["status"] == "FILESYSTEM_CHANGED_DURING_DIAGNOSTIC"
    assert diff["unexpected_changes"] == ["unexpected.tmp"]


def test_only_eight_report_changes_are_allowed(tmp_path: Path) -> None:
    before = capture_filesystem_snapshot(tmp_path)
    allowed = [
        "data/logs/project_storage_inventory_stage5_1_20260729T000006Z.json",
        "data/logs/project_storage_inventory_stage5_1_20260729T000006Z.md",
        "data/logs/project_cleanup_plan_stage5_1_20260729T000006Z.json",
        "data/logs/project_cleanup_plan_stage5_1_20260729T000006Z.md",
        "data/logs/project_artifact_reference_graph_stage5_1_20260729T000006Z.json",
        "data/logs/project_artifact_reference_graph_stage5_1_20260729T000006Z.md",
        "data/logs/project_duplicate_and_temporary_analysis_stage5_1_20260729T000006Z.json",
        "data/logs/project_duplicate_and_temporary_analysis_stage5_1_20260729T000006Z.md",
    ]
    for rel in allowed:
        _write(tmp_path / rel, "x")
    after = capture_filesystem_snapshot(tmp_path)

    diff = compare_filesystem_snapshots(before, after, allowed_new_files=allowed)

    assert diff["status"] == "OK"


def subprocess_completed(*, stdout: str, returncode: int):
    import subprocess

    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr="")


def json_loads_bytes(data: bytes) -> dict:
    import json

    return json.loads(data.decode("utf-8"))
