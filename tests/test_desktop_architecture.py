from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_DESKTOP_IMPORTS = {
    "playwright",
    "openpyxl",
    "automacao_gd.infrastructure.portal.cdp_service",
    "automacao_gd.infrastructure.excel.service",
}


def test_automation_bridge_does_not_import_low_level_automation_or_excel() -> None:
    source = Path("apps/desktop/bridge/automation_bridge.py").read_text(encoding="utf-8")

    assert "playwright" not in source
    assert "openpyxl" not in source
    assert "automacao_gd.infrastructure.portal.cdp_service" not in source
    assert "automacao_gd.infrastructure.excel.service" not in source


def test_apps_desktop_does_not_import_forbidden_infrastructure_directly() -> None:
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
                    for forbidden in FORBIDDEN_DESKTOP_IMPORTS
                ), f"{path} importa dependência proibida: {imported}"


def test_desktop_tests_do_not_reference_real_portal_or_customer_drive() -> None:
    forbidden_portal = "gdneoenergia" + "pernambuco.neoenergia.com"
    forbidden_drive = "Z:" + "\\Clientes"
    for path in [
        Path("tests/test_desktop_app_structure.py"),
        Path("tests/test_desktop_bridge.py"),
        Path("tests/test_desktop_architecture.py"),
    ]:
        source = path.read_text(encoding="utf-8")
        assert forbidden_portal not in source
        assert forbidden_drive not in source
