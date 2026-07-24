from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from automacao_gd.infrastructure.config import Settings
from automacao_gd.infrastructure.files.paths import (
    ensure_path_within_root,
    validate_protocol_component,
)
from automacao_gd.infrastructure.pdf.service import validate_pdf_input
from automacao_gd.infrastructure.persistence.atomic import atomic_write_json
from automacao_gd.infrastructure.state.pipeline_state import utc_now_iso


def _settings(**overrides) -> Settings:
    defaults = {
        "PLANILHA_PATH": "planilha.xlsx",
        "CLIENTES_ROOT": "clientes",
        "PORTAL_GD_URL": "https://example.com/",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_remote_cdp_is_blocked_by_default() -> None:
    with pytest.raises(ValidationError, match="CDP remoto bloqueado"):
        _settings(CDP_MODE=True, CDP_ENDPOINT="http://192.168.1.10:9222")


def test_remote_cdp_can_be_explicitly_allowed() -> None:
    settings = _settings(
        CDP_MODE=True,
        CDP_ENDPOINT="http://192.168.1.10:9222",
        ALLOW_REMOTE_CDP=True,
    )
    assert settings.ALLOW_REMOTE_CDP is True


def test_fuzzy_review_threshold_cannot_exceed_auto_threshold() -> None:
    with pytest.raises(ValidationError, match="FUZZY_REVIEW_MATCH_SCORE"):
        _settings(FUZZY_AUTO_MATCH_SCORE=70, FUZZY_REVIEW_MATCH_SCORE=80)


def test_protocol_component_rejects_path_traversal() -> None:
    with pytest.raises(ValueError):
        validate_protocol_component("../../segredo")
    assert validate_protocol_component("2606184625") == "2606184625"


def test_path_must_remain_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "clientes"
    allowed = root / "cliente" / "arquivo.pdf"
    assert ensure_path_within_root(root, allowed) == allowed.resolve(strict=False)
    with pytest.raises(ValueError):
        ensure_path_within_root(root, tmp_path / "fora.pdf")


def test_atomic_json_write_replaces_complete_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 1})
    atomic_write_json(target, {"version": 2, "items": [1, 2, 3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "version": 2,
        "items": [1, 2, 3],
    }
    assert not list(tmp_path.glob("*.tmp"))


def test_validate_pdf_rejects_fake_pdf(tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    with pytest.raises(ValueError, match="assinatura PDF"):
        validate_pdf_input(fake)


def test_validate_pdf_accepts_signature(tmp_path: Path) -> None:
    pdf = tmp_path / "valid.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    assert validate_pdf_input(pdf) == pdf


def test_pipeline_timestamp_is_timezone_aware_utc() -> None:
    value = utc_now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
