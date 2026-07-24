from __future__ import annotations

import argparse
import os
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


_EXCLUDED_DIRS = {
    Path(".venv"),
    Path(".pytest_cache"),
    Path(".git"),
    Path("data/auth"),
    Path("data/browser_profile_edge"),
    Path("data/edge_cdp_profile"),
    Path("data/downloads"),
    Path("data/logs"),
    Path("data/state"),
    Path("data/cache"),
    Path("data/migration_backups"),
    Path("data/temp"),
    Path("frontend/node_modules"),
    Path("frontend/dist"),
}
_EXCLUDED_SUFFIXES = {
    ".pdf", ".xls", ".xlsx", ".xlsm", ".pyc", ".tmp", ".temp", ".bak",
    ".swp", ".crdownload", ".log"
}


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
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
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
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    from scripts.validate_release_zip import validate_release_zip

    validation = validate_release_zip(
        output,
        reports_dir=root / "data" / "logs",
    )
    if not validation["valid"]:
        output.unlink(missing_ok=True)
        raise RuntimeError(
            "ZIP de release reprovado: " + "; ".join(validation["errors"])
        )

    return {
        "output_path": str(output),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included_files": included,
        "excluded_files": excluded,
        "validation": validation,
    }


def should_exclude_from_release(relative_path: Path) -> bool:
    relative = Path(relative_path)
    lowered_parts = tuple(part.lower() for part in relative.parts)
    lowered = Path(*lowered_parts)
    if relative.name.lower() == ".env":
        return True
    if relative.name.lower() == "storage_state.json":
        return True
    if any(marker in relative.name.lower() for marker in ("cookie", "token")):
        return True
    if relative.name.endswith("~"):
        return True
    if relative.suffix.lower() in _EXCLUDED_SUFFIXES:
        return True
    if "__pycache__" in lowered_parts:
        return True
    return any(lowered == directory or directory in lowered.parents for directory in _EXCLUDED_DIRS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria ZIP limpo da V2.")
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
