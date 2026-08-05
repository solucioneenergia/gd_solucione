from __future__ import annotations

import argparse
import os
import sys
import tempfile
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


def create_clean_release_zip(project_root: Path, output_path: Path) -> dict:
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("A raiz do projeto deve ser um diretório existente.")
    output = Path(output_path).resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    excluded: list[str] = []
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_dir():
                    continue
                relative = path.relative_to(root)
                if path.is_symlink() or path.resolve(strict=False) in {output, temporary}:
                    excluded.append(relative.as_posix())
                    continue
                if should_exclude_from_release(relative):
                    excluded.append(relative.as_posix())
                    continue
                archive.write(path, relative.as_posix())
                included.append(relative.as_posix())
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    from scripts.validate_release_zip import validate_release_zip

    validation = validate_release_zip(
        output,
        reports_dir=output.parent / f"{output.stem}_reports",
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
