from __future__ import annotations

import importlib
from pathlib import Path


FRONTEND = Path("apps/desktop/frontend")


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_static_sidebar_contains_all_items() -> None:
    source = _read("static/index.html")

    for label in ["Dashboard", "Portal", "Pipeline", "Protocolos", "Relatórios", "Configurações"]:
        assert label in source


def test_dashboard_sidebar_item_is_active() -> None:
    source = _read("static/index.html")

    assert 'class="nav-item active"' in source
    assert "Dashboard" in source


def test_sidebar_active_item_has_orange_vertical_bar_rule() -> None:
    css = _read("src/styles/layout.css")

    assert ".nav-item.active::before" in css
    assert "background: var(--color-orange)" in css
    assert "width: 3px" in css


def test_sidebar_icons_have_controlled_svg_size() -> None:
    css = _read("src/styles/layout.css")

    assert ".sidebar-icon" in css
    assert "width: 22px" in css
    assert "height: 22px" in css
    assert "stroke: currentColor" in css


def test_sidebar_icons_are_real_local_assets_not_empty_placeholders() -> None:
    static_source = _read("static/index.html")
    react_source = _read("src/components/Sidebar.tsx")

    for icon in [
        "dashboard.svg",
        "portal.svg",
        "pipeline.svg",
        "protocolos.svg",
        "relatorios.svg",
        "configuracoes.svg",
    ]:
        assert f"./assets/icons/{icon}" in static_source
        assert icon in react_source
    assert "sidebar-icon" in react_source


def test_python_card_contains_required_status_texts() -> None:
    source = _read("static/index.html") + _read("src/components/Sidebar.tsx")

    assert "Python 3.12+" in source
    assert "Ambiente não verificado" in source
    assert "status-dot" in source


def test_static_fallback_does_not_reference_vite_tsx_entrypoint() -> None:
    source = _read("static/index.html")

    assert "src/main.tsx" not in source
    assert "/src/main.tsx" not in source
    assert 'type="module"' not in source


def test_app_entrypoints_remain_importable() -> None:
    importlib.import_module("app")
    importlib.import_module("desktop_app")
