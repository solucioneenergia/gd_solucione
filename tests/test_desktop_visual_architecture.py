from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_IMPORTS = {
    "playwright",
    "openpyxl",
    "automacao_gd.infrastructure.portal.cdp_service",
    "automacao_gd.infrastructure.excel.service",
}


def test_apps_desktop_visual_does_not_import_forbidden_infrastructure() -> None:
    for path in Path("apps/desktop").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = None
            if isinstance(node, ast.ImportFrom):
                imported = node.module
            elif isinstance(node, ast.Import):
                imported = node.names[0].name
            if imported:
                assert not any(
                    imported == forbidden or imported.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_IMPORTS
                ), f"{path} importa dependência proibida: {imported}"


def test_desktop_window_uses_qwebengine_and_qwebchannel() -> None:
    source = Path("apps/desktop/window.py").read_text(encoding="utf-8")

    assert "QWebEngineView" in source
    assert "QWebChannel" in source
    assert "registerObject(\"backend\"" in source


def test_desktop_visual_tests_do_not_reference_real_external_resources() -> None:
    forbidden_portal = "gdneoenergia" + "pernambuco.neoenergia.com"
    forbidden_drive = "Z:" + "\\Clientes"
    for path in [
        Path("tests/test_desktop_visual_structure.py"),
        Path("tests/test_desktop_visual_assets.py"),
        Path("tests/test_desktop_visual_architecture.py"),
        Path("tests/test_desktop_sidebar_visual.py"),
    ]:
        source = path.read_text(encoding="utf-8")
        assert forbidden_portal not in source
        assert forbidden_drive not in source
