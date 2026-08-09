from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.persistence.atomic import (
    atomic_copy_file,
    atomic_write_json,
    atomic_write_text,
)
from automacao_gd.application.operational_guard import (
    DirectRouteAuthorization,
    MIGRATION_OPERATION,
    guard_direct_route,
)
from automacao_gd.infrastructure.config import get_settings


_SINGLE_FILES = (
    Path("data/state/pipeline_cdp_state.json"),
    Path("data/cache/client_folder_cache.json"),
)
_DOWNLOADS_DIR = Path("data/downloads")
_FORBIDDEN_PATHS = (
    Path(".env"),
    Path("storage_state.json"),
    Path(".venv"),
    Path("data/auth"),
    Path("data/browser_profile_edge"),
    Path("data/edge_cdp_profile"),
    Path("data/logs"),
    Path(".pytest_cache"),
)
_TEMPORARY_SUFFIXES = {
    ".tmp", ".temp", ".crdownload", ".part", ".pyc", ".log"
}


def build_migration_plan(v1_root: Path, v2_root: Path) -> list[dict]:
    source_root, destination_root = _validated_roots(v1_root, v2_root)
    relative_files = [path for path in _SINGLE_FILES if (source_root / path).is_file()]
    downloads = source_root / _DOWNLOADS_DIR
    if downloads.exists():
        for source in sorted(downloads.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Link simbólico não permitido na migração: {source}")
            if source.is_file() and not _excluded_download_file(source):
                relative_files.append(source.relative_to(source_root))

    plan = []
    for relative in relative_files:
        source = _safe_child(source_root, relative)
        destination = _safe_child(destination_root, relative)
        plan.append(
            {
                "relative_path": relative.as_posix(),
                "source": str(source),
                "destination": str(destination),
                "destination_exists": destination.exists(),
            }
        )
    return plan


def migrate_operational_data(
    v1_root: Path,
    v2_root: Path,
    *,
    apply: bool = False,
    authorization: DirectRouteAuthorization | None = None,
) -> dict:
    if not apply:
        return _migrate_operational_data(v1_root, v2_root, apply=False)
    settings = get_settings()
    with guard_direct_route(
        settings,
        authorization,
        operation=MIGRATION_OPERATION,
    ):
        return _migrate_operational_data(v1_root, v2_root, apply=True)


def _migrate_operational_data(
    v1_root: Path,
    v2_root: Path,
    *,
    apply: bool,
) -> dict:
    source_root, destination_root = _validated_roots(v1_root, v2_root)
    plan = build_migration_plan(source_root, destination_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = destination_root / "data" / "migration_backups" / timestamp
    migrated: list[str] = []
    backups: list[str] = []
    errors: list[str] = []
    ignored = _ignored_paths(source_root)

    if apply:
        _backup_existing_targets(destination_root, backup_root, backups)
        for item in plan:
            relative = Path(item["relative_path"])
            source = _safe_child(source_root, relative)
            destination = _safe_child(destination_root, relative)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(
                    source,
                    destination,
                    private=relative.suffix.lower() != ".pdf",
                )
                migrated.append(relative.as_posix())
            except Exception as exc:
                errors.append(f"{relative.as_posix()}: {exc}")

    report = {
        "mode": "apply" if apply else "dry-run",
        "v1_root": str(source_root),
        "v2_root": str(destination_root),
        "planned_count": len(plan),
        "migrated_count": len(migrated),
        "backup_count": len(backups),
        "planned_files": plan,
        "migrated_files": migrated,
        "backed_up_files": backups,
        "ignored_files": ignored,
        "errors": errors,
        "json_report_path": None,
        "markdown_report_path": None,
    }
    if apply:
        logs_dir = destination_root / "data" / "logs"
        json_path = logs_dir / "migracao_v1_v2.json"
        markdown_path = logs_dir / "migracao_v1_v2.md"
        report["json_report_path"] = str(json_path)
        report["markdown_report_path"] = str(markdown_path)
        atomic_write_json(json_path, report, private=True)
        atomic_write_text(markdown_path, _migration_markdown(report), private=True)
    return report


def _backup_existing_targets(
    destination_root: Path,
    backup_root: Path,
    backups: list[str],
) -> None:
    targets = (*_SINGLE_FILES, _DOWNLOADS_DIR)
    for relative in targets:
        target = _safe_child(destination_root, relative)
        if not target.exists():
            continue
        if target.is_file():
            backup = _safe_child(backup_root, relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(target, backup, private=False)
            backups.append(relative.as_posix())
            continue
        for existing in sorted(target.rglob("*")):
            if existing.is_symlink():
                raise ValueError(f"Link simbólico não permitido no backup: {existing}")
            if not existing.is_file():
                continue
            existing_relative = existing.relative_to(destination_root)
            backup = _safe_child(backup_root, existing_relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(existing, backup, private=False)
        backups.append(relative.as_posix())


def _excluded_download_file(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    lowered_name = path.name.lower()
    return (
        path.suffix.lower() in _TEMPORARY_SUFFIXES
        or path.suffix.lower() in {".xls", ".xlsx", ".xlsm"}
        or lowered_name == "storage_state.json"
        or any(marker in lowered_name for marker in ("cookie", "token", "session"))
        or "__pycache__" in lowered_parts
        or ".pytest_cache" in lowered_parts
    )


def _ignored_paths(source_root: Path) -> list[dict]:
    ignored = []
    for relative in _FORBIDDEN_PATHS:
        if (source_root / relative).exists():
            ignored.append({"path": relative.as_posix(), "reason": "não permitido"})
    downloads = source_root / _DOWNLOADS_DIR
    if downloads.exists():
        for path in sorted(downloads.rglob("*")):
            if path.is_file() and _excluded_download_file(path):
                ignored.append(
                    {
                        "path": path.relative_to(source_root).as_posix(),
                        "reason": "temporário, sessão, bytecode ou planilha",
                    }
                )
    return ignored


def _migration_markdown(report: dict) -> str:
    lines = [
        "# Migração operacional V1 → V2",
        "",
        f"- Modo: {report['mode']}",
        f"- Planejados: {report['planned_count']}",
        f"- Migrados: {report['migrated_count']}",
        f"- Backups: {report['backup_count']}",
        f"- Erros: {len(report['errors'])}",
        "",
        "## Arquivos planejados",
    ]
    lines.extend(
        f"- `{item['relative_path']}`" for item in report["planned_files"]
    )
    lines.extend(["", "## Arquivos migrados"])
    lines.extend(f"- `{path}`" for path in report["migrated_files"])
    lines.extend(["", "## Itens ignorados"])
    lines.extend(
        f"- `{item['path']}` — {item['reason']}" for item in report["ignored_files"]
    )
    lines.extend(["", "## Backups criados"])
    lines.extend(f"- `{path}`" for path in report["backed_up_files"])
    lines.extend(["", "## Erros"])
    lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def _validated_roots(v1_root: Path, v2_root: Path) -> tuple[Path, Path]:
    source_root = Path(v1_root).resolve(strict=True)
    destination_root = Path(v2_root).resolve(strict=True)
    if not source_root.is_dir() or not destination_root.is_dir():
        raise ValueError("As raízes V1 e V2 devem ser diretórios existentes.")
    if source_root == destination_root:
        raise ValueError("As raízes V1 e V2 devem ser diferentes.")
    return source_root, destination_root


def _safe_child(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Caminho relativo inválido: {relative}")
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"Caminho fora da raiz permitida: {candidate}") from exc
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra dados operacionais da V1 para V2.")
    parser.add_argument("--v1-root", type=Path, required=True)
    parser.add_argument("--v2-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print("Status: BLOQUEADO")
        print("Migração apply direta desativada nesta etapa; use --dry-run.")
        return 2

    report = migrate_operational_data(
        args.v1_root,
        args.v2_root,
        apply=args.apply,
    )
    print(f"Modo: {report['mode']}")
    print(f"Arquivos planejados: {report['planned_count']}")
    print(f"Arquivos migrados: {report['migrated_count']}")
    print(f"Backups criados: {report['backup_count']}")
    for item in report["planned_files"]:
        action = "backup + substituir" if item["destination_exists"] else "copiar"
        print(f"- {item['relative_path']} | {action}")
    if report["json_report_path"]:
        print(f"Relatório JSON: {report['json_report_path']}")
        print(f"Relatório Markdown: {report['markdown_report_path']}")
    elif not args.apply:
        print("Dry-run: nenhum arquivo foi alterado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
