from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


TEMPORARY_FILE_SUFFIXES = {".tmp", ".temp", ".crdownload", ".part"}
PROTECTED_FILE_SUFFIXES = {".pdf", ".xlsx", ".xlsm"}
PROTECTED_REPORT_SUFFIXES = {".md", ".json"}
PROTECTED_FILE_NAMES = {
    "pipeline_cdp_state.json",
    "client_folder_cache.json",
    "state.json",
    "cache.json",
}
TEMPORARY_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache"}


@dataclass(slots=True)
class CleanupCandidate:
    path: Path
    reason: str
    size_bytes: int
    category: str
    is_safe: bool
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["path"] = self.path
        return payload


@dataclass
class CleanupResult:
    dry_run: bool
    scanned_count: int = 0
    deleted_count: int = 0
    skipped_count: int = 0
    total_bytes_candidate: int = 0
    total_bytes_deleted: int = 0
    candidates: list[CleanupCandidate] = field(default_factory=list)
    deleted_files: list[Path] = field(default_factory=list)
    skipped_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def would_remove(self) -> list[Path]:
        return [candidate.path for candidate in self.candidates if candidate.is_safe]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "scanned_count": self.scanned_count,
            "deleted_count": self.deleted_count,
            "skipped_count": self.skipped_count,
            "total_bytes_candidate": self.total_bytes_candidate,
            "total_bytes_deleted": self.total_bytes_deleted,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "would_remove": self.would_remove,
            "deleted_files": self.deleted_files,
            "skipped_files": self.skipped_files,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def to_serializable_dict(self) -> dict[str, Any]:
        return _json_safe(self.to_dict())

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


class TemporaryCleanupService:
    def __init__(self, allowed_root: Path) -> None:
        self.allowed_root = _resolve_existing_root(allowed_root)

    def cleanup(
        self,
        *,
        dry_run: bool = True,
        candidates: Iterable[Path] | None = None,
    ) -> CleanupResult:
        result = CleanupResult(dry_run=dry_run)
        explicit_candidates = candidates is not None
        paths = list(candidates) if explicit_candidates else _scan_paths(self.allowed_root)

        for raw_path in paths:
            try:
                path = _resolve_candidate_path(raw_path, self.allowed_root)
            except ValueError:
                if explicit_candidates:
                    raise
                result.scanned_count += 1
                result.skipped_count += 1
                result.skipped_files.append(Path(raw_path))
                result.warnings.append(f"Caminho fora da raiz permitida ignorado: {raw_path}")
                continue
            result.scanned_count += 1
            candidate = classify_cleanup_candidate(path, self.allowed_root)
            if candidate is None:
                continue
            result.candidates.append(candidate)
            if candidate.is_safe:
                result.total_bytes_candidate += candidate.size_bytes
            else:
                result.skipped_count += 1
                result.skipped_files.append(candidate.path)
                if candidate.warning:
                    result.warnings.append(candidate.warning)
                continue

            if dry_run:
                continue

            try:
                _delete_candidate(candidate.path)
                result.deleted_count += 1
                result.total_bytes_deleted += candidate.size_bytes
                result.deleted_files.append(candidate.path)
            except OSError as exc:
                result.skipped_count += 1
                result.skipped_files.append(candidate.path)
                result.errors.append(f"{candidate.path}: {exc}")

        return result


def cleanup_temp_files(
    allowed_root: Path,
    *,
    dry_run: bool = True,
    candidates: Iterable[Path] | None = None,
) -> CleanupResult:
    return TemporaryCleanupService(allowed_root).cleanup(
        dry_run=dry_run,
        candidates=candidates,
    )


def classify_cleanup_candidate(path: Path, allowed_root: Path) -> CleanupCandidate | None:
    resolved_root = _resolve_existing_root(allowed_root)
    resolved_path = path.resolve(strict=False)
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError(f"Caminho fora da raiz permitida: {path}")

    if path.is_dir():
        return _classify_directory(path, resolved_root)
    if not path.is_file():
        return None
    return _classify_file(path, resolved_root)


