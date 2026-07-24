from __future__ import annotations

import importlib
from pathlib import Path


def test_apps_desktop_structure_exists() -> None:
    expected = [
        Path("apps/desktop/__init__.py"),
        Path("apps/desktop/main.py"),
        Path("apps/desktop/window.py"),
        Path("apps/desktop/bridge/automation_bridge.py"),
        Path("apps/desktop/bridge/progress_bridge.py"),
        Path("apps/desktop/bridge/file_bridge.py"),
        Path("apps/desktop/workers/automation_worker.py"),
        Path("apps/desktop/resources/__init__.py"),
        Path("apps/desktop/frontend/README.md"),
    ]

    assert all(path.exists() for path in expected)


def test_desktop_app_wrapper_remains_importable() -> None:
    module = importlib.import_module("desktop_app")

    assert callable(module.main)


def test_apps_desktop_main_is_importable() -> None:
    module = importlib.import_module("apps.desktop.main")

    assert callable(module.main)
    assert hasattr(module, "QT_AVAILABLE")


def test_desktop_main_contains_reference_dashboard_sections() -> None:
    source = "\n".join(
        [
            Path("apps/desktop/frontend/index.html").read_text(encoding="utf-8"),
            Path("apps/desktop/frontend/src/App.tsx").read_text(encoding="utf-8"),
        ]
    )

    for expected_text in [
        "Automação GD Neoenergia — Desktop Visual",
        "Portal e Execução",
        "Visualização do Processo",
        "Protocolos recentes",
        "Relatórios",
        "Abrir Edge CDP",
        "Testar conexão CDP",
        "Rodar simulação",
        "Rodar produção",
    ]:
        assert expected_text in source


def test_desktop_main_keeps_single_screen_layout_without_outer_scroll() -> None:
    main_source = Path("apps/desktop/main.py").read_text(encoding="utf-8")
    css_source = Path("apps/desktop/frontend/src/styles/layout.css").read_text(encoding="utf-8")

    assert "QScrollArea" not in main_source
    assert "overflow-y: auto" in css_source
    assert ".table-wrap" in css_source


def test_cli_app_remains_importable() -> None:
    module = importlib.import_module("app")

    assert callable(module.main)


def test_tkinter_legacy_was_not_removed() -> None:
    assert Path("automacao_gd/presentation/tkinter_app.py").exists()
