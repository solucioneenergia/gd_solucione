from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any


# Frozen, explicitly enumerated fixture namespace. Do not replace this list with a
# range: a path under tests/specs is not proof that an identifier is synthetic.
_OFFICIAL_SYNTHETIC_PROTOCOL_SUFFIXES = {
    "0000",
    "0001",
    "0002",
    "0003",
    "0004",
    "0005",
    "0006",
    "0007",
    "0008",
    "0009",
    "0017",
    "1003",
    "1007",
    "1011",
    "1012",
    "1013",
    "1014",
    "1015",
    "1016",
    "1017",
    "1018",
    "1019",
    "1020",
    "1021",
    "1022",
    "1023",
    "1024",
    "1025",
    "1026",
    "1027",
    "1028",
    "1029",
    "1030",
    "1031",
    "1032",
    "1033",
    "1034",
    "1035",
    "1036",
    "1037",
    "1038",
    "1039",
    "1040",
    "1041",
    "1042",
    "1043",
    "1044",
    "1045",
    "1046",
    "1047",
    "1048",
    "1049",
    "1050",
    "1051",
    "1052",
    "1053",
    "1054",
    "1055",
    "1056",
    "1057",
    "1058",
    "1059",
    "1060",
    "1061",
    "1064",
    "1068",
    "1069",
    "1070",
    "1071",
    "1073",
    "1074",
    "1075",
    "1077",
    "1078",
    "1079",
    "1080",
    "1082",
    "1083",
    "1084",
    "1085",
    "1088",
    "1089",
    "1094",
    "1097",
    "1098",
    "1099",
    "1104",
    "1106",
    "1107",
}
_SYNTHETIC_IDENTIFIERS = {"0000000000"} | {
    f"260000{suffix}" for suffix in _OFFICIAL_SYNTHETIC_PROTOCOL_SUFFIXES
}
_SYNTHETIC_TEXT_MARKERS = {
    "CLIENTE SINTETICO LTDA",
    r"C:\CAMINHO\SINTETICO",
    "/CAMINHO/SINTETICO",
    r"\\SERVIDOR\SINTETICO",
}
_SKIPPED_DIRECTORY_NAMES = {
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
    "node_modules",
    "venv",
}
_SKIPPED_ROOT_DIRECTORY_NAMES = {
    "build",
    "data",
    "downloads",
    "logs",
    "outputs",
    "profiles",
    "temp",
    "tmp",
}
_HOME_PATH_PREFIX = "/" + "home/"
_SCANNABLE_DIST_SUFFIXES = (
    "apps/desktop/frontend/dist",
    "frontend/dist",
)
_SKIPPED_FILE_NAMES = {".coverage"}
_TEXT_PATTERNS = (
    (
        "personal_absolute_path",
        re.compile(
            r"(?i)(?:[a-z]:(?:\\+|/+)(?:users|documents and settings)(?:\\+|/+)"
            r"[^\s\"'<>]+|"
            + re.escape(_HOME_PATH_PREFIX)
            + r"[^\s\"'<>]+|\\\\[^\\\s]+\\[^\\\s]+)"
        ),
    ),
    (
        "credential_value",
        re.compile(r"(?i)\bsk-(?:live|prod|test)-?[a-z0-9_-]{8,}\b"),
    ),
    (
        "credential_value",
        re.compile(
            r"(?i)\b(?:api[_-]?key|cookie|password|secret|token)\b\s*[:=]\s*"
            r"[\"'][^\"'\s]{8,}[\"']"
        ),
    ),
    (
        "cpf",
        re.compile(r"(?<!\d)\d{3}[.]\d{3}[.]\d{3}-\d{2}(?!\d)"),
    ),
    (
        "cnpj",
        re.compile(r"(?<!\d)\d{2}[.]\d{3}[.]\d{3}/\d{4}-\d{2}(?!\d)"),
    ),
    (
        "operational_identifier",
        re.compile(r"(?<!\d)2[2-9]\d{8}(?!\d)"),
    ),
    (
        "operational_identifier",
        re.compile(
            r"(?i)\b(?:protocol(?:o)?|uc|unidade[_ ]?consumidora)\b"
            r"[^\r\n\d]{0,24}\d{10}(?!\d)"
        ),
    ),
    (
        "client_name",
        re.compile(
            r"(?i)\b(?:client_name|customer_name|cliente)\b\s*[:=]\s*"
            r"[\"'][a-zÀ-öø-ÿ][a-zÀ-öø-ÿ .'-]{4,}[\"']"
        ),
    ),
)
_OPERATIONAL_SUFFIXES = {".pdf", ".xls", ".xlsx", ".xlsm"}