def _classify_file(path: Path, allowed_root: Path) -> CleanupCandidate | None:
    reason = _temporary_file_reason(path)
    if reason is None:
        return None

    warning = _protection_warning(path, allowed_root)
    size_bytes = _safe_size(path)
    if warning:
        return CleanupCandidate(
            path=path,
            reason=reason,
            size_bytes=size_bytes,
            category="file",
            is_safe=False,
            warning=warning,
        )

    return CleanupCandidate(
        path=path,
        reason=reason,
        size_bytes=size_bytes,
        category="file",
        is_safe=True,
    )


def _classify_directory(path: Path, allowed_root: Path) -> CleanupCandidate | None:
    name = path.name
    if name not in TEMPORARY_DIRECTORY_NAMES and not _is_temporary_empty_dir(path):
        return None
    warning = _protection_warning(path, allowed_root)
    if warning:
        return CleanupCandidate(
            path=path,
            reason="protected_directory",
            size_bytes=0,
            category="directory",
            is_safe=False,
            warning=warning,
        )
    return CleanupCandidate(
        path=path,
        reason="temporary_directory",
        size_bytes=_directory_size(path),
        category="directory",
        is_safe=True,
    )


def _temporary_file_reason(path: Path) -> str | None:
    name = path.name
    suffix = path.suffix.lower()
    if name.startswith("~$") and suffix in {".xlsx", ".xlsm", ".xls"}:
        return "office_lock_file"
    if suffix == ".crdownload":
        return "browser_incomplete_download"
    if suffix == ".part":
        return "partial_download"
    if suffix in {".tmp", ".temp"}:
        return "temporary_extension"
    return None


def _protection_warning(path: Path, allowed_root: Path) -> str | None:
    normalized_parts = [part.casefold() for part in path.parts]
    suffix = path.suffix.lower()
    name = path.name.casefold()

    if suffix in PROTECTED_FILE_SUFFIXES and not name.startswith("~$"):
        return "arquivo operacional protegido"
    if suffix in PROTECTED_REPORT_SUFFIXES and suffix not in TEMPORARY_FILE_SUFFIXES:
        return "relatório/state/cache protegido"
    if name in PROTECTED_FILE_NAMES:
        return "arquivo de state/cache protegido"
    if "backup" in name or "_backup_" in name:
        return "backup protegido"
    if _has_path_segment_sequence(normalized_parts, ["data", "state"]):
        return "data/state protegido"
    if _has_path_segment_sequence(normalized_parts, ["data", "cache"]):
        return "data/cache protegido"
    if _has_path_segment_sequence(normalized_parts, ["data", "downloads"]):
        if suffix not in {".crdownload", ".part"}:
            return "data/downloads protegido para arquivos definitivos"
    if _has_path_segment_sequence(normalized_parts, ["data", "logs"]):
        if suffix in PROTECTED_REPORT_SUFFIXES:
            return "relatório real protegido"
    return None


def _scan_paths(root: Path) -> list[Path]:
    paths = []
    for path in root.rglob("*"):
        paths.append(path)
    paths.sort(key=lambda item: (len(item.parts), str(item)), reverse=True)
    return paths


def _delete_candidate(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _resolve_existing_root(root: Path) -> Path:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Raiz permitida inexistente ou inválida: {root}")
    return root.resolve(strict=True)


def _resolve_candidate_path(path: Path, allowed_root: Path) -> Path:
    path = Path(path)
    resolved_path = path.resolve(strict=False)
    resolved_root = allowed_root.resolve(strict=True)
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError(f"Caminho fora da raiz permitida: {path}")
    return path


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_path_segment_sequence(parts: list[str], sequence: list[str]) -> bool:
    if len(parts) < len(sequence):
        return False
    for index in range(len(parts) - len(sequence) + 1):
        if parts[index : index + len(sequence)] == sequence:
            return True
    return False


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += _safe_size(item)
    return total


def _is_temporary_empty_dir(path: Path) -> bool:
    return path.suffix.lower() in {".tmp", ".temp"} and not any(path.iterdir())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in value]
    if isinstance(value, tuple):
        return [_json_safe(child) for child in value]
    return value
