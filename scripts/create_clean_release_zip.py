from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


_ALLOWED_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "README.md",
    "app.py",
    "desktop_app.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements-build.txt",
    "requirements-dev.txt",
}
_ALLOWED_EXACT_FILES = {
    "frontend/AGENTS.md",
}
_ALLOWED_ROOT_DIRS = {
    ".agents",
    ".github",
    "apps",
    "automacao_gd",
    "docs",
    "scripts",
    "specs",
    "src",
    "tests",
}
_FORBIDDEN_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "browser_profile",
    "browser_profiles",
    "cache",
    "caches",
    "downloads",
    "logs",
    "node_modules",
    "outputs",
    "profiles",
    "temp",
    "tmp",
    "user data",
    "venv",
}
_FORBIDDEN_FILE_NAMES = {
    "cookies",
    "history",
    "local state",
    "login data",
    "network persistent state",
    "storage_state.json",
    "web data",
}
_FORBIDDEN_SUFFIXES = {
    ".bak",
    ".backup",
    ".crdownload",
    ".log",
    ".pyc",
    ".swp",
    ".temp",
    ".tmp",
    ".zip",
}
_OPERATIONAL_DOCUMENT_SUFFIXES = {".pdf", ".xls", ".xlsx", ".xlsm"}
_GENERATED_RELEASE_ROOT = PurePosixPath("apps/desktop/frontend/dist")


def _normalized(relative_path: Path | PurePosixPath | str) -> PurePosixPath:
    return PurePosixPath(str(relative_path).replace("\\", "/"))


def _is_declared_synthetic_fixture(relative: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    return (
        len(parts) >= 4
        and parts[:3] == ("tests", "fixtures", "synthetic")
        and relative.name.casefold().startswith("synthetic_")
    )


def release_exclusion_reason(relative_path: Path | PurePosixPath | str) -> str | None:
    relative = _normalized(relative_path)
    parts = tuple(part.casefold() for part in relative.parts)
    if not parts or relative.is_absolute() or ".." in relative.parts:
        return "unsafe_path"

    name = relative.name.casefold()
    suffix = relative.suffix.casefold()
    if name == ".env.example":
        return None
    if name == ".env" or name.startswith(".env."):
        return "environment_file"
    if name in _FORBIDDEN_FILE_NAMES:
        return "browser_or_auth_state"
    if any(marker in name for marker in ("cookie", "credential", "secret", "token")):
        return "credential_artifact"
    if name.endswith("~") or suffix in _FORBIDDEN_SUFFIXES:
        return "temporary_or_nested_archive"
    if suffix in _OPERATIONAL_DOCUMENT_SUFFIXES and not _is_declared_synthetic_fixture(relative):
        return "operational_document"
    if any(part in _FORBIDDEN_DIR_NAMES for part in parts[:-1]):
        return "forbidden_directory"
    if parts[0] in {"build", "data", "dist", "outputs"}:
        return "operational_or_build_directory"
    if len(parts) >= 2 and parts[:2] in {
        ("frontend", "dist"),
        ("automacao_gd", "data"),
    }:
        return "legacy_build_or_operational_data"
    return None


def should_exclude_from_release(relative_path: Path | PurePosixPath | str) -> bool:
    relative = _normalized(relative_path)
    if release_exclusion_reason(relative) is not None:
        return True
    if relative.as_posix() in _ALLOWED_EXACT_FILES:
        return False
    if len(relative.parts) == 1:
        return relative.name not in _ALLOWED_ROOT_FILES
    return relative.parts[0].casefold() not in _ALLOWED_ROOT_DIRS


def _run_git(root: Path, *args: str, check: bool = True) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("RELEASE_PROVENANCE_INVALID") from exc
    return completed.stdout.strip()


def _assert_clean_worktree(root: Path) -> None:
    if _run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("DIRTY_WORKTREE")


def _git_provenance(root: Path) -> tuple[dict[str, object], list[PurePosixPath]]:
    repository_root = Path(_run_git(root, "rev-parse", "--show-toplevel")).resolve()
    if repository_root != root:
        raise RuntimeError("RELEASE_PROVENANCE_INVALID")
    _assert_clean_worktree(root)

    head_sha = _run_git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise RuntimeError("RELEASE_PROVENANCE_INVALID")
    branch = _run_git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False) or None
    tags = [item for item in _run_git(root, "tag", "--points-at", "HEAD").splitlines() if item]
    tracked_output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    tracked = [
        PurePosixPath(item.decode("utf-8"))
        for item in tracked_output.split(b"\0")
        if item
    ]
    return (
        {
            "head_sha": head_sha,
            "branch": branch,
            "detached": branch is None,
            "tags": sorted(tags),
            "describe": _run_git(root, "describe", "--tags", "--always"),
        },
        tracked,
    )


