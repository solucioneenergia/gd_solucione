from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


class MaintenanceClassification(StrEnum):
    PROTECTED_ABSOLUTE = "PROTECTED_ABSOLUTE"
    PROTECTED_REFERENCED = "PROTECTED_REFERENCED"
    ACTIVE_RUNTIME = "ACTIVE_RUNTIME"
    ACTIVE_PIPELINE_STATE = "ACTIVE_PIPELINE_STATE"
    ACTIVE_AUTHENTICATION = "ACTIVE_AUTHENTICATION"
    ACTIVE_DOWNLOAD = "ACTIVE_DOWNLOAD"
    HISTORICAL_AUDIT = "HISTORICAL_AUDIT"
    HISTORICAL_BACKUP = "HISTORICAL_BACKUP"
    HISTORICAL_REPORT = "HISTORICAL_REPORT"
    REGENERABLE_CACHE = "REGENERABLE_CACHE"
    REGENERABLE_BUILD = "REGENERABLE_BUILD"
    TEMPORARY_ACTIVE = "TEMPORARY_ACTIVE"
    TEMPORARY_ORPHAN_CANDIDATE = "TEMPORARY_ORPHAN_CANDIDATE"
    DUPLICATE_IDENTICAL_REVIEW = "DUPLICATE_IDENTICAL_REVIEW"
    DUPLICATE_DERIVED_REVIEW = "DUPLICATE_DERIVED_REVIEW"
    LOG_RETENTION_REVIEW = "LOG_RETENTION_REVIEW"
    ARCHIVE_CANDIDATE = "ARCHIVE_CANDIDATE"
    DELETE_CANDIDATE_SAFE = "DELETE_CANDIDATE_SAFE"
    UNKNOWN_REVIEW_REQUIRED = "UNKNOWN_REVIEW_REQUIRED"
    EXTERNAL_PROTECTED_ROOT = "EXTERNAL_PROTECTED_ROOT"
    BROKEN_SYMLINK_REVIEW = "BROKEN_SYMLINK_REVIEW"
    UNREADABLE_REVIEW = "UNREADABLE_REVIEW"
    HASH_UNSTABLE_FILE = "HASH_UNSTABLE_FILE"


class ReferenceKind(StrEnum):
    AUTHORITATIVE_FILE_REFERENCE = "AUTHORITATIVE_FILE_REFERENCE"
    AUTHORITATIVE_HASH_REFERENCE = "AUTHORITATIVE_HASH_REFERENCE"
    CODE_RESOURCE_REFERENCE = "CODE_RESOURCE_REFERENCE"
    TEST_FIXTURE_REFERENCE = "TEST_FIXTURE_REFERENCE"
    DOCUMENTATION_EXAMPLE = "DOCUMENTATION_EXAMPLE"
    DIRECTORY_REFERENCE = "DIRECTORY_REFERENCE"
    PLACEHOLDER_REFERENCE = "PLACEHOLDER_REFERENCE"
    MALFORMED_REFERENCE = "MALFORMED_REFERENCE"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


class ReferenceConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Stage51DiagnosticError(RuntimeError):
    """Raised when the Stage 5.1 diagnostic cannot proceed safely."""


@dataclass(frozen=True)
class CanonicalProjectRoot:
    path: Path
    resolved_path: Path
    identity_method: str
    git_root_text: str | None
    git_root_text_status: str
    same_physical_root: bool | None
    unicode_normalization: str
    validation_markers: tuple[str, ...]
    is_valid: bool


@dataclass(frozen=True)
class FilesystemSnapshotEntry:
    relative_path: str
    file_type: str
    size: int
    mtime_ns: int
    is_symlink: bool


@dataclass(frozen=True)
class Stage51ArtifactBundle:
    execution_id: str
    created_at: str
    timestamp: str
    inventory_payload: MappingProxyType
    cleanup_plan_payload: MappingProxyType
    reference_graph_payload: MappingProxyType
    duplicate_analysis_payload: MappingProxyType
    cleanup_plan_hash: str
    artifact_manifest: tuple[dict[str, str], ...]


class ArtifactWriter(Protocol):
    def write(self, bundle: Stage51ArtifactBundle, logs_dir: Path) -> dict[str, Any]:
        ...


SENSITIVE_NAME_MARKERS = (
    "cookie",
    "cookies",
    "token",
    "secret",
    "senha",
    "password",
    "storage_state",
    "local state",
    "session",
)

TEXT_EXTENSIONS = {
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".py",
    ".cfg",
    ".ini",
    ".example",
    ".gitignore",
}

ROOT_VALIDATION_MARKERS = ("AGENTS.md", "automacao_gd", "scripts", "tests", "data")

AUTHORITATIVE_JSON_REFERENCE_KEYS = {
    "artifact_path",
    "relative_path",
    "report_path",
    "report_name",
    "backup_name",
    "backup_path",
    "plan_file",
    "preflight_file",
    "certification_file",
    "source_artifact",
    "output_file",
}

AUTHORITATIVE_REFERENCE_SOURCES = (
    "docs/codex_execution_ledger.md",
    "docs/production_runbook.md",
    "docs/operator_checklist.md",
    "docs/releases/",
    "specs/SPEC-",
    "data/logs/",
)

CONTROLLED_MOJIBAKE_MARKERS = (
    "\ufffd",
    "Viola??o",
    "Decis?o",
    "A??o",
    " ? FILESYSTEM",
    "ÃƒÂ§",
    "ÃƒÂ£",
    "ÃƒÂ¡",
    "Ã§",
    "Ã£",
    "Ã¡",
)

TEMPORARY_SUFFIXES = (
    ".tmp",
    ".temp",
    ".partial",
    ".part",
    ".crdownload",
    ".download",
    ".lock",
    ".lck",
    ".pid",
    ".retry",
    ".staging",
    ".old.tmp",
)

PROTECTED_ROOT_FILES = {
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-build.txt",
    "SECURITY.md",
}

SKIP_RECURSIVE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
}


@dataclass(frozen=True)
class InventoryOptions:
    official_workbook_path: Path | None = None
    official_workbook_sha: str | None = None
    external_roots: tuple[Path, ...] = ()
    max_hash_bytes: int = 100 * 1024 * 1024
    now_epoch: float | None = None


def resolve_canonical_project_root(
    script_file: Path | str,
    *,
    git_runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None = None,
) -> CanonicalProjectRoot:
    """Resolve a single physical project root without trusting mojibake Git text as a destination."""

    script_root = Path(script_file).resolve().parents[1]
    markers = tuple(marker for marker in ROOT_VALIDATION_MARKERS if (script_root / marker).exists())
    if set(markers) != set(ROOT_VALIDATION_MARKERS):
        return CanonicalProjectRoot(
            path=script_root,
            resolved_path=script_root,
            identity_method="INVALID_MARKERS",
            git_root_text=None,
            git_root_text_status="NOT_CHECKED",
            same_physical_root=None,
            unicode_normalization="NFC",
            validation_markers=markers,
            is_valid=False,
        )
    runner = git_runner or _run_git_for_root
    inside = runner(["git", "-C", str(script_root), "rev-parse", "--is-inside-work-tree"], script_root)
    git_root_text: str | None = None
    git_status = "UNAVAILABLE"
    same: bool | None = None
    if inside.returncode == 0 and inside.stdout.strip().lower() == "true":
        completed = runner(["git", "-C", str(script_root), "rev-parse", "--show-toplevel"], script_root)
        if completed.returncode == 0:
            git_root_text = completed.stdout.strip()
            git_status = _git_root_text_status(git_root_text)
            if git_status == "OK":
                try:
                    git_path = Path(git_root_text).resolve(strict=True)
                    same = _same_physical_path(script_root, git_path)
                    if same is False and _normcase_nfc(script_root) != _normcase_nfc(git_path):
                        raise Stage51DiagnosticError("STAGE5_1_ROOT_VALIDATION_FAILED")
                except (OSError, RuntimeError):
                    git_status = "UNTRUSTED_ENCODING"
        else:
            git_status = "UNAVAILABLE"
    elif inside.returncode == 0:
        git_status = "NOT_INSIDE_WORKTREE"
    return CanonicalProjectRoot(
        path=script_root,
        resolved_path=script_root,
        identity_method="SCRIPT_ROOT_VALIDATED_BY_GIT" if git_status == "OK" else "SCRIPT_ROOT_GIT_TEXT_UNTRUSTED",
        git_root_text=git_root_text,
        git_root_text_status=git_status,
        same_physical_root=same,
        unicode_normalization="NFC",
        validation_markers=markers,
        is_valid=True,
    )


