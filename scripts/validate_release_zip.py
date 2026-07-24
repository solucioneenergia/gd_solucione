from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.persistence.atomic import (
    atomic_write_json,
    atomic_write_text,
)
from scripts.create_clean_release_zip import should_exclude_from_release


_REQUIRED_FILES = {"README.md", "requirements.txt", ".env.example"}
_REQUIRED_PREFIXES = {"automacao_gd/", "docs/", "scripts/", "tests/"}


def validate_release_zip(
    zip_path: Path,
    *,
    reports_dir: Path | None = None,
) -> dict:
    archive_path = Path(zip_path).resolve(strict=True)
    violations: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = [name.replace("\\", "/") for name in archive.namelist()]

    for name in names:
        relative = Path(name)
        lowered_name = relative.name.lower()
        if should_exclude_from_release(relative):
            violations.append(name)
            continue
        if any(marker in lowered_name for marker in ("cookie", "token")):
            violations.append(name)

    missing = sorted(file for file in _REQUIRED_FILES if file not in names)
    missing.extend(
        sorted(prefix for prefix in _REQUIRED_PREFIXES if not any(name.startswith(prefix) for name in names))
    )
    errors = []
    if violations:
        errors.append(f"{len(violations)} caminho(s) sensível(is) encontrado(s)")
    if missing:
        errors.append("Itens obrigatórios ausentes: " + ", ".join(missing))

    report = {
        "valid": not errors,
        "zip_path": str(archive_path),
        "total_entries": len(names),
        "sensitive_paths": violations,
        "missing_required": missing,
        "errors": errors,
        "json_report_path": None,
        "markdown_report_path": None,
    }
    if reports_dir is not None:
        target_dir = Path(reports_dir)
        json_path = target_dir / "release_validation.json"
        markdown_path = target_dir / "release_validation.md"
        report["json_report_path"] = str(json_path)
        report["markdown_report_path"] = str(markdown_path)
        atomic_write_json(json_path, report, private=True)
        atomic_write_text(markdown_path, _validation_markdown(report), private=True)
    return report


def _validation_markdown(report: dict) -> str:
    lines = [
        "# Validação do pacote de release",
        "",
        f"- Status: {'APROVADO' if report['valid'] else 'REPROVADO'}",
        f"- Entradas no ZIP: {report['total_entries']}",
        f"- Caminhos sensíveis: {len(report['sensitive_paths'])}",
        f"- Itens obrigatórios ausentes: {len(report['missing_required'])}",
        "",
        "## Caminhos sensíveis",
    ]
    lines.extend(f"- `{path}`" for path in report["sensitive_paths"])
    lines.extend(["", "## Itens obrigatórios ausentes"])
    lines.extend(f"- `{path}`" for path in report["missing_required"])
    lines.extend(["", "## Erros"])
    lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida ZIP limpo da V2.")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--reports-dir", type=Path, default=PROJECT_ROOT / "data" / "logs")
    args = parser.parse_args()
    report = validate_release_zip(args.zip_path, reports_dir=args.reports_dir)
    print(f"Status: {'APROVADO' if report['valid'] else 'REPROVADO'}")
    print(f"Relatório JSON: {report['json_report_path']}")
    print(f"Relatório Markdown: {report['markdown_report_path']}")
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