def _normalized(name: str | Path | PurePosixPath) -> str:
    return PurePosixPath(str(name).replace("\\", "/")).as_posix()


def _match_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _is_synthetic(match: str, path: str) -> bool:
    stripped = match.strip('"\'')
    if stripped in _SYNTHETIC_IDENTIFIERS:
        return True
    if stripped in {"000.000.000-00", "00.000.000/0000-00"}:
        return True
    identifiers = re.findall(r"(?<!\d)\d{10}(?!\d)", stripped)
    if identifiers and all(
        item in _SYNTHETIC_IDENTIFIERS
        for item in identifiers
    ):
        return True
    inner_path = path.rsplit("!", 1)[-1]
    parts = tuple(part.casefold() for part in PurePosixPath(inner_path).parts)
    synthetic_context = bool(parts and parts[0] in {"specs", "tests"})
    folded = stripped.casefold()
    if synthetic_context and "cliente sintetico" in folded:
        return True
    return any(marker.casefold() in folded for marker in _SYNTHETIC_TEXT_MARKERS)


def _is_hex_digest_match(text: str, start: int, end: int) -> bool:
    left = start
    while left > 0 and text[left - 1] in "0123456789abcdefABCDEF":
        left -= 1
    right = end
    while right < len(text) and text[right] in "0123456789abcdefABCDEF":
        right += 1
    token = text[left:right]
    return len(token) in {40, 64} and bool(re.fullmatch(r"[0-9a-fA-F]+", token))


def _finding(
    rule: str, path: str, text: str, start: int, end: int
) -> dict[str, object]:
    line = text.count("\n", 0, start) + 1
    last_break = text.rfind("\n", 0, start)
    column = start - last_break
    return {
        "rule": rule,
        "path": path,
        "line": line,
        "column": column,
        "match_hash": _match_hash(text[start:end]),
    }


def _path_findings(name: str) -> list[dict[str, object]]:
    normalized = _normalized(name)
    pure = PurePosixPath(normalized)
    lowered = pure.name.casefold()
    rules: list[str] = []
    if lowered == ".env" or lowered.startswith(".env.") and lowered != ".env.example":
        rules.append("environment_file")
    if any(marker in lowered for marker in ("cookie", "credential", "secret", "token")):
        rules.append("credential_artifact")
    if pure.suffix.casefold() in _OPERATIONAL_SUFFIXES:
        parts = tuple(part.casefold() for part in pure.parts)
        synthetic = (
            len(parts) >= 4
            and parts[:3] == ("tests", "fixtures", "synthetic")
            and pure.name.casefold().startswith("synthetic_")
        )
        if not synthetic:
            rules.append("operational_document")
    return [
        {
            "rule": rule,
            "path": "[sensitive-path]",
            "line": 0,
            "column": 0,
            "match_hash": _match_hash(normalized + rule),
        }
        for rule in rules
    ]