def validate_canonical_log_directory(root: Path | CanonicalProjectRoot, logs_dir: Path | str) -> Path:
    canonical_root = root.resolved_path if isinstance(root, CanonicalProjectRoot) else Path(root).resolve()
    logs = Path(logs_dir)
    resolved_logs = logs.resolve(strict=True)
    expected = (canonical_root / "data" / "logs").resolve(strict=True)
    if logs.name != "logs" or logs.parent.name != "data":
        raise Stage51DiagnosticError("STAGE5_1_BLOCKED — CANONICAL_LOG_DIRECTORY_UNAVAILABLE")
    if logs.is_symlink() or resolved_logs != expected:
        raise Stage51DiagnosticError("STAGE5_1_BLOCKED — CANONICAL_LOG_DIRECTORY_UNAVAILABLE")
    try:
        resolved_logs.relative_to(canonical_root)
    except ValueError as exc:
        raise Stage51DiagnosticError("STAGE5_1_BLOCKED — CANONICAL_LOG_DIRECTORY_UNAVAILABLE") from exc
    return resolved_logs


def capture_filesystem_snapshot(root: Path | str, exclusions: Iterable[str] | None = None) -> dict[str, FilesystemSnapshotEntry]:
    """Capture filesystem state in memory only."""

    base = Path(root).resolve()
    excluded = {item.replace("\\", "/").rstrip("/") for item in (exclusions or ())}
    snapshot: dict[str, FilesystemSnapshotEntry] = {}
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_RECURSIVE_DIRS]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            rel = _relative(base, path)
            if any(rel == ex or rel.startswith(f"{ex}/") for ex in excluded):
                continue
            stat = _safe_stat(path)
            if stat is None:
                continue
            snapshot[rel] = FilesystemSnapshotEntry(
                relative_path=rel,
                file_type="symlink" if path.is_symlink() else "file",
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                is_symlink=path.is_symlink(),
            )
    return dict(sorted(snapshot.items()))


def compare_filesystem_snapshots(
    before: dict[str, FilesystemSnapshotEntry],
    after: dict[str, FilesystemSnapshotEntry],
    *,
    allowed_new_files: Iterable[str] = (),
) -> dict[str, Any]:
    allowed = set(allowed_new_files)
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = sorted(
        key
        for key in before_keys & after_keys
        if before[key].size != after[key].size or before[key].mtime_ns != after[key].mtime_ns or before[key].file_type != after[key].file_type
    )
    unexpected_added = [path for path in added if path not in allowed]
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "allowed_new_files": sorted(allowed),
        "unexpected_changes": sorted(unexpected_added + removed + changed),
        "status": "OK" if not unexpected_added and not removed and not changed else "FILESYSTEM_CHANGED_DURING_DIAGNOSTIC",
    }


