from __future__ import annotations

from pathlib import Path


FRONTEND = Path("apps/desktop/frontend")


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_frontend_required_files_exist() -> None:
    for relative in [
        "package.json",
        "vite.config.ts",
        "index.html",
        "static/index.html",
        "static/styles.css",
        "static/app.js",
        "src/main.tsx",
        "src/App.tsx",
        "src/bridge/qtBridge.ts",
        "src/styles/theme.css",
        "src/styles/layout.css",
        "src/components/Sidebar.tsx",
        "src/components/StatusCards.tsx",
        "src/components/PortalExecutionPanel.tsx",
        "src/components/ProcessVisualization.tsx",
        "src/components/RecentProtocolsTable.tsx",
        "src/components/ReportsPanel.tsx",
        "src/components/TopBar.tsx",
        "src/components/Badge.tsx",
        "src/components/Button.tsx",
        "src/data/mockDashboard.ts",
    ]:
        assert (FRONTEND / relative).exists(), relative


def test_theme_css_contains_design_tokens() -> None:
    source = _read("src/styles/theme.css")

    for token in [
        "--color-orange",
        "--color-blue",
        "--color-green",
        "--color-purple",
        "--radius-card",
        "--shadow-card",
        "--shadow-glow-blue",
        "--radius-lg",
        "--cyan",
        "--surface",
        "--orange",
    ]:
        assert token in source


def test_layout_contains_incremental_responsive_refinement() -> None:
    source = _read("src/styles/layout.css")

    assert "@media (min-width: 1500px)" in source
    assert ".premium-flow" in source
    assert ".process-asset" in source
    assert ".process-premium-image" in source
    assert "object-fit: cover" in source
    assert "border-radius: 12px" in source
    assert "height: 180px" in source
    assert "height: 245px" in source
    assert "@media (max-width: 1499px)" in source
    assert ".process-status-message" in source
    assert ".process-status-percent" in source
    assert "flex: 0 0 auto" in source
    assert "display: block" in source
    assert "width: 100%" in source


def test_sidebar_contains_expected_tabs() -> None:
    source = _read("src/components/Sidebar.tsx") + _read("index.html")

    for label in ["Dashboard", "Portal", "Pipeline", "Protocolos", "Relatórios", "Configurações"]:
        assert label in source


def test_dashboard_contains_expected_status_cards() -> None:
    source = _read("src/components/StatusCards.tsx") + _read("index.html")

    for label in ["Status do sistema", "CDP", "Modo", "Lote"]:
        assert label in source


def test_portal_execution_panel_contains_expected_buttons() -> None:
    source = _read("src/components/PortalExecutionPanel.tsx") + _read("index.html")

    for label in [
        "Abrir Edge CDP",
        "Testar conexão CDP",
        "Inspecionar portal",
        "Rodar simulação",
        "Rodar produção",
    ]:
        assert label in source


def test_recent_protocols_table_contains_expected_columns() -> None:
    source = _read("src/components/RecentProtocolsTable.tsx") + _read("index.html")

    for label in ["Protocolo", "Cliente", "PDF", "Excel", "Arquivo", "Status"]:
        assert label in source


def test_reports_panel_contains_expected_actions() -> None:
    source = _read("src/components/ReportsPanel.tsx") + _read("index.html")

    for label in [
        "pipeline_cdp_completo.md",
        "processamento_pdfs_planilha_clientes.md",
        "pasta de logs",
        "planilha",
    ]:
        assert label.casefold() in source.casefold()


def test_production_requires_exact_confirmation_text() -> None:
    source = _read("src/data/mockDashboard.ts") + _read("src/components/PortalExecutionPanel.tsx")

    assert "SIM, EXECUTAR PRODUÇÃO" in source
    assert "requestProduction" in source
