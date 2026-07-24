from __future__ import annotations

from pathlib import Path


def test_visual_asset_directories_exist() -> None:
    for path in [
        Path("apps/desktop/frontend/assets/images/.gitkeep"),
        Path("apps/desktop/frontend/assets/icons/.gitkeep"),
        Path("apps/desktop/frontend/static/assets/icons"),
        Path("apps/desktop/frontend/src/assets/icons"),
        Path("apps/desktop/frontend/static/assets/images"),
        Path("apps/desktop/frontend/src/assets/images"),
    ]:
        assert path.exists()


def test_premium_svg_icon_library_exists_for_static_and_react() -> None:
    required_icons = [
        "dashboard.svg",
        "portal.svg",
        "pipeline.svg",
        "protocolos.svg",
        "relatorios.svg",
        "configuracoes.svg",
        "status-system.svg",
        "cdp-link.svg",
        "simulation-mode.svg",
        "batch-layers.svg",
        "process-portal-solar.svg",
        "process-download-device.svg",
        "process-pdf.svg",
        "process-spreadsheet.svg",
        "process-folder.svg",
        "report-file.svg",
        "logs-folder.svg",
        "spreadsheet-file.svg",
    ]

    for root in [
        Path("apps/desktop/frontend/static/assets/icons"),
        Path("apps/desktop/frontend/src/assets/icons"),
    ]:
        for icon in required_icons:
            path = root / icon
            assert path.exists(), str(path)
            source = path.read_text(encoding="utf-8")
            assert "<svg" in source
            assert "xmlns=\"http://www.w3.org/2000/svg\"" in source


def test_static_frontend_references_local_svg_assets_without_cdn() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    assert "./assets/icons/dashboard.svg" in source
    assert "./assets/images/process-flow-premium.webp" in source
    assert "./assets/images/process-flow-premium.png" in source
    assert "./assets/images/process-flow-premium.svg" in source
    assert "./assets/icons/report-file.svg" in source
    assert "https://" not in source
    assert "http://" not in source


def test_premium_process_flow_asset_exists_for_static_and_react() -> None:
    expected_assets = [
        "process-flow-premium.webp",
        "process-flow-premium.png",
        "process-flow-premium.svg",
    ]

    for root in [
        Path("apps/desktop/frontend/static/assets/images"),
        Path("apps/desktop/frontend/src/assets/images"),
    ]:
        for asset in expected_assets:
            path = root / asset
            assert path.exists(), str(path)

    for path in [
        Path("apps/desktop/frontend/static/assets/images/process-flow-premium.svg"),
        Path("apps/desktop/frontend/src/assets/images/process-flow-premium.svg"),
    ]:
        source = path.read_text(encoding="utf-8")
        assert "<svg" in source


def test_react_process_visualization_uses_premium_asset() -> None:
    source = Path("apps/desktop/frontend/src/components/ProcessVisualization.tsx").read_text(encoding="utf-8")

    assert "process-flow-premium.webp" in source
    assert "process-flow-premium.png" in source
    assert "process-flow-premium.svg" in source
    assert "process-asset" in source
    assert "process-premium-image" in source


def test_process_asset_fallback_order_and_dynamic_status_are_preserved() -> None:
    static_source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")
    static_script = Path("apps/desktop/frontend/static/app.js").read_text(encoding="utf-8")
    react_source = Path(
        "apps/desktop/frontend/src/components/ProcessVisualization.tsx"
    ).read_text(encoding="utf-8")

    webp_position = static_source.index("process-flow-premium.webp")
    png_position = static_source.index("process-flow-premium.png")
    svg_position = static_source.index("process-flow-premium.svg")
    picture_end = static_source.index("</picture>", webp_position)
    status_position = static_source.index('class="activity-status"', picture_end)

    assert webp_position < png_position < svg_position < picture_end < status_position
    assert 'class="process-status-message"' in static_source
    assert 'class="progress-pill process-status-percent"' in static_source
    assert 'data-field="overall-progress">0%</span>' in static_source
    assert 'text(\'[data-field="overall-progress"]\'' in static_script
    assert "Math.max(0, Math.min(100, percent))" in static_script

    react_picture_end = react_source.index("</picture>")
    react_status_position = react_source.index('className="activity-status"')
    assert react_picture_end < react_status_position
    assert 'className="process-status-message"' in react_source
    assert 'className="progress-pill process-status-percent"' in react_source
    assert "{percent}%" in react_source


def test_static_frontend_does_not_use_text_glyphs_as_primary_icons() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    for old_icon in ["🔗", "▶", "↻", "⌕"]:
        assert old_icon not in source


def test_package_json_declares_visual_stack() -> None:
    source = Path("apps/desktop/frontend/package.json").read_text(encoding="utf-8")

    for dependency in ["react", "react-dom", "vite", "typescript", "lucide-react"]:
        assert dependency in source


def test_frontend_index_loads_qwebchannel_and_react_entrypoint() -> None:
    source = Path("apps/desktop/frontend/index.html").read_text(encoding="utf-8")

    assert "qrc:///qtwebchannel/qwebchannel.js" in source
    assert "./src/main.tsx" in source
