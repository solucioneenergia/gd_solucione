from __future__ import annotations

from pathlib import Path

from apps.desktop import window


def test_window_does_not_use_vite_index_as_local_fallback(tmp_path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        '<script type="module" src="./src/main.tsx"></script>',
        encoding="utf-8",
    )
    monkeypatch.setattr(window, "FRONTEND_ROOT", frontend)

    assert window._frontend_entrypoint() is None


def test_window_prefers_dist_index_when_build_exists(tmp_path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    dist = frontend / "dist"
    static = frontend / "static"
    dist.mkdir(parents=True)
    static.mkdir()
    dist_index = dist / "index.html"
    static_index = static / "index.html"
    dist_index.write_text("<html>dist</html>", encoding="utf-8")
    static_index.write_text("<html>static</html>", encoding="utf-8")
    monkeypatch.setattr(window, "FRONTEND_ROOT", frontend)

    assert window._frontend_entrypoint() == dist_index


def test_window_uses_static_index_when_dist_does_not_exist(tmp_path, monkeypatch) -> None:
    frontend = tmp_path / "frontend"
    static = frontend / "static"
    static.mkdir(parents=True)
    static_index = static / "index.html"
    static_index.write_text("<html>static</html>", encoding="utf-8")
    monkeypatch.setattr(window, "FRONTEND_ROOT", frontend)

    assert window._frontend_entrypoint() == static_index


def test_static_frontend_is_safe_without_vite() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    assert "/src/main.tsx" not in source
    assert "src/main.tsx" not in source
    assert 'type="module"' not in source
    assert "./app.js" in source


def test_static_dashboard_does_not_show_fallback_banner() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    assert "static-mode-banner" not in source
    assert "Modo visual estático — execute npm run build para ativar React." not in source


def test_static_frontend_contains_visual_sections() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    for label in [
        "Automação GD Neoenergia",
        "Dashboard",
        "Portal e Execução",
        "Visualização do Processo",
        "Protocolos recentes",
        "Relatórios",
    ]:
        assert label in source


def test_static_frontend_contains_four_mock_protocol_rows() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    for protocol in ["2606184625", "2606174347", "2606123663", "2606021741"]:
        assert protocol in source


def test_static_frontend_contains_process_labels() -> None:
    source = Path("apps/desktop/frontend/static/index.html").read_text(encoding="utf-8")

    for label in ["Portal", "Download", "PDF", "Planilha", "Arquivo"]:
        assert label in source


def test_vite_config_supports_file_protocol_build_assets() -> None:
    source = Path("apps/desktop/frontend/vite.config.ts").read_text(encoding="utf-8")

    assert 'base: "./"' in source


def test_static_styles_contain_design_tokens() -> None:
    source = Path("apps/desktop/frontend/static/styles.css").read_text(encoding="utf-8")

    for token in [
        "--color-orange",
        "--color-blue",
        "--color-green",
        "--color-purple",
        "--radius-card",
        "--shadow-card",
    ]:
        assert token in source


def test_static_styles_keep_protocol_table_without_mandatory_scroll() -> None:
    source = Path("apps/desktop/frontend/static/styles.css").read_text(encoding="utf-8")

    assert ".table-wrap" in source
    assert "overflow-y: visible" in source


def test_static_styles_keep_report_buttons_on_one_line() -> None:
    source = Path("apps/desktop/frontend/static/styles.css").read_text(encoding="utf-8")

    assert ".report-button" in source
    assert ".report-label" in source
    assert "white-space: nowrap" in source


def test_static_styles_define_process_flow_glow() -> None:
    source = Path("apps/desktop/frontend/static/styles.css").read_text(encoding="utf-8")

    assert "process-flow" in source
    assert "process-glow" in source


def test_static_production_confirmation_is_preserved() -> None:
    source = Path("apps/desktop/frontend/static/app.js").read_text(encoding="utf-8")

    assert "SIM, EXECUTAR PRODUÇÃO" in source
