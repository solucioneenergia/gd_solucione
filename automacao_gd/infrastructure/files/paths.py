"""Regras de segurança para nomes e caminhos controlados por dados externos."""

from __future__ import annotations

import re
from pathlib import Path

_PROTOCOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def validate_protocol_component(protocol: str) -> str:
    value = str(protocol or "").strip()
    if not _PROTOCOL_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Protocolo contém caracteres inválidos para uso em caminho.")
    return value


def ensure_path_within_root(root: Path, candidate: Path) -> Path:
    root_resolved = Path(root).resolve(strict=False)
    candidate_resolved = Path(candidate).resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"Caminho de destino fora da raiz permitida: {candidate_resolved}"
        ) from exc
    return candidate_resolved
