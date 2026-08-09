from __future__ import annotations

import re
from pathlib import Path


LAYOUT_CSS = Path("apps/desktop/frontend/src/styles/layout.css")
STATIC_INDEX = Path("apps/desktop/frontend/static/index.html")
STATIC_JS = Path("apps/desktop/frontend/static/app.js")
WINDOW_PY = Path("apps/desktop/window.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _css_block(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]+)\}}", source)
    assert match, f"CSS block not found: {selector}"
    return match.group("body")


def test_main_content_allows_vertical_scroll_at_zoom_100() -> None:
    source = _read(LAYOUT_CSS)
    block = _css_block(source, ".main-content")

    assert "overflow-y: auto" in block
    assert "overflow-x: hidden" in block
    assert "overflow: hidden" not in block


def test_responsive_media_queries_exist_for_common_small_desktop_sizes() -> None:
    source = _read(LAYOUT_CSS)

    assert "@media (max-height: 820px)" in source
    assert "@media (max-width: 1400px)" in source


def test_process_asset_has_compact_height_for_low_viewports() -> None:
    source = _read(LAYOUT_CSS)
    compact_media = source[source.index("@media (max-height: 820px)") :]

    assert ".process-asset" in compact_media
    assert "height: 170px" in compact_media or "height: 180px" in compact_media
    assert "object-fit: contain" in compact_media or "object-fit: cover" in compact_media


def test_ultra_compact_mode_exists_for_1366_by_768_zoom_100() -> None:
    source = _read(LAYOUT_CSS)
    compact_media = source[source.index("@media (max-height: 780px)") :]

    assert ".process-asset" in compact_media
    assert "height: 145px" in compact_media
    assert ".status-card" in compact_media
    assert "min-height: 64px" in compact_media
    assert ".activity-status" in compact_media
    assert "min-height: 30px" in compact_media


def test_reports_grid_stays_single_row_on_1366_width() -> None:
    source = _read(LAYOUT_CSS)
    compact_media = source[source.index("@media (max-width: 1400px)") :]

    assert ".reports-grid" in compact_media
    assert "minmax(210px" in compact_media
    assert "repeat(2" not in compact_media.split("@media (max-width: 1220px)")[0]


def test_protocol_table_does_not_force_inner_scroll_for_four_rows() -> None:
    source = _read(LAYOUT_CSS)
    final_table_block = list(re.finditer(r"\.table-wrap\s*\{(?P<body>[^}]+)\}", source))[-1].group("body")

    assert "overflow: visible" in final_table_block
    assert "max-height: none" in final_table_block


def test_window_minimum_size_is_compatible_with_1366_by_768_displays() -> None:
    source = _read(WINDOW_PY)

    assert "setMinimumSize(1180, 700)" in source
    assert "setMinimumSize(1280, 780)" not in source


def test_static_frontend_keeps_required_actions_accessible() -> None:
    source = _read(STATIC_INDEX)

    for label in [
        "Abrir Edge CDP",
        "Testar conexao CDP",
        "Testar conexão CDP",
        "Rodar simulacao",
        "Rodar simulação",
        "Rodar producao",
        "Rodar produção",
        "Abrir pipeline_cdp_completo.md",
        "Abrir processamento_pdfs_planilha_clientes.md",
        "Abrir pasta de logs",
        "Abrir planilha",
    ]:
        if "conexao" in label or "conexão" in label:
            assert "Testar conex" in source
        elif "simulacao" in label or "simulação" in label:
            assert "Rodar simula" in source
        elif "producao" in label or "produção" in label:
            assert "Rodar produ" in source
        else:
            assert label in source


def test_static_frontend_still_blocks_production_without_exact_confirmation() -> None:
    source = _read(STATIC_JS)

    assert "get_production_confirmation" in source
    assert "contract.confirmation" in source
    assert "requestProduction" in source
    assert "confirmacao" in source or "confirmation" in source


def test_static_frontend_remains_independent_from_vite_entrypoint() -> None:
    source = _read(STATIC_INDEX)

    assert "src/main.tsx" not in source
    assert "/src/main.tsx" not in source
    assert 'type="module"' not in source