def run_project_maintenance_inventory(
    project_root: Path | str,
    *,
    official_workbook_path: Path | str | None = None,
    official_workbook_sha: str | None = None,
    external_roots: Iterable[Path | str] | None = None,
    git_tracked: set[str] | None = None,
    git_untracked: set[str] | None = None,
    now_epoch: float | None = None,
    max_hash_bytes: int = 100 * 1024 * 1024,
    before_hash_hook: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    """Inventory project storage without deleting, moving, copying, compressing, or saving files."""

    root = Path(project_root).resolve()
    options = InventoryOptions(
        official_workbook_path=Path(official_workbook_path).resolve() if official_workbook_path else None,
        official_workbook_sha=official_workbook_sha,
        external_roots=tuple(Path(p) for p in (external_roots or ())),
        max_hash_bytes=max_hash_bytes,
        now_epoch=now_epoch,
    )
    tracked = git_tracked if git_tracked is not None else _git_file_set(root, ["git", "ls-files"])
    untracked = (
        git_untracked
        if git_untracked is not None
        else _git_file_set(root, ["git", "ls-files", "--others", "--exclude-standard"])
    )
    ignored = _git_ignored(root)
    modified = _git_status(root)
    files = _scan_files(root)
    references = _build_reference_graph(root, files)
    inventory_files: list[dict[str, Any]] = []
    for path in files:
        item = _inventory_file(
            root,
            path,
            options,
            tracked=tracked,
            untracked=untracked,
            ignored=ignored,
            modified=modified,
            references=references,
            before_hash_hook=before_hash_hook,
        )
        inventory_files.append(item)
    duplicates = _duplicate_groups(inventory_files)
    _assign_duplicate_groups(inventory_files, duplicates)
    _downgrade_duplicate_reviews(inventory_files)
    metrics = _metrics(inventory_files, duplicates, references, options)
    safety = {
        "read_only": True,
        "files_deleted": 0,
        "files_moved": 0,
        "files_archived": 0,
        "files_compressed": 0,
        "directories_deleted": 0,
        "permissions_changed": 0,
        "timestamps_changed": 0,
        "workbook_save": 0,
        "os_replace": 0,
        "portal_access": 0,
        "pdf_downloads": 0,
        "cleanup_apply": False,
        "cleanup_delete": False,
        "cleanup_move": False,
        "cleanup_compress": False,
    }
    external = [_external_root(root_path) for root_path in options.external_roots]
    official = _official_workbook_summary(options)
    review = _review(metrics, inventory_files, references, official)
    decision = _decision(review, metrics)
    return {
        "metadata": {
            "artifact_type": "project_maintenance_inventory_stage5_1",
            "stage": "5.1",
            "mode": "READ_ONLY",
            "created_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "project_root": "<PROJECT_ROOT>",
        },
        "scope": {
            "project_root_alias": "<PROJECT_ROOT>",
            "recursive_scan": True,
            "external_roots_recursive_scan": False,
            "skipped_recursive_directories": sorted(SKIP_RECURSIVE_DIRS),
        },
        "baseline": {
            "official_workbook": official,
            "official_workbook_sha_expected": options.official_workbook_sha,
        },
        "external_roots": external,
        "inventory": {
            "files": inventory_files,
            "directories_scanned": len({item["parent_directory"] for item in inventory_files}),
        },
        "reference_graph": references,
        "duplicates": {"groups": duplicates},
        "cleanup_plan": _cleanup_plan(inventory_files, duplicates, references),
        "metrics": metrics,
        "safety": safety,
        "review": review,
        "decision": decision,
    }


def build_stage51_artifact_bundle(
    result: dict[str, Any],
    *,
    timestamp: str | None = None,
    execution_id: str | None = None,
) -> Stage51ArtifactBundle:
    timestamp = timestamp or result["metadata"]["created_at"]
    execution_id = execution_id or hashlib.sha256(f"stage5.1:{timestamp}".encode("utf-8")).hexdigest()
    base_metadata = {
        **result["metadata"],
        "execution_id": execution_id,
        "timestamp": timestamp,
        "created_at": result["metadata"]["created_at"],
    }
    inventory = _freeze_payload(_normalize_text_payload({**_inventory_payload(result), "metadata": base_metadata}))
    plan = _cleanup_plan_payload(result)
    plan = {**plan, "metadata": {**base_metadata, "artifact_type": "project_cleanup_plan_stage5_1"}}
    cleanup_hash = _hash_without(plan, "cleanup_plan_hash")
    plan["cleanup_plan_hash"] = cleanup_hash
    plan = _freeze_payload(_normalize_text_payload(plan))
    reference_graph = _freeze_payload(
        _normalize_text_payload({**_reference_graph_payload(result), "metadata": {**base_metadata, "artifact_type": "project_artifact_reference_graph_stage5_1"}})
    )
    duplicate = _freeze_payload(
        _normalize_text_payload({**_duplicate_payload(result), "metadata": {**base_metadata, "artifact_type": "project_duplicate_and_temporary_analysis_stage5_1"}})
    )
    manifest = (
        {"key": "inventory_json", "file_name": f"project_storage_inventory_stage5_1_{timestamp}.json"},
        {"key": "inventory_md", "file_name": f"project_storage_inventory_stage5_1_{timestamp}.md"},
        {"key": "cleanup_plan_json", "file_name": f"project_cleanup_plan_stage5_1_{timestamp}.json"},
        {"key": "cleanup_plan_md", "file_name": f"project_cleanup_plan_stage5_1_{timestamp}.md"},
        {"key": "reference_graph_json", "file_name": f"project_artifact_reference_graph_stage5_1_{timestamp}.json"},
        {"key": "reference_graph_md", "file_name": f"project_artifact_reference_graph_stage5_1_{timestamp}.md"},
        {"key": "duplicate_json", "file_name": f"project_duplicate_and_temporary_analysis_stage5_1_{timestamp}.json"},
        {"key": "duplicate_md", "file_name": f"project_duplicate_and_temporary_analysis_stage5_1_{timestamp}.md"},
    )
    bundle = Stage51ArtifactBundle(
        execution_id=execution_id,
        created_at=str(base_metadata["created_at"]),
        timestamp=timestamp,
        inventory_payload=inventory,
        cleanup_plan_payload=plan,
        reference_graph_payload=reference_graph,
        duplicate_analysis_payload=duplicate,
        cleanup_plan_hash=cleanup_hash,
        artifact_manifest=manifest,
    )
    validate_artifact_bundle_consistency(bundle)
    return bundle


def render_stage51_artifact_bundle(bundle: Stage51ArtifactBundle) -> dict[str, bytes]:
    rendered = {
        "inventory_json": _json_bytes(bundle.inventory_payload),
        "inventory_md": _text_bytes(_inventory_md(dict(bundle.inventory_payload))),
        "cleanup_plan_json": _json_bytes(bundle.cleanup_plan_payload),
        "cleanup_plan_md": _text_bytes(_cleanup_plan_md(dict(bundle.cleanup_plan_payload))),
        "reference_graph_json": _json_bytes(bundle.reference_graph_payload),
        "reference_graph_md": _text_bytes(_reference_graph_md(dict(bundle.reference_graph_payload))),
        "duplicate_json": _json_bytes(bundle.duplicate_analysis_payload),
        "duplicate_md": _text_bytes(_duplicate_md(dict(bundle.duplicate_analysis_payload))),
    }
    for content in rendered.values():
        _validate_utf8_bytes(content)
    return rendered


def save_project_maintenance_artifacts(
    result: dict[str, Any],
    logs_dir: Path | str,
    *,
    timestamp: str | None = None,
    writer: ArtifactWriter | None = None,
) -> dict[str, Any]:
    bundle = build_stage51_artifact_bundle(result, timestamp=timestamp)
    artifact_writer = writer or FinalArtifactWriter()
    return artifact_writer.write(bundle, Path(logs_dir))


class InMemoryArtifactWriter:
    def write(self, bundle: Stage51ArtifactBundle, logs_dir: Path) -> dict[str, bytes]:
        del logs_dir
        return render_stage51_artifact_bundle(bundle)


class FinalArtifactWriter:
    def __init__(self, project_root: Path | CanonicalProjectRoot | None = None) -> None:
        self._project_root = project_root

    def write(self, bundle: Stage51ArtifactBundle, logs_dir: Path) -> dict[str, Path]:
        logs = validate_canonical_log_directory(self._project_root or Path.cwd(), logs_dir)
        rendered = render_stage51_artifact_bundle(bundle)
        outputs = {item["key"]: logs / item["file_name"] for item in bundle.artifact_manifest}
        if set(outputs) != set(rendered):
            raise Stage51DiagnosticError("STAGE5_1_BLOCKED — ARTIFACT_MANIFEST_MISMATCH")
        existing = [path for path in outputs.values() if path.exists()]
        if existing:
            raise Stage51DiagnosticError("STAGE5_1_BLOCKED — REPORT_DESTINATION_ALREADY_EXISTS")
        for key, path in outputs.items():
            _write_final_exclusive(path, rendered[key])
        return outputs


def _scan_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [
            d
            for d in dirs
            if d not in SKIP_RECURSIVE_DIRS
            and not (Path(current) / d).is_symlink()
            and not _is_external_profile_dir(Path(current) / d)
        ]
        for name in files:
            path = current_path / name
            try:
                if path.is_symlink():
                    result.append(path)
                elif path.is_file():
                    result.append(path)
            except OSError:
                result.append(path)
    return sorted(result, key=lambda p: _relative(root, p))


def _run_git_for_root(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=30)


def _git_root_text_status(value: str) -> str:
    if not value:
        return "EMPTY"
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        return "NORMALIZED_FOR_COMPARISON"
    if _has_mojibake(value):
        return "UNTRUSTED_ENCODING"
    return "OK"


def _same_physical_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _normcase_nfc(path: Path) -> str:
    return os.path.normcase(unicodedata.normalize("NFC", str(path)))


def _freeze_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze_payload(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_payload(v) for v in value)
    return value


def _normalize_text_payload(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {k: _normalize_text_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_text_payload(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_normalize_text_payload(v) for v in value)
    return value


def _thaw_payload(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {k: _thaw_payload(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw_payload(v) for v in value]
    return value


def _json_bytes(payload: Any) -> bytes:
    text = json.dumps(_thaw_payload(payload), ensure_ascii=False, sort_keys=True, indent=2)
    text = f"{text}\n"
    data = text.encode("utf-8", errors="strict")
    _validate_utf8_bytes(data)
    return data


def _text_bytes(text: str) -> bytes:
    normalized = unicodedata.normalize("NFC", text)
    data = normalized.encode("utf-8", errors="strict")
    _validate_utf8_bytes(data)
    return data


def _validate_utf8_bytes(data: bytes) -> None:
    text = data.decode("utf-8", errors="strict")
    text.encode("utf-8", errors="strict")
    controlled = _controlled_text_for_mojibake_scan(text)
    if _has_mojibake(controlled):
        raise Stage51DiagnosticError("STAGE5_1_REJECTED — UTF8_OR_MOJIBAKE_DEFECT")


def _controlled_text_for_mojibake_scan(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if any(marker in line for marker in ("decision", "Decis", "Viol", "Aç", "acao", "action", "STAGE5_1")):
            lines.append(line)
    return "\n".join(lines)


def _has_mojibake(text: str) -> bool:
    return any(marker in text for marker in CONTROLLED_MOJIBAKE_MARKERS)


def _write_final_exclusive(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def validate_artifact_bundle_consistency(bundle: Stage51ArtifactBundle) -> None:
    payloads = (
        bundle.inventory_payload,
        bundle.cleanup_plan_payload,
        bundle.reference_graph_payload,
        bundle.duplicate_analysis_payload,
    )
    decisions = {payload["decision"] for payload in payloads if "decision" in payload}
    if len(decisions) != 1:
        raise Stage51DiagnosticError("STAGE5_1_REJECTED — ARTIFACT_BUNDLE_INCONSISTENT")
    metadata = [payload["metadata"] for payload in payloads]
    if len({item["execution_id"] for item in metadata}) != 1 or len({item["timestamp"] for item in metadata}) != 1:
        raise Stage51DiagnosticError("STAGE5_1_REJECTED — ARTIFACT_BUNDLE_INCONSISTENT")
    plan = _thaw_payload(bundle.cleanup_plan_payload)
    reproduced = _hash_without(plan, "cleanup_plan_hash")
    if reproduced != bundle.cleanup_plan_hash or plan.get("cleanup_plan_hash") != bundle.cleanup_plan_hash:
        raise Stage51DiagnosticError("STAGE5_1_REJECTED — ARTIFACT_HASH_INCONSISTENT")


def _inventory_file(
    root: Path,
    path: Path,
    options: InventoryOptions,
    *,
    tracked: set[str],
    untracked: set[str],
    ignored: set[str],
    modified: set[str],
    references: dict[str, Any],
    before_hash_hook: Callable[[Path], None] | None,
) -> dict[str, Any]:
    rel = _relative(root, path)
    stat = _safe_stat(path)
    readable = os.access(path, os.R_OK)
    writable = os.access(path, os.W_OK)
    is_symlink = path.is_symlink()
    sha_status = "HASH_NOT_APPLICABLE"
    digest = None
    classification = MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED
    reasons: list[str] = []
    if is_symlink:
        target = _sanitize_path(os.readlink(path))
        classification = (
            MaintenanceClassification.BROKEN_SYMLINK_REVIEW
            if not path.exists()
            else MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED
        )
        sha_status = "HASH_SKIPPED_SYMLINK"
    else:
        target = None
        if not readable:
            classification = MaintenanceClassification.UNREADABLE_REVIEW
            sha_status = "HASH_SKIPPED_UNREADABLE"
        elif _is_sensitive(rel):
            sha_status = "HASH_SKIPPED_SENSITIVE"
        elif stat and stat.st_size > options.max_hash_bytes:
            sha_status = "HASH_SKIPPED_SIZE"
        elif path.is_file():
            hash_result = _hash_file_stable(path, before_hash_hook=before_hash_hook)
            sha_status = hash_result["status"]
            digest = hash_result.get("sha256")
            if sha_status == "HASH_UNSTABLE_FILE":
                classification = MaintenanceClassification.HASH_UNSTABLE_FILE
    referenced_by = references["referenced_by"].get(rel, [])
    references_out = references["references_by_file"].get(rel, [])
    if classification not in {
        MaintenanceClassification.UNREADABLE_REVIEW,
        MaintenanceClassification.BROKEN_SYMLINK_REVIEW,
        MaintenanceClassification.HASH_UNSTABLE_FILE,
    }:
        classification, reasons = _classify(rel, path, options, referenced_by)
    if referenced_by and classification not in {
        MaintenanceClassification.PROTECTED_ABSOLUTE,
        MaintenanceClassification.ACTIVE_AUTHENTICATION,
        MaintenanceClassification.ACTIVE_PIPELINE_STATE,
    }:
        classification = MaintenanceClassification.PROTECTED_REFERENCED
        reasons.append("referenced_by_artifact")
    age_days = None
    if stat:
        now = options.now_epoch if options.now_epoch is not None else datetime.now().timestamp()
        age_days = max(0, int((now - stat.st_mtime) // 86400))
    return {
        "relative_path": rel,
        "file_name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": stat.st_size if stat else 0,
        "created_at": _iso(stat.st_ctime) if stat else None,
        "modified_at": _iso(stat.st_mtime) if stat else None,
        "age_days": age_days,
        "age_bucket": _age_bucket(age_days),
        "is_file": path.is_file(),
        "is_directory": False,
        "is_symlink": is_symlink,
        "symlink_target_sanitized": target,
        "git_tracked": rel in tracked,
        "git_ignored": rel in ignored,
        "git_untracked": rel in untracked,
        "git_modified": rel in modified,
        "readable": readable,
        "writable": writable,
        "sha256_status": sha_status,
        "sha256": digest,
        "mime_family": _mime_family(path),
        "parent_directory": _posix(Path(rel).parent),
        "classification": str(classification),
        "classification_reason": ";".join(dict.fromkeys(reasons)) or str(classification),
        "protection_reasons": _protection_reasons(classification, rel, referenced_by),
        "references_in": referenced_by,
        "references_out": references_out,
        "referenced_by_count": len(referenced_by),
        "references_count": len(references_out),
        "duplicate_group_id": None,
        "candidate_action": _candidate_action(classification),
        "delete_now": False,
        "move_now": False,
        "archive_now": False,
        "compress_now": False,
        "review_required": _review_required(classification),
        "risk": _risk(classification),
        "evidence": _evidence(classification, rel, referenced_by),
    }


def _classify(rel: str, path: Path, options: InventoryOptions, referenced_by: list[str]) -> tuple[MaintenanceClassification, list[str]]:
    lower = rel.lower()
    name = path.name.lower()
    parts = set(Path(rel).parts)
    if options.official_workbook_path and _same_path(path, options.official_workbook_path):
        return MaintenanceClassification.PROTECTED_ABSOLUTE, ["official_workbook"]
    if rel in PROTECTED_ROOT_FILES or rel.startswith(("automacao_gd/", "apps/", "scripts/", "tests/", "specs/", "docs/")):
        return MaintenanceClassification.PROTECTED_ABSOLUTE, ["source_or_documentation"]
    if _is_sensitive(rel):
        return MaintenanceClassification.ACTIVE_AUTHENTICATION, ["authentication_or_secret_path"]
    if rel.startswith("data/state/") or "state" in parts and name.endswith(".json"):
        return MaintenanceClassification.ACTIVE_PIPELINE_STATE, ["pipeline_resume_state"]
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        if "backup" in lower or "pre_" in lower:
            return MaintenanceClassification.HISTORICAL_BACKUP, ["workbook_backup"]
        return MaintenanceClassification.PROTECTED_ABSOLUTE, ["workbook"]
    if name.endswith(".pdf"):
        return MaintenanceClassification.PROTECTED_ABSOLUTE, ["pdf_document"]
    if _is_cache(rel, name):
        return MaintenanceClassification.REGENERABLE_CACHE, ["regenerable_cache"]
    if _is_build(rel):
        return MaintenanceClassification.REGENERABLE_BUILD, ["regenerable_build"]
    if _is_temporary(rel, name):
        return MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE, ["recognized_temporary_pattern"]
    if rel.startswith("data/downloads/"):
        return MaintenanceClassification.ACTIVE_DOWNLOAD, ["internal_download"]
    if rel.startswith("data/logs/"):
        if any(marker in name for marker in ("apply", "audit", "plan", "preflight", "certification", "reconciliation")):
            return MaintenanceClassification.HISTORICAL_AUDIT, ["audit_report"]
        return MaintenanceClassification.LOG_RETENTION_REVIEW, ["operational_log"]
    if referenced_by:
        return MaintenanceClassification.PROTECTED_REFERENCED, ["referenced_by_artifact"]
    return MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED, ["no_safe_cleanup_evidence"]


def _build_reference_graph(root: Path, files: list[Path]) -> dict[str, Any]:
    rels = {_relative(root, p) for p in files}
    referenced_by: dict[str, list[str]] = {rel: [] for rel in rels}
    references_by_file: dict[str, list[str]] = {}
    hash_references: list[dict[str, str]] = []
    reference_records: list[dict[str, Any]] = []
    broken_authoritative: set[str] = set()
    for path in files:
        rel = _relative(root, path)
        if not _safe_to_read_text(rel, path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        found: set[str] = set()
        for candidate in _extract_path_like_tokens(text):
            record = _reference_record(source_file=rel, raw_token=candidate, existing_files=rels)
            reference_records.append(record)
            normalized = record["normalized_target"]
            if record["protects_target"] and normalized in rels:
                found.add(normalized)
                referenced_by[normalized].append(rel)
            if record["broken_authoritative"]:
                broken_authoritative.add(normalized)
        for digest in re.findall(r"\b[a-fA-F0-9]{64}\b", text):
            hash_record = {
                "source_file": rel,
                "raw_token": digest,
                "normalized_target": digest.lower(),
                "kind": ReferenceKind.AUTHORITATIVE_HASH_REFERENCE if _is_authoritative_source(rel) else ReferenceKind.DOCUMENTATION_EXAMPLE,
                "confidence": ReferenceConfidence.HIGH if _is_authoritative_source(rel) else ReferenceConfidence.LOW,
                "exists": None,
                "protects_target": False,
                "broken_authoritative": False,
                "reason": "sha256_token",
            }
            reference_records.append(hash_record)
            hash_references.append({"source": rel, "sha256": digest.lower(), "kind": str(hash_record["kind"])})
        if found:
            references_by_file[rel] = sorted(found)
    counts = _reference_counts(reference_records)
    return {
        "files_referenced": sorted(k for k, v in referenced_by.items() if v),
        "referenced_by": {k: sorted(set(v)) for k, v in referenced_by.items() if v},
        "references_by_file": {k: sorted(v) for k, v in references_by_file.items()},
        "reference_records": reference_records,
        "broken_references": sorted(broken_authoritative - rels),
        "broken_authoritative_references": sorted(broken_authoritative - rels),
        "broken_code_resource_references": sorted(
            r["normalized_target"] for r in reference_records if r["kind"] == ReferenceKind.CODE_RESOURCE_REFERENCE and not r["exists"]
        ),
        "documentation_examples": [r for r in reference_records if r["kind"] == ReferenceKind.DOCUMENTATION_EXAMPLE],
        "test_fixture_references": [r for r in reference_records if r["kind"] == ReferenceKind.TEST_FIXTURE_REFERENCE],
        "directory_references": [r for r in reference_records if r["kind"] == ReferenceKind.DIRECTORY_REFERENCE],
        "malformed_references": [r for r in reference_records if r["kind"] == ReferenceKind.MALFORMED_REFERENCE],
        "placeholder_references": [r for r in reference_records if r["kind"] == ReferenceKind.PLACEHOLDER_REFERENCE],
        "reference_kind_counts": counts,
        "references_to_external_roots": [],
        "hash_references": hash_references,
        "plan_references": [r for r in sorted(rels) if "plan" in Path(r).name.lower()],
        "preflight_references": [r for r in sorted(rels) if "preflight" in Path(r).name.lower()],
        "certification_references": [r for r in sorted(rels) if "certification" in Path(r).name.lower()],
        "backup_references": [r for r in sorted(rels) if "backup" in Path(r).name.lower() or "pre_" in Path(r).name.lower()],
        "unreferenced_final_looking_files": [],
    }


def _duplicate_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for item in items:
        if item.get("sha256") and item.get("size_bytes") is not None:
            buckets.setdefault((item["sha256"], item["size_bytes"]), []).append(item)
    groups = []
    for index, ((digest, size), files) in enumerate(sorted(buckets.items()), start=1):
        if len(files) < 2:
            continue
        protected = [f for f in files if str(f["classification"]).startswith("PROTECTED") or str(f["classification"]).startswith("ACTIVE")]
        recoverable = 0 if protected else size * (len(files) - 1)
        groups.append(
            {
                "duplicate_group_id": f"DUP-{index:04d}",
                "sha256": digest,
                "size_bytes": size,
                "files": [f["relative_path"] for f in files],
                "git_status": {f["relative_path"]: _git_status_label(f) for f in files},
                "reference_count_by_file": {f["relative_path"]: f["referenced_by_count"] for f in files},
                "oldest_file": min(files, key=lambda f: f.get("modified_at") or "")["relative_path"],
                "newest_file": max(files, key=lambda f: f.get("modified_at") or "")["relative_path"],
                "canonical_candidate": None,
                "potential_recoverable_bytes": recoverable,
                "classification": MaintenanceClassification.DUPLICATE_IDENTICAL_REVIEW,
                "review_required": True,
            }
        )
    return groups


def _assign_duplicate_groups(items: list[dict[str, Any]], groups: list[dict[str, Any]]) -> None:
    by_path = {path: group["duplicate_group_id"] for group in groups for path in group["files"]}
    for item in items:
        item["duplicate_group_id"] = by_path.get(item["relative_path"])


def _downgrade_duplicate_reviews(items: list[dict[str, Any]]) -> None:
    for item in items:
        if item.get("duplicate_group_id") and item["classification"] == MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED:
            item["classification"] = MaintenanceClassification.DUPLICATE_IDENTICAL_REVIEW
            item["candidate_action"] = "REVIEW_DUPLICATE"
            item["review_required"] = True


def _metrics(items: list[dict[str, Any]], duplicates: list[dict[str, Any]], references: dict[str, Any], options: InventoryOptions) -> dict[str, Any]:
    by_class: dict[str, int] = {}
    for item in items:
        by_class[item["classification"]] = by_class.get(item["classification"], 0) + 1
    total_size = sum(int(item["size_bytes"] or 0) for item in items)
    protected = [i for i in items if str(i["classification"]).startswith("PROTECTED") or str(i["classification"]).startswith("ACTIVE") or str(i["classification"]).startswith("HISTORICAL")]
    low_risk = sum(int(i["size_bytes"] or 0) for i in items if i["classification"] in {MaintenanceClassification.REGENERABLE_CACHE, MaintenanceClassification.REGENERABLE_BUILD})
    review_recoverable = sum(int(g["potential_recoverable_bytes"]) for g in duplicates)
    return {
        "directories_scanned": len({i["parent_directory"] for i in items}),
        "files_scanned": len(items),
        "files_hashed": len([i for i in items if i["sha256_status"] == "HASHED"]),
        "hash_skipped": len([i for i in items if str(i["sha256_status"]).startswith("HASH_SKIPPED")]),
        "hash_unstable": len([i for i in items if i["sha256_status"] == "HASH_UNSTABLE_FILE"]),
        "total_size_bytes": total_size,
        "protected_files": len(protected),
        "protected_size_bytes": sum(int(i["size_bytes"] or 0) for i in protected),
        "referenced_files": len(references.get("files_referenced", [])),
        "historical_files": len([i for i in items if str(i["classification"]).startswith("HISTORICAL")]),
        "active_runtime_files": by_class.get(MaintenanceClassification.ACTIVE_RUNTIME, 0),
        "authentication_files": by_class.get(MaintenanceClassification.ACTIVE_AUTHENTICATION, 0),
        "pipeline_state_files": by_class.get(MaintenanceClassification.ACTIVE_PIPELINE_STATE, 0),
        "cache_files": by_class.get(MaintenanceClassification.REGENERABLE_CACHE, 0),
        "build_files": by_class.get(MaintenanceClassification.REGENERABLE_BUILD, 0),
        "temporary_files": by_class.get(MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE, 0) + by_class.get(MaintenanceClassification.TEMPORARY_ACTIVE, 0),
        "temporary_orphan_candidates": by_class.get(MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE, 0),
        "duplicate_groups": len(duplicates),
        "duplicate_files": sum(len(g["files"]) for g in duplicates),
        "log_files": len([i for i in items if i["relative_path"].startswith("data/logs/")]),
        "report_files": len([i for i in items if i["extension"] in {".json", ".md"} and "log" in i["relative_path"]]),
        "pdf_files": len([i for i in items if i["extension"] == ".pdf"]),
        "workbook_files": len([i for i in items if i["extension"] in {".xlsx", ".xlsm", ".xls"}]),
        "backup_files": len([i for i in items if "backup" in i["file_name"].lower() or "pre_" in i["file_name"].lower()]),
        "unknown_review_files": by_class.get(MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED, 0),
        "delete_candidates_safe": by_class.get(MaintenanceClassification.DELETE_CANDIDATE_SAFE, 0),
        "archive_candidates": by_class.get(MaintenanceClassification.ARCHIVE_CANDIDATE, 0),
        "potential_recoverable_bytes_low_risk": low_risk,
        "potential_recoverable_bytes_review_required": review_recoverable,
        "external_roots_registered": len(options.external_roots),
        "classification_counts": by_class,
        "errors": [],
        "warnings": [],
    }


def _cleanup_plan(items: list[dict[str, Any]], duplicates: list[dict[str, Any]], references: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        {
            "candidate_id": f"CAND-{index:05d}",
            "relative_path": item["relative_path"],
            "classification": item["classification"],
            "candidate_action": item["candidate_action"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
            "age_days": item["age_days"],
            "reference_count": item["referenced_by_count"],
            "git_status": _git_status_label(item),
            "protection_reasons": item["protection_reasons"],
            "risk": item["risk"],
            "evidence": item["evidence"],
            "review_required": item["review_required"],
            "delete_now": False,
            "move_now": False,
            "archive_now": False,
            "compress_now": False,
        }
        for index, item in enumerate(items, start=1)
        if item["classification"]
        in {
            MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE,
            MaintenanceClassification.REGENERABLE_CACHE,
            MaintenanceClassification.REGENERABLE_BUILD,
            MaintenanceClassification.DUPLICATE_IDENTICAL_REVIEW,
            MaintenanceClassification.LOG_RETENTION_REVIEW,
            MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED,
        }
    ]
    return {
        "candidates": candidates,
        "duplicate_groups": duplicates,
        "reference_graph_summary": {
            "files_referenced": len(references.get("files_referenced", [])),
            "broken_references": len(references.get("broken_references", [])),
        },
    }


def _inventory_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": result["metadata"],
        "scope": result["scope"],
        "baseline": result["baseline"],
        "external_roots": result["external_roots"],
        "inventory_summary": result["metrics"],
        "files": result["inventory"]["files"],
        "safety": result["safety"],
        "decision": result["decision"],
    }


def _cleanup_plan_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {**result["metadata"], "artifact_type": "project_cleanup_plan_stage5_1"},
        "scope": result["scope"],
        "baseline": result["baseline"],
        "inventory_summary": result["metrics"],
        "classification_policy": "SPEC-007",
        "protected_roots": result["external_roots"],
        "protected_files": [i for i in result["inventory"]["files"] if i["protection_reasons"]],
        "reference_graph_summary": result["cleanup_plan"]["reference_graph_summary"],
        "duplicate_groups": result["duplicates"]["groups"],
        "temporary_candidates": _items_by_class(result, MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE),
        "cache_candidates": _items_by_class(result, MaintenanceClassification.REGENERABLE_CACHE),
        "log_retention_candidates": _items_by_class(result, MaintenanceClassification.LOG_RETENTION_REVIEW),
        "archive_candidates": _items_by_class(result, MaintenanceClassification.ARCHIVE_CANDIDATE),
        "delete_candidates": _items_by_class(result, MaintenanceClassification.DELETE_CANDIDATE_SAFE),
        "unknown_review": _items_by_class(result, MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED),
        "candidates": result["cleanup_plan"]["candidates"],
        "recoverable_space": {
            "recoverable_low_risk": result["metrics"]["potential_recoverable_bytes_low_risk"],
            "recoverable_after_review": result["metrics"]["potential_recoverable_bytes_review_required"],
            "not_recoverable_protected": result["metrics"]["protected_size_bytes"],
            "unknown": sum(i["size_bytes"] for i in _items_by_class(result, MaintenanceClassification.UNKNOWN_REVIEW_REQUIRED)),
        },
        "safety": result["safety"],
        "review": result["review"],
        "decision": result["decision"],
    }


def _reference_graph_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {**result["metadata"], "artifact_type": "project_artifact_reference_graph_stage5_1"},
        **result["reference_graph"],
        "summary": result["cleanup_plan"]["reference_graph_summary"],
        "decision": result["decision"],
    }


def _duplicate_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {**result["metadata"], "artifact_type": "project_duplicate_and_temporary_analysis_stage5_1"},
        "identical_duplicates": result["duplicates"]["groups"],
        "derived_duplicates": [],
        "temporary_active": _items_by_class(result, MaintenanceClassification.TEMPORARY_ACTIVE),
        "temporary_orphan_candidates": _items_by_class(result, MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE),
        "regenerable_caches": _items_by_class(result, MaintenanceClassification.REGENERABLE_CACHE),
        "regenerable_builds": _items_by_class(result, MaintenanceClassification.REGENERABLE_BUILD),
        "unreadable_files": _items_by_class(result, MaintenanceClassification.UNREADABLE_REVIEW),
        "unstable_files": _items_by_class(result, MaintenanceClassification.HASH_UNSTABLE_FILE),
        "safety": result["safety"],
        "decision": result["decision"],
    }


def _items_by_class(result: dict[str, Any], classification: MaintenanceClassification) -> list[dict[str, Any]]:
    return [i for i in result["inventory"]["files"] if i["classification"] == classification]


def _inventory_md(payload: dict[str, Any]) -> str:
    metrics = payload["inventory_summary"]
    return (
        "# Inventario de armazenamento - Etapa 5.1\n\n"
        f"- Arquivos inventariados: {metrics['files_scanned']}\n"
        f"- Diretorios analisados: {metrics['directories_scanned']}\n"
        f"- Tamanho total: {metrics['total_size_bytes']} bytes\n"
        f"- Arquivos protegidos: {metrics['protected_files']}\n"
        f"- Caches regeneraveis: {metrics['cache_files']}\n"
        f"- Temporarios candidatos: {metrics['temporary_orphan_candidates']}\n"
        f"- Duplicados: {metrics['duplicate_groups']} grupos\n"
        f"- Decisao: `{payload['decision']}`\n"
    )


def _cleanup_plan_md(payload: dict[str, Any]) -> str:
    rec = payload["recoverable_space"]
    return (
        "# Plano preliminar de manutencao - Etapa 5.1\n\n"
        f"- Candidatos em revisao: {len(payload['candidates'])}\n"
        f"- Espaco regeneravel baixo risco: {rec['recoverable_low_risk']} bytes\n"
        f"- Espaco potencial apos revisao: {rec['recoverable_after_review']} bytes\n"
        "- delete_now/move_now/archive_now/compress_now: false para todos os itens\n"
        f"- cleanup_plan_hash: `{payload.get('cleanup_plan_hash')}`\n"
        f"- Decisao: `{payload['decision']}`\n"
    )


def _reference_graph_md(payload: dict[str, Any]) -> str:
    return (
        "# Grafo de referencias - Etapa 5.1\n\n"
        f"- Arquivos referenciados: {len(payload['files_referenced'])}\n"
        f"- Referencias quebradas: {len(payload['broken_references'])}\n"
        f"- Referencias por hash: {len(payload['hash_references'])}\n"
    )


def _duplicate_md(payload: dict[str, Any]) -> str:
    return (
        "# Duplicados e temporarios - Etapa 5.1\n\n"
        f"- Duplicados identicos: {len(payload['identical_duplicates'])} grupos\n"
        f"- Temporarios orfaos candidatos: {len(payload['temporary_orphan_candidates'])}\n"
        f"- Caches regeneraveis: {len(payload['regenerable_caches'])}\n"
        f"- Builds regeneraveis: {len(payload['regenerable_builds'])}\n"
    )


def _hash_file_stable(path: Path, *, before_hash_hook: Callable[[Path], None] | None) -> dict[str, str]:
    try:
        before = path.stat()
        if before_hash_hook:
            before_hash_hook(path)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            return {"status": "HASH_UNSTABLE_FILE"}
        return {"status": "HASHED", "sha256": digest.hexdigest()}
    except OSError:
        return {"status": "HASH_SKIPPED_UNREADABLE"}


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat() if not path.is_symlink() else path.lstat()
    except OSError:
        return None


def _git_file_set(root: Path, cmd: list[str]) -> set[str]:
    try:
        out = subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {_normalize_rel(line) for line in out.stdout.splitlines() if line.strip()}


def _git_ignored(root: Path) -> set[str]:
    try:
        paths = [str(p.relative_to(root)).replace("\\", "/") for p in _scan_files(root)]
        if not paths:
            return set()
        out = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=root,
            input="\n".join(paths),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {_normalize_rel(line) for line in out.stdout.splitlines() if line.strip()}


def _git_status(root: Path) -> set[str]:
    try:
        out = subprocess.run(["git", "status", "--short"], cwd=root, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    result = set()
    for line in out.stdout.splitlines():
        if len(line) > 3:
            result.add(_normalize_rel(line[3:]))
    return result


def _relative(root: Path, path: Path) -> str:
    try:
        return _normalize_rel(str(path.resolve().relative_to(root)))
    except (ValueError, OSError):
        try:
            return _normalize_rel(str(path.relative_to(root)))
        except ValueError:
            return _sanitize_path(str(path))


def _normalize_rel(value: str) -> str:
    return value.replace("\\", "/").strip()


def _posix(path: Path) -> str:
    value = path.as_posix()
    return "." if value == "." else value


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _age_bucket(age: int | None) -> str:
    if age is None:
        return "unknown"
    if age <= 1:
        return "0-1 days"
    if age <= 7:
        return "2-7 days"
    if age <= 30:
        return "8-30 days"
    if age <= 90:
        return "31-90 days"
    if age <= 180:
        return "91-180 days"
    if age <= 365:
        return "181-365 days"
    return "more than 365 days"


def _mime_family(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".json", ".md", ".txt", ".py", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".example"}:
        return "text"
    if ext in {".xlsx", ".xlsm", ".xls", ".csv"}:
        return "workbook"
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
        return "image"
    if ext in {".zip", ".7z", ".gz"}:
        return "archive"
    return "binary"


def _is_sensitive(rel: str) -> bool:
    lower = rel.lower()
    return rel == ".env" or any(marker in lower for marker in SENSITIVE_NAME_MARKERS) or lower.startswith(
        ("data/auth/", "data/browser_profile_edge/", "data/edge_cdp_profile/")
    )


def _is_cache(rel: str, name: str) -> bool:
    return (
        rel.startswith((".pytest_cache/", ".mypy_cache/", ".ruff_cache/"))
        or "/__pycache__/" in f"/{rel}"
        or name.endswith(".pyc")
    )


def _is_build(rel: str) -> bool:
    return rel.startswith(("build/", "dist/", "htmlcov/")) or ".egg-info/" in rel or rel == ".coverage"


def _is_temporary(rel: str, name: str) -> bool:
    return name.startswith("~$") or any(name.endswith(suffix) for suffix in TEMPORARY_SUFFIXES) or rel.startswith(("tmp/", "temp/", "data/temp/"))


def _is_external_profile_dir(path: Path) -> bool:
    return path.name in {"browser_profile_edge", "edge_cdp_profile"} and path.parent.name == "data"


def _safe_to_read_text(rel: str, path: Path) -> bool:
    if _is_sensitive(rel) or path.is_symlink():
        return False
    if path.suffix.lower() not in TEXT_EXTENSIONS and path.name not in {".gitignore"}:
        return False
    try:
        return path.stat().st_size <= 2 * 1024 * 1024
    except OSError:
        return False


def _extract_path_like_tokens(text: str) -> set[str]:
    tokens = set()
    pattern = re.compile(
        r"(?:(?:data|docs|specs|scripts|tests|automacao_gd|apps|outputs|src|frontend)/[A-Za-z0-9_.@()=+/\\-]+)"
    )
    for match in pattern.findall(text.replace("\\", "/")):
        tokens.add(match.rstrip(".,);]`'\""))
    return tokens


def _reference_record(*, source_file: str, raw_token: str, existing_files: set[str]) -> dict[str, Any]:
    normalized = _normalize_reference_token(raw_token)
    kind, confidence, reason = _reference_kind(source_file, raw_token, normalized)
    exists = normalized in existing_files
    protects = _reference_protects_target(kind, confidence, exists)
    broken_authoritative = kind == ReferenceKind.AUTHORITATIVE_FILE_REFERENCE and confidence == ReferenceConfidence.HIGH and not exists
    return {
        "source_file": source_file,
        "raw_token": raw_token,
        "normalized_target": normalized,
        "kind": kind,
        "confidence": confidence,
        "exists": exists,
        "protects_target": protects,
        "broken_authoritative": broken_authoritative,
        "reason": reason,
    }


def _normalize_reference_token(token: str) -> str:
    normalized = token.replace("\\", "/").lstrip("./")
    normalized = re.sub(r"/+", "/", normalized)
    return normalized.rstrip(".,);]`'\"")


def _reference_kind(source_file: str, raw_token: str, normalized: str) -> tuple[ReferenceKind, ReferenceConfidence, str]:
    lower_source = source_file.lower()
    lower = normalized.lower()
    raw_lower = raw_token.lower()
    if raw_token.endswith(("/", "\\")):
        return ReferenceKind.DIRECTORY_REFERENCE, ReferenceConfidence.LOW, "directory_token"
    if any(marker in raw_lower for marker in ("tmp_path", "fixture", "mock", "sample", "synthetic")) or lower_source.startswith("tests/"):
        return ReferenceKind.TEST_FIXTURE_REFERENCE, ReferenceConfidence.LOW, "test_fixture_context"
    if _is_placeholder_reference(normalized):
        return ReferenceKind.PLACEHOLDER_REFERENCE, ReferenceConfidence.LOW, "placeholder_pattern"
    if _is_malformed_reference(normalized):
        return ReferenceKind.MALFORMED_REFERENCE, ReferenceConfidence.LOW, "malformed_path"
    if any(marker in raw_lower for marker in ("example", "exemplo")):
        return ReferenceKind.DOCUMENTATION_EXAMPLE, ReferenceConfidence.LOW, "documentation_example_marker"
    if not _looks_internal_reference(normalized):
        return ReferenceKind.EXTERNAL_REFERENCE, ReferenceConfidence.LOW, "external_reference"
    if lower_source.startswith(("automacao_gd/", "apps/", "scripts/", "src/")):
        return ReferenceKind.CODE_RESOURCE_REFERENCE, ReferenceConfidence.MEDIUM, "code_resource_context"
    if _is_authoritative_source(source_file):
        return ReferenceKind.AUTHORITATIVE_FILE_REFERENCE, ReferenceConfidence.HIGH, "authoritative_source"
    return ReferenceKind.DOCUMENTATION_EXAMPLE, ReferenceConfidence.LOW, "non_authoritative_text"


def _reference_protects_target(kind: ReferenceKind, confidence: ReferenceConfidence, exists: bool) -> bool:
    return (kind == ReferenceKind.AUTHORITATIVE_FILE_REFERENCE and confidence == ReferenceConfidence.HIGH and exists) or (
        kind == ReferenceKind.CODE_RESOURCE_REFERENCE and exists
    )


def _is_authoritative_source(source_file: str) -> bool:
    return source_file in {
        "docs/codex_execution_ledger.md",
        "docs/production_runbook.md",
        "docs/operator_checklist.md",
    } or source_file.startswith(("specs/SPEC-", "docs/releases/", "data/logs/")) or (
        source_file.startswith("docs/") and "ledger" in Path(source_file).name.lower()
    )


def _is_placeholder_reference(path: str) -> bool:
    return any(marker in path for marker in ("NNNN-", "SPEC-NNN", "<timestamp>", "<PROJECT_ROOT>")) or bool(re.search(r"<[^>]+>", path))


def _is_malformed_reference(path: str) -> bool:
    return (
        path.endswith("_")
        or path.endswith("/n")
        or any(ord(char) < 32 for char in path)
        or "*" in path
        or Path(path).name in {"test_", "validate_"}
    )


def _reference_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = str(record["kind"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def _looks_internal_reference(value: str) -> bool:
    return value.split("/", 1)[0] in {"data", "docs", "specs", "scripts", "tests", "automacao_gd", "apps", "outputs", "src", "frontend"}


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _sanitize_path(value: str) -> str:
    sanitized = value.replace("\\", "/")
    sanitized = re.sub(r"[A-Za-z]:/Users/[^/]+", "<AUTH_ROOT>", sanitized)
    sanitized = re.sub(r"[A-Za-z]:/.*", "<EXTERNAL_PATH>", sanitized)
    return sanitized


def _external_root(path: Path) -> dict[str, Any]:
    text = str(path).replace("\\", "/")
    if text.upper().startswith("Y:"):
        alias = "<OFFICIAL_WORKBOOK_ROOT>"
    elif text.upper().startswith("Z:"):
        alias = "<CLIENT_ARCHIVE_ROOT>"
    elif "auth" in text.lower() or "profile" in text.lower():
        alias = "<AUTH_ROOT>"
    else:
        alias = "<EXTERNAL_PROTECTED_ROOT>"
    return {
        "external_root_alias": alias,
        "available": path.exists(),
        "protected": True,
        "recursive_scan": False,
        "classification": MaintenanceClassification.EXTERNAL_PROTECTED_ROOT,
    }


def _official_workbook_summary(options: InventoryOptions) -> dict[str, Any]:
    path = options.official_workbook_path
    if not path:
        return {"configured": False}
    exists = path.exists()
    size = path.stat().st_size if exists else None
    digest = None
    status = "NOT_FOUND"
    if exists and path.is_file():
        digest = _hash_file_stable(path, before_hash_hook=None).get("sha256")
        status = "HASHED"
    return {
        "configured": True,
        "path_alias": "<OFFICIAL_WORKBOOK_ROOT>/planilha.xlsx",
        "exists": exists,
        "size_bytes": size,
        "sha256_status": status,
        "sha256": digest,
        "sha256_matches_expected": digest == options.official_workbook_sha if options.official_workbook_sha else None,
        "classification": MaintenanceClassification.PROTECTED_ABSOLUTE,
        "recursive_scan": False,
    }


def _protection_reasons(classification: MaintenanceClassification | str, rel: str, referenced_by: list[str]) -> list[str]:
    reasons = []
    if str(classification).startswith("PROTECTED"):
        reasons.append("protected_classification")
    if str(classification).startswith("ACTIVE"):
        reasons.append("active_operational_state")
    if referenced_by:
        reasons.append("referenced_by_artifact")
    if rel.startswith(("automacao_gd/", "apps/", "scripts/", "tests/", "specs/", "docs/")) or rel in PROTECTED_ROOT_FILES:
        reasons.append("project_source_or_governance_file")
    return sorted(set(reasons))


def _candidate_action(classification: MaintenanceClassification | str) -> str:
    mapping = {
        MaintenanceClassification.TEMPORARY_ORPHAN_CANDIDATE: "REVIEW_DELETE_TEMPORARY",
        MaintenanceClassification.REGENERABLE_CACHE: "REVIEW_CACHE_CLEANUP",
        MaintenanceClassification.REGENERABLE_BUILD: "REVIEW_BUILD_CLEANUP",
        MaintenanceClassification.DUPLICATE_IDENTICAL_REVIEW: "REVIEW_DUPLICATE",
        MaintenanceClassification.LOG_RETENTION_REVIEW: "REVIEW_LOG_RETENTION",
        MaintenanceClassification.ARCHIVE_CANDIDATE: "REVIEW_ARCHIVE",
        MaintenanceClassification.DELETE_CANDIDATE_SAFE: "REVIEW_DELETE",
    }
    return mapping.get(MaintenanceClassification(str(classification)), "NO_ACTION_READ_ONLY")


def _review_required(classification: MaintenanceClassification | str) -> bool:
    return any(marker in str(classification) for marker in ("REVIEW", "CANDIDATE", "DUPLICATE", "UNKNOWN", "UNREADABLE", "UNSTABLE"))


def _risk(classification: MaintenanceClassification | str) -> str:
    if classification in {MaintenanceClassification.REGENERABLE_CACHE, MaintenanceClassification.REGENERABLE_BUILD}:
        return "low"
    if "PROTECTED" in str(classification) or "ACTIVE" in str(classification):
        return "high"
    return "medium"


def _evidence(classification: MaintenanceClassification | str, rel: str, referenced_by: list[str]) -> list[str]:
    evidence = [str(classification)]
    if referenced_by:
        evidence.append(f"referenced_by_count={len(referenced_by)}")
    if _is_temporary(rel, Path(rel).name.lower()):
        evidence.append("temporary_pattern")
    if _is_cache(rel, Path(rel).name.lower()):
        evidence.append("regeneration_command=pytest/ruff/mypy/compileall")
    return evidence


def _git_status_label(item: dict[str, Any]) -> str:
    if item.get("git_tracked"):
        return "tracked_modified" if item.get("git_modified") else "tracked"
    if item.get("git_ignored"):
        return "ignored"
    if item.get("git_untracked"):
        return "untracked"
    return "outside_git_index"


def _review(metrics: dict[str, Any], items: list[dict[str, Any]], references: dict[str, Any], official: dict[str, Any]) -> dict[str, Any]:
    conflicts = [
        item["relative_path"]
        for item in items
        if item["protection_reasons"] and item["classification"] in {MaintenanceClassification.DELETE_CANDIDATE_SAFE}
    ]
    return {
        "P0": 0,
        "P1": 1 if conflicts else 0,
        "P2": len(references.get("broken_references", [])),
        "P3": metrics.get("unknown_review_files", 0),
        "classification_conflicts": conflicts,
        "privacy_findings": 0,
        "read_only_violations": 0,
        "official_workbook_ok": official.get("sha256_matches_expected") is not False,
    }


def _decision(review: dict[str, Any], metrics: dict[str, Any]) -> str:
    if not review["official_workbook_ok"]:
        return "STAGE5_1_BLOCKED — OFFICIAL_WORKBOOK_STATE_CHANGED"
    if review["classification_conflicts"]:
        return "STAGE5_1_REJECTED — PROTECTED_FILE_CLASSIFIED_FOR_CLEANUP"
    if review["privacy_findings"]:
        return "STAGE5_1_REJECTED — PRIVACY_VIOLATION"
    if metrics["files_scanned"] == 0:
        return "STAGE5_1_NO_CLEANUP_CANDIDATES_FOUND"
    if review["P2"] or review["P3"]:
        return "STAGE5_1_PARTIAL — INVENTORY_REQUIRES_MANUAL_REVIEW"
    return "STAGE5_1_CLEANUP_PLAN_GENERATED_READ_ONLY"


def _hash_without(payload: dict[str, Any], key: str) -> str:
    plain = _thaw_payload(payload)
    return hashlib.sha256(
        json.dumps({k: v for k, v in plain.items() if k != key}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