def _release_versions(root: Path) -> dict[str, str | None]:
    project_version: str | None = None
    python_version: str | None = None
    frontend_version: str | None = None
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = str(project["project"]["version"])
    except (KeyError, OSError, tomllib.TOMLDecodeError):
        pass
    try:
        package_source = (root / "automacao_gd/__init__.py").read_text(encoding="utf-8")
        match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']',
            package_source,
            re.MULTILINE,
        )
        python_version = match.group(1) if match else None
    except OSError:
        pass
    try:
        frontend = json.loads(
            (root / "apps/desktop/frontend/package.json").read_text(encoding="utf-8")
        )
        frontend_version = str(frontend["version"])
    except (KeyError, OSError, json.JSONDecodeError):
        pass
    return {
        "project": project_version,
        "python_package": python_version,
        "frontend": frontend_version,
    }


def _generated_source_binding(file_hashes: dict[str, str]) -> dict[str, object]:
    source_files = sorted(
        name
        for name in file_hashes
        if name.startswith("apps/desktop/frontend/")
        and not name.startswith("apps/desktop/frontend/dist/")
    )
    digest_payload = "\n".join(
        f"{name}:{file_hashes[name]}" for name in source_files
    ).encode("utf-8")
    return {
        "root": "apps/desktop/frontend",
        "source_files": source_files,
        "source_digest": hashlib.sha256(digest_payload).hexdigest(),
    }


def create_clean_release_zip(project_root: Path, output_path: Path) -> dict:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("A raiz do projeto deve ser um diretório existente.")
    provenance, tracked = _git_provenance(root)
    output = Path(output_path).resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    excluded: list[str] = []
    source_paths: list[Path] = []
    file_hashes: dict[str, str] = {}
    for relative in sorted(tracked, key=lambda item: item.as_posix().casefold()):
        normalized = relative.as_posix()
        path = root.joinpath(*relative.parts)
        if should_exclude_from_release(relative) or path.is_symlink():
            excluded.append(normalized)
            continue
        included.append(normalized)
        source_paths.append(Path(normalized))
        file_hashes[normalized] = hashlib.sha256(path.read_bytes()).hexdigest()

    generated_artifacts: list[str] = []
    generated_root = root.joinpath(*_GENERATED_RELEASE_ROOT.parts)
    if generated_root.is_dir():
        for path in sorted(generated_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            normalized = path.relative_to(root).as_posix()
            generated_artifacts.append(normalized)
            if normalized not in file_hashes:
                included.append(normalized)
                source_paths.append(Path(normalized))
                file_hashes[normalized] = hashlib.sha256(path.read_bytes()).hexdigest()
    included.sort(key=str.casefold)

    from scripts.privacy_scan import scan_paths

    privacy = scan_paths(root, source_paths)
    if not privacy["valid"]:
        raise RuntimeError("PRIVACY_SCAN_BLOCKED")
    _assert_clean_worktree(root)
    if any(
        hashlib.sha256(root.joinpath(*PurePosixPath(name).parts).read_bytes()).hexdigest()
        != expected_hash
        for name, expected_hash in file_hashes.items()
    ):
        raise RuntimeError("RELEASE_CONTENT_MISMATCH")
    manifest = {
        "schema_version": 1,
        **provenance,
        "versions": _release_versions(root),
        "privacy_scan": {
            "valid": True,
            "scanned_files": privacy["scanned_files"],
            "finding_count": 0,
        },
        "generated_artifacts": generated_artifacts,
        "generated_source": _generated_source_binding(file_hashes),
        "files": file_hashes,
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for normalized in included:
                info = zipfile.ZipInfo(normalized, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(
                    info,
                    root.joinpath(*PurePosixPath(normalized).parts).read_bytes(),
                )
            manifest_info = zipfile.ZipInfo(
                "release-manifest.json", date_time=(1980, 1, 1, 0, 0, 0)
            )
            manifest_info.compress_type = zipfile.ZIP_DEFLATED
            manifest_info.create_system = 3
            manifest_info.external_attr = 0o100644 << 16
            archive.writestr(manifest_info, manifest_bytes)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    from scripts.validate_release_zip import validate_release_zip

    validation = validate_release_zip(
        output,
        reports_dir=output.parent / f"{output.stem}_reports",
        expected_head_sha=str(provenance["head_sha"]),
    )
    if not validation["valid"]:
        output.unlink(missing_ok=True)
        raise RuntimeError("ZIP de release reprovado: " + "; ".join(validation["errors"]))

    return {
        "output_path": str(output),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included_files": included,
        "excluded_files": excluded,
        "validation": validation,
        "manifest": manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ZIP limpo da candidata 2.0.2.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.project_root.resolve().parent / "gd_neoenergia_release.zip"
    report = create_clean_release_zip(args.project_root, output)
    print(f"ZIP criado: {report['output_path']}")
    print(f"Arquivos incluídos: {report['included_count']}")
    print(f"Arquivos excluídos: {report['excluded_count']}")


if __name__ == "__main__":
    main()