def _scan_entry(name: str, payload: bytes) -> list[dict[str, object]]:
    normalized = _normalized(name)
    findings = _path_findings(normalized)
    texts: list[str] = []
    if b"\x00" in payload[:4096]:
        printable = re.findall(rb"[\x20-\x7e]{6,}", payload)
        texts.append("\n".join(item.decode("ascii") for item in printable))
        even = payload[0::2]
        odd = payload[1::2]
        even_nuls = even.count(0) / max(1, len(even))
        odd_nuls = odd.count(0) / max(1, len(odd))
        if odd_nuls >= 0.3 and even_nuls <= 0.1:
            texts.append(payload.decode("utf-16-le", errors="replace"))
        elif even_nuls >= 0.3 and odd_nuls <= 0.1:
            texts.append(payload.decode("utf-16-be", errors="replace"))
    else:
        texts.append(payload.decode("utf-8", errors="replace"))
    seen: set[tuple[str, str]] = set()
    for text in texts:
        for rule, pattern in _TEXT_PATTERNS:
            for match in pattern.finditer(text):
                if rule == "operational_identifier" and _is_hex_digest_match(
                    text, match.start(), match.end()
                ):
                    continue
                if _is_synthetic(match.group(0), normalized):
                    continue
                match_hash = _match_hash(match.group(0))
                key = (rule, match_hash)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    _finding(rule, normalized, text, match.start(), match.end())
                )
    return findings


def scan_entries(entries: Mapping[str, bytes]) -> dict[str, Any]:
    findings: list[dict[str, object]] = []
    for name in sorted(entries, key=lambda item: _normalized(item).casefold()):
        findings.extend(_scan_entry(name, entries[name]))
    return {
        "valid": not findings,
        "scanned_files": len(entries),
        "finding_count": len(findings),
        "findings": findings,
    }


def _iter_scannable_files(candidate: Path, base: Path) -> Iterable[Path]:
    if candidate.is_file():
        yield candidate
        return
    for directory, child_directories, filenames in os.walk(candidate, topdown=True):
        current = Path(directory)
        child_directories[:] = [
            name
            for name in child_directories
            if name.casefold() not in _SKIPPED_DIRECTORY_NAMES
            and not (
                len(current.joinpath(name).relative_to(base).parts) == 1
                and name.casefold() in _SKIPPED_ROOT_DIRECTORY_NAMES
            )
            and not name.casefold().endswith(".egg-info")
            and (
                name.casefold() != "dist"
                or current.joinpath(name)
                .as_posix()
                .casefold()
                .endswith(_SCANNABLE_DIST_SUFFIXES)
            )
        ]
        for filename in filenames:
            if filename.casefold() not in _SKIPPED_FILE_NAMES:
                yield current / filename


def scan_paths(root: Path, relative_paths: Iterable[Path]) -> dict[str, Any]:
    base = Path(root).resolve(strict=True)
    entries: dict[str, bytes] = {}
    for requested in relative_paths:
        candidate = (base / requested).resolve(strict=True)
        try:
            relative = candidate.relative_to(base)
        except ValueError as exc:
            raise ValueError("O alvo do scanner deve permanecer dentro da raiz.") from exc
        if (
            len(relative.parts) == 1
            and relative.name.casefold() in _SKIPPED_ROOT_DIRECTORY_NAMES
        ):
            continue
        for path in _iter_scannable_files(candidate, base):
            if not path.is_file():
                continue
            display_path = path.relative_to(base).as_posix()
            if path.suffix.casefold() in {".zip", ".whl"}:
                with zipfile.ZipFile(path, "r") as archive:
                    for info in archive.infolist():
                        if not info.is_dir():
                            entry_name = _normalized(info.filename)
                            display_entry = f"{display_path}!{entry_name}"
                            entries[display_entry] = (
                                b"" if _path_findings(entry_name) else archive.read(info)
                            )
                continue
            entries[display_path] = (
                b"" if _path_findings(display_path) else path.read_bytes()
            )
    return scan_entries(entries)


def scan_archive(archive_path: Path) -> dict[str, Any]:
    path = Path(archive_path).resolve(strict=True)
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(path, "r") as archive:
        for info in archive.infolist():
            if not info.is_dir():
                entry_name = _normalized(info.filename)
                entries[entry_name] = (
                    b"" if _path_findings(entry_name) else archive.read(info)
                )
    return scan_entries(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa o gate de privacidade.")
    parser.add_argument("root", type=Path)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    report = scan_paths(args.root, args.paths)
    print(
        json.dumps(
            {
                "valid": report["valid"],
                "scanned_files": report["scanned_files"],
                "finding_count": report["finding_count"],
                "rules": sorted({item["rule"] for item in report["findings"]}),
            },
            ensure_ascii=False,
        )
    )
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
