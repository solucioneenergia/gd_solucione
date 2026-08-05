from __future__ import annotations

import pytest

from automacao_gd.domain.errors import PreflightBlockedError
from automacao_gd.application import full_pipeline


def _items(tmp_path, *protocols: str) -> dict:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    for protocol in set(protocols):
        (pdfs / f"{protocol}.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    return {
        "results": [
            {
                "protocol": protocol,
                "download_status": "downloaded",
                "selected_for_processing": True,
                "process_pdf_path": str(pdfs / f"{protocol}.pdf"),
                "source_kind": "portal_new",
            }
            for protocol in protocols
        ]
    }


def test_option5_confirmation_phrase_is_derived_from_validated_limit() -> None:
    policy = full_pipeline.BatchAuthorizationPolicy(
        authorized_max_protocols=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
    )
    authorization = full_pipeline.validate_requested_batch_limit(10, policy)

    assert full_pipeline.build_option5_strong_confirmation(5) == (
        "APLICAR OPÇÃO 5 COM CONCLUSÃO EM 5 PROTOCOLOS"
    )
    assert full_pipeline.build_option5_strong_confirmation(
        authorization.requested_batch_limit
    ) == "APLICAR OPÇÃO 5 COM CONCLUSÃO EM 10 PROTOCOLOS"
    assert full_pipeline.validate_option5_strong_confirmation(
        "APLICAR OPÇÃO 5 COM CONCLUSÃO EM 10 PROTOCOLOS",
        authorization,
    )


@pytest.mark.parametrize(
    "bad_confirmation",
    [
        "SIM",
        "sim",
        "10",
        "5 PROTOCOLOS",
        "APLICAR 10",
        "APLICAR OPÇÃO 5 COM CONCLUSÃO EM 5 PROTOCOLOS",
        "APLICAR OPÇÃO 5 COM CONCLUSÃO EM 10 PROTOCOLOS AGORA",
    ],
)
def test_option5_confirmation_rejects_generic_partial_and_wrong_limit(
    bad_confirmation: str,
) -> None:
    authorization = full_pipeline.validate_requested_batch_limit(
        10,
        full_pipeline.BatchAuthorizationPolicy(
            authorized_max_protocols=10,
            authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        ),
    )

    with pytest.raises(full_pipeline.StrongConfirmationError) as exc:
        full_pipeline.validate_option5_strong_confirmation(
            bad_confirmation,
            authorization,
        )

    assert exc.value.code == "STRONG_CONFIRMATION_MISMATCH"


@pytest.mark.parametrize("requested", [0, -1, 6, 10])
def test_production_authorization_policy_fails_closed_before_external_access(
    requested: int,
) -> None:
    with pytest.raises(full_pipeline.BatchAuthorizationError) as exc:
        full_pipeline.validate_requested_batch_limit(
            requested,
            None,
        )

    assert exc.value.code == "BATCH_LIMIT_NOT_AUTHORIZED"


def test_entry_point_normal_policy_cannot_load_synthetic_batch10() -> None:
    policy = full_pipeline.default_batch_authorization_policy()

    assert policy.authorized_max_protocols == 5
    assert policy.authorization_scope == "CONTROLLED_PRODUCTION_V2_0_1"
    with pytest.raises(full_pipeline.BatchAuthorizationError):
        full_pipeline.validate_requested_batch_limit(10, policy)


def test_frozen_batch_selects_exactly_10_and_drops_11th_with_deduplication(
    tmp_path,
) -> None:
    summary = _items(
        tmp_path,
        "SYNTH0001",
        "SYNTH0002",
        "SYNTH0003",
        "SYNTH0004",
        "SYNTH0005",
        "SYNTH0006",
        "SYNTH0007",
        "SYNTH0008",
        "SYNTH0009",
        "SYNTH0010",
        "SYNTH0011",
        "SYNTH0003",
        "SYNTH0012",
    )
    summary["results"][1]["download_status"] = "existing_pdf_after_skip"
    summary["results"][2]["source_kind"] = "resumed"

    authorization = full_pipeline.validate_requested_batch_limit(
        10,
        full_pipeline.BatchAuthorizationPolicy(
            authorized_max_protocols=10,
            authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        ),
    )
    limited = full_pipeline.apply_authorized_global_protocol_limit(
        summary,
        authorization,
    )

    assert limited.frozen_batch.protocols == tuple(
        f"SYNTH{i:04d}" for i in range(1, 11)
    )
    assert limited.frozen_batch.unique_before_limit == 12
    assert limited.frozen_batch.dropped_by_limit == 2
    assert limited.frozen_batch.duplicate_protocols_in_frozen_batch == 0
    assert limited.frozen_batch.protocols_added_after_freeze == 0
    assert limited.summary["protocols_dropped_by_global_limit"] == [
        "SYNTH0011",
        "SYNTH0012",
    ]
    dropped = [
        item["protocol"]
        for item in limited.summary["results"]
        if item.get("global_limit_status") == "excluded_by_global_limit"
    ]
    assert "SYNTH0011" in dropped


def test_frozen_batch_scope_violation_blocks_unknown_protocol_after_freeze() -> None:
    batch = full_pipeline.FrozenProtocolBatch(
        requested_limit=10,
        authorized_limit=10,
        authorization_scope="SYNTHETIC_BATCH10_VALIDATION",
        protocols=("SYNTH0001", "SYNTH0002"),
        unique_before_limit=2,
        dropped_by_limit=0,
    )

    with pytest.raises(full_pipeline.FrozenBatchScopeError) as exc:
        batch.validate_phase_protocols(
            ["SYNTH0001", "SYNTH9999"],
            phase="processing",
        )

    assert exc.value.code == "FROZEN_BATCH_SCOPE_VIOLATION"


class _EntryPointSettings:
    MAX_COMPLETED_TO_PROCESS = 10
    option5_execution_lock_path = None


def test_unauthorized_batch_limit_blocks_before_external_resources(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(full_pipeline, "run_preflight", lambda *a, **k: calls.append("preflight"))
    monkeypatch.setattr(full_pipeline, "_run_download_step", lambda *a, **k: calls.append("download"))
    monkeypatch.setattr(
        full_pipeline,
        "process_downloaded_pdfs",
        lambda *a, **k: calls.append("processing"),
    )

    with pytest.raises(PreflightBlockedError) as exc:
        full_pipeline.run_full_cdp_pipeline(_EntryPointSettings())

    assert exc.value.code == "BATCH_LIMIT_NOT_AUTHORIZED"
    assert calls == []


def test_reentrant_global_lock_blocks_before_external_resources(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Settings:
        MAX_COMPLETED_TO_PROCESS = 5
        option5_execution_lock_path = tmp_path / "option5_execution.lock"

    monkeypatch.setattr(full_pipeline, "run_preflight", lambda *a, **k: calls.append("preflight"))
    monkeypatch.setattr(full_pipeline, "_run_download_step", lambda *a, **k: calls.append("download"))

    with full_pipeline.ExecutionLock(
        Settings.option5_execution_lock_path,
        execution_id="SYNTH-HELD",
        operation="option5",
        requested_batch_limit=5,
        authorization_scope="CONTROLLED_PRODUCTION_V2_0_1",
    ):
        with pytest.raises(PreflightBlockedError) as exc:
            full_pipeline.run_full_cdp_pipeline(Settings())

    assert exc.value.code == "GLOBAL_EXECUTION_LOCK_REENTRANT"
    assert calls == []
