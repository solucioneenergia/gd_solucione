from __future__ import annotations

import argparse
import hashlib
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
from scripts.privacy_scan import scan_entries
from scripts.validate_engineering_foundation import REQUIRED_FILES as FOUNDATION_REQUIRED_FILES


CURRENT_VERSION = "2.0.2"
_REQUIRED_FILES = {
    ".env.example",
    "README.md",
    "requirements.txt",
    "pyproject.toml",
    "release-manifest.json",
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


def _path_safety_reason(raw_name: str) -> str | None:
    normalized = _normalized_archive_name(raw_name)
    pure = PurePosixPath(normalized)
    if raw_name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", normalized):
        return "absolute_path"
    if not pure.parts or normalized == "." or ".." in pure.parts:
        return "path_traversal"
    return release_exclusion_reason(pure)


def _read_project_version(entries: dict[str, bytes]) -> str | None:
    try:
        project = tomllib.loads(entries["pyproject.toml"].decode("utf-8"))
        return str(project["project"]["version"])
    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _read_python_version(entries: dict[str, bytes]) -> str | None:
    try:
        payload = entries["automacao_gd/__init__.py"].decode("utf-8")
    except (KeyError, UnicodeDecodeError):
        return None
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', payload, re.MULTILINE)
    return match.group(1) if match else None


def _read_frontend_version(entries: dict[str, bytes]) -> str | None:
    try:
        payload = json.loads(entries["apps/desktop/frontend/package.json"])
        return str(payload["version"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_archive(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, bytes], list[str], list[str]]:
    normalized_entries: dict[str, bytes] = {}
    raw_names: list[str] = []
    normalized_names: list[str] = []
    seen: set[str] = set()
    collisions: list[str] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        raw_names.append(info.filename)
        normalized = _normalized_archive_name(info.filename)
        normalized_names.append(normalized)
        key = normalized.casefold()
        if key in seen:
            collisions.append("duplicate_path")
            continue
        seen.add(key)
        normalized_entries[normalized] = (
            b"" if _path_safety_reason(info.filename) else archive.read(info)
        )
    return normalized_entries, raw_names, collisions


def _manifest_errors(
    entries: dict[str, bytes],
    *,
    expected_head_sha: str | None,
    versions: dict[str, str | None],
) -> list[str]:
    try:
        manifest = json.loads(entries["release-manifest.json"])
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return ["RELEASE_PROVENANCE_INVALID"]
    if not isinstance(manifest, dict):
        return ["RELEASE_PROVENANCE_INVALID"]

    errors: list[str] = []
    head_sha = manifest.get("head_sha")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        errors.append("RELEASE_PROVENANCE_INVALID")
    elif expected_head_sha is not None and head_sha.casefold() != expected_head_sha.casefold():
        errors.append("RELEASE_PROVENANCE_INVALID")

    if manifest.get("versions") != versions:
        errors.append("RELEASE_PROVENANCE_INVALID")
    privacy = manifest.get("privacy_scan")
    if not isinstance(privacy, dict) or privacy.get("valid") is not True:
        errors.append("RELEASE_PROVENANCE_INVALID")

    generated = manifest.get("generated_artifacts")
    actual_generated = sorted(
        name
        for name in entries
        if name.startswith("apps/desktop/frontend/dist/")
    )
    if (
        not isinstance(generated, list)
        or generated != actual_generated
        or any(not isinstance(name, str) for name in generated)
    ):
        errors.append("RELEASE_PROVENANCE_INVALID")

    generated_source = manifest.get("generated_source")
    if not isinstance(generated_source, dict):
        errors.append("RELEASE_PROVENANCE_INVALID")
    else:
        source_files = generated_source.get("source_files")
        declared_files = manifest.get("files")
        if (
            generated_source.get("root") != "apps/desktop/frontend"
            or not isinstance(source_files, list)
            or not isinstance(declared_files, dict)
            or source_files
            != sorted(
                name
                for name in declared_files
                if name.startswith("apps/desktop/frontend/")
                and not name.startswith("apps/desktop/frontend/dist/")
            )
        ):
            errors.append("RELEASE_PROVENANCE_INVALID")
        else:
            source_payload = "\n".join(
                f"{name}:{declared_files[name]}" for name in source_files
            ).encode("utf-8")
            if hashlib.sha256(source_payload).hexdigest() != generated_source.get(
                "source_digest"
            ):
                errors.append("RELEASE_PROVENANCE_INVALID")

    declared_files = manifest.get("files")
    content_entries = {
        name: payload for name, payload in entries.items() if name != "release-manifest.json"
    }
    if not isinstance(declared_files, dict) or set(declared_files) != set(content_entries):
        errors.append("RELEASE_CONTENT_MISMATCH")
    elif any(
        not isinstance(declared_files[name], str)
        or hashlib.sha256(payload).hexdigest() != declared_files[name]
        for name, payload in content_entries.items()
    ):
        errors.append("RELEASE_CONTENT_MISMATCH")
    return errors


def validate_release_zip(
    zip_path: Path,
    *,
    reports_dir: Path | None = None,
    expected_version: str = CURRENT_VERSION,
    expected_head_sha: str | None = None,
) -> dict:
    archive_path = Path(zip_path).resolve(strict=True)
    violations: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        entries, raw_names, collisions = _read_archive(archive)
    names = list(entries)
    violations.extend(collisions)
    for raw_name in raw_names:
        reason = _path_safety_reason(raw_name)
        if reason is not None:
            violations.append(reason)

    duplicate_casefold = [
        name for name, count in Counter(item.casefold() for item in names).items() if count > 1
    ]
    violations.extend("duplicate_path" for _ in duplicate_casefold)

    missing = sorted(file for file in _REQUIRED_FILES if file not in names)
    missing.extend(
        sorted(
            prefix
            for prefix in _REQUIRED_PREFIXES
            if not any(name.startswith(prefix) for name in names)
        )
    )
    version = _read_project_version(entries)
    python_version = _read_python_version(entries)
    frontend_version = _read_frontend_version(entries)
    versions = {
        "project": version,
        "python_package": python_version,
        "frontend": frontend_version,
    }

    privacy_entries = {
        name: payload for name, payload in entries.items() if name != "release-manifest.json"
    }
    privacy_report = scan_entries(privacy_entries)
    violations.extend(str(item["rule"]) for item in privacy_report["findings"])

    errors: list[str] = []
    if violations:
        errors.append(f"{len(violations)} categoria(s) proibida(s) encontrada(s)")
    if missing:
        errors.append("Itens obrigatórios ausentes: " + ", ".join(missing))
    if {version, python_version, frontend_version} != {expected_version}:
        errors.append("Versão inconsistente ou ausente no pacote")
    errors.extend(
        _manifest_errors(
            entries,
            expected_head_sha=expected_head_sha,
            versions=versions,
        )
    )
    errors = list(dict.fromkeys(errors))

    report = {
        "valid": not errors,
        "zip_path": str(archive_path),
        "total_entries": len(raw_names),
        "sensitive_paths": sorted(set(violations)),
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
        shareable = {
            key: value
            for key, value in report.items()
            if key not in {"zip_path", "json_report_path", "markdown_report_path"}
        }
        atomic_write_json(json_path, shareable, private=True)
        atomic_write_text(markdown_path, _validation_markdown(shareable), private=True)
        report["json_report_path"] = str(json_path)
        report["markdown_report_path"] = str(markdown_path)
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
    parser.add_argument("--expected-head-sha", required=True)
    args = parser.parse_args()
    report = validate_release_zip(
        args.zip_path,
        reports_dir=args.reports_dir or args.zip_path.parent / f"{args.zip_path.stem}_reports",
        expected_version=args.expected_version,
        expected_head_sha=args.expected_head_sha,
    )
    print(f"Status: {'APROVADO' if report['valid'] else 'REPROVADO'}")
    print(f"Relatório JSON: {report['json_report_path']}")
    print(f"Relatório Markdown: {report['markdown_report_path']}")
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
