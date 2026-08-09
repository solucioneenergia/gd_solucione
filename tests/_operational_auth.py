from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from automacao_gd.application.operational_guard import (
    OfflineOperationAuthorization,
    authorize_offline_batch,
    build_option4_strong_confirmation,
    prepare_offline_batch,
)


_PROTOCOL = re.compile(r"(?<!\d)(\d{10})(?!\d)")


def authorize_synthetic_pdfs(
    downloads_root: Path,
    pdf_paths: Iterable[Path],
) -> OfflineOperationAuthorization:
    root = Path(downloads_root).resolve(strict=True)
    selections: list[tuple[Path, str]] = []
    for raw_path in pdf_paths:
        candidate = Path(raw_path)
        if not candidate.exists():
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(b"%PDF-1.4\nCONTEUDO SINTETICO\n%%EOF")
        path = candidate.resolve(strict=True)
        match = _PROTOCOL.search(path.stem)
        if match is None:
            raise ValueError("Fixture real exige protocolo sintético de 10 dígitos.")
        selections.append((path.relative_to(root), match.group(1)))
    batch = prepare_offline_batch(
        root,
        requested_limit=len(selections),
        selections=selections,
    )
    return authorize_offline_batch(
        batch,
        build_option4_strong_confirmation(len(selections)),
    )
