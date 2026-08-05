from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automacao_gd.infrastructure.persistence.atomic import atomic_write_json, atomic_write_text
from scripts.create_clean_release_zip import release_exclusion_reason
from scripts.validate_engineering_foundation import REQUIRED_FILES as FOUNDATION_REQUIRED_FILES


CURRENT_VERSION = "2.0.2"
_REQUIRED_FILES = {
    ".env.example",
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "desktop_app.py",
    "automacao_gd/__init__.py",
    "apps/__init__.py",
    "apps/desktop/__init__.py",
    "apps/desktop/frontend/package.json",
    "apps/desktop/frontend/pnpm-lock.yaml",
    "apps/desktop/frontend/dist/index.html",
} | set(FOUNDATION_REQUIRED_FILES)
_REQUIRED_PREFIXES = {
    "apps/desktop/frontend/dist/assets/",
    "automacao_gd/",
    "docs/",
    "scripts/",
    "tests/",
}


def _normalized_archive_name(name: str) -> str:
    return PurePosixPath(str(name).replace("\\", "/")).as_posix()


def _path_safety_reason(name: str) -> str | None:
    normalized = _normalized_archive_name(name)
    pure = PurePosixPath(normalized)
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", normalized):
        return "absolute_path"
    if ".." in pure.parts:
        return "path_traversal"
    return release_exclusion_reason(pure)


def _read_project_version(archive: zipfile.ZipFile) -> str | None:
    try:
        payload = archive.read("pyproject.toml").decode("utf-8")
        project = tomllib.loads(payload)
        return str(project["project"]["version"])
    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _read_python_version(archive: zipfile.ZipFile) -> str | None:
    try:
        payload = archive.read("automacao_gd/__init__.py").decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return None
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', payload, re.MULTILINE)
    return match.group(1) if match else None


def _read_frontend_version(archive: zipfile.ZipFile) -> str | None:
    try:
        payload = json.loads(archive.read("apps/desktop/frontend/package.json"))
        return str(payload["version"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def validate_release_zip(
    zip_path: Path,
    *,
    reports_dir: Path | None = None,
    expected_version: str = CURRENT_VERSION,
) -> dict:
    archive_path = Path(zip_path).resolve(strict=True)
    violations: list[str] = []
    version: str | None = None
    python_version: str | None = None
    frontend_version: str | None = None
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = [_normalized_archive_name(name) for name in archive.namelist()]
        for name in names:
            reason = _path_safety_reason(name)
            if reason is not None:
                violations.append(reason)
        version = _read_project_version(archive)
        python_version = _read_python_version(archive)
        frontend_version = _read_frontend_version(archive)

    duplicate_names = [
        name for name, count in Counter(item.casefold() for item in names).items() if count > 1
    ]
    if duplicate_names:
        violations.extend("duplicate_path" for _name in duplicate_names)

    missing = sorted(file for file in _REQUIRED_FILES if file not in names)
    missing.extend(
        sorted(prefix for prefix in _REQUIRED_PREFIXES if not any(name.startswith(prefix) for name in names))
    )

    errors: list[str] = []
    if violations:
        errors.append(f"{len(violations)} caminho(s) proibido(s) encontrado(s)")
    if missing:
        errors.append("Itens obrigatórios ausentes: " + ", ".join(missing))
    versions = {version, python_version, frontend_version}
    if versions != {expected_version}:
        errors.append("Versão inconsistente ou ausente no pacote")

    report = {
        "valid": not errors,
        "zip_path": str(archive_path),
        "total_entries": len(names),
        "sensitive_paths": sorted(violations),
        "missing_required": missing,
        "version": version,
        "python_version": python_version,
        "frontend_version": frontend_version,
        "expected_version": expected_version,
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
        f"- Categorias proibidas: {len(report['sensitive_paths'])}",
        f"- Itens obrigatórios ausentes: {len(report['missing_required'])}",
        f"- Versão esperada: {report['expected_version']}",
        f"- Versão encontrada: {report['version'] or 'ausente'}",
        "",
        "## Categorias proibidas",
    ]
    lines.extend(f"- `{reason}`" for reason in report["sensitive_paths"])
    lines.extend(["", "## Itens obrigatórios ausentes"])
    lines.extend(f"- `{path}`" for path in report["missing_required"])
    lines.extend(["", "## Erros"])
    lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida ZIP limpo da candidata 2.0.2.")
    parser.add_argument("zip_path", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--expected-version", default=CURRENT_VERSION)
    args = parser.parse_args()
    report = validate_release_zip(
        args.zip_path,
        reports_dir=args.reports_dir or args.zip_path.parent / f"{args.zip_path.stem}_reports",
        expected_version=args.expected_version,
    )
    print(f"Status: {'APROVADO' if report['valid'] else 'REPROVADO'}")
    print(f"Relatório JSON: {report['json_report_path']}")
    print(f"Relatório Markdown: {report['markdown_report_path']}")
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
