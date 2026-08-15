from __future__ import annotations

import inspect

from automacao_gd.application import full_pipeline
from automacao_gd.application import op5_contracts
from automacao_gd.application import op5_plan
from automacao_gd.application import op5_selection


def test_full_pipeline_reexports_op5_contract_types_and_authorization_helpers() -> None:
    assert full_pipeline.BatchAuthorizationError is op5_contracts.BatchAuthorizationError
    assert full_pipeline.StrongConfirmationError is op5_contracts.StrongConfirmationError
    assert full_pipeline.FrozenBatchScopeError is op5_contracts.FrozenBatchScopeError
    assert full_pipeline.BatchAuthorizationPolicy is op5_contracts.BatchAuthorizationPolicy
    assert full_pipeline.BatchAuthorization is op5_contracts.BatchAuthorization
    assert full_pipeline.FrozenProtocolBatch is op5_contracts.FrozenProtocolBatch
    assert full_pipeline.FrozenPdfArtifact is op5_contracts.FrozenPdfArtifact
    assert full_pipeline.FrozenPdfScope is op5_contracts.FrozenPdfScope
    assert full_pipeline.LimitedProtocolSelection is op5_contracts.LimitedProtocolSelection
    assert full_pipeline.FrozenDryRunPlan is op5_contracts.FrozenDryRunPlan
    assert (
        full_pipeline.default_batch_authorization_policy
        is op5_contracts.default_batch_authorization_policy
    )
    assert (
        full_pipeline.batch_authorization_policy_from_settings
        is op5_contracts.batch_authorization_policy_from_settings
    )
    assert full_pipeline.validate_requested_batch_limit is op5_contracts.validate_requested_batch_limit
    assert full_pipeline.build_option5_strong_confirmation is (
        op5_contracts.build_option5_strong_confirmation
    )
    assert full_pipeline.validate_option5_strong_confirmation is (
        op5_contracts.validate_option5_strong_confirmation
    )


def test_full_pipeline_reexports_op5_selection_helpers() -> None:
    assert (
        full_pipeline.apply_authorized_global_protocol_limit
        is op5_selection.apply_authorized_global_protocol_limit
    )
    assert full_pipeline.freeze_selected_pdf_scope is op5_selection.freeze_selected_pdf_scope
    assert full_pipeline.attach_frozen_pdf_scope is op5_selection.attach_frozen_pdf_scope
    assert full_pipeline._apply_global_protocol_limit is op5_selection.apply_global_protocol_limit
    assert full_pipeline._pdf_paths_for_processing is op5_selection.pdf_paths_for_processing
    assert full_pipeline._refresh_processing_selection_totals is (
        op5_selection.refresh_processing_selection_totals
    )
    assert full_pipeline._download_item_sent_to_processing is (
        op5_selection.download_item_sent_to_processing
    )
    assert full_pipeline._download_item_is_metadata_only_for_processing is (
        op5_selection.download_item_is_metadata_only_for_processing
    )


def test_full_pipeline_reexports_op5_plan_helpers() -> None:
    assert full_pipeline._build_op5_plan_payload is op5_plan.build_op5_plan_payload
    assert full_pipeline._planned_excel_actions is op5_plan.planned_excel_actions
    assert full_pipeline._build_op5_workbook_coverage is op5_plan.build_op5_workbook_coverage
    assert full_pipeline._op5_planning_candidate_limit is op5_plan.op5_planning_candidate_limit
    assert full_pipeline._op5_payload_can_persist_plan is op5_plan.op5_payload_can_persist_plan
    assert (
        full_pipeline._download_summary_for_planned_actions
        is op5_plan.download_summary_for_planned_actions
    )
    assert full_pipeline._frozen_portal_metadata_by_protocol is (
        op5_plan.frozen_portal_metadata_by_protocol
    )
    assert full_pipeline._processing_item_is_pending_review is (
        op5_plan.processing_item_is_pending_review
    )


def test_full_pipeline_keeps_public_pipeline_entrypoint_signature() -> None:
    signature = inspect.signature(full_pipeline.run_full_cdp_pipeline)

    assert list(signature.parameters) == [
        "settings",
        "progress_callback",
        "confirmation",
    ]
    assert signature.parameters["progress_callback"].default is None
    assert signature.parameters["confirmation"].default is None
