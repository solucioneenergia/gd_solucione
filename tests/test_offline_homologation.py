from __future__ import annotations

import importlib


def test_main_entrypoints_and_core_modules_are_importable() -> None:
    module_names = [
        "app",
        "desktop_app",
        "apps.desktop.main",
        "automacao_gd.presentation.cli",
        "automacao_gd.application.full_pipeline",
        "automacao_gd.application.processing_service",
        "automacao_gd.application.equipment_reformatting",
        "automacao_gd.infrastructure.excel.service",
        "automacao_gd.infrastructure.pdf.service",
        "automacao_gd.infrastructure.files.cleanup",
        "automacao_gd.infrastructure.state.pipeline_state",
    ]

    for module_name in module_names:
        assert importlib.import_module(module_name)
