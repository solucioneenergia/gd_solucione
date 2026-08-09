from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from automacao_gd.domain.errors import OperationalBlockError


def test_migration_apply_requires_typed_authorization_before_copy(
    tmp_path: Path,
) -> None:
    from scripts.migrate_v1_operational_data import migrate_operational_data

    source = tmp_path / "origem_sintetica"
    destination = tmp_path / "destino_sintetico"
    source.mkdir()
    destination.mkdir()
    state = source / "data/state/pipeline_cdp_state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")

    with pytest.raises(OperationalBlockError) as exc:
        migrate_operational_data(source, destination, apply=True)

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"
    assert not (destination / "data/state/pipeline_cdp_state.json").exists()


def test_imported_cdp_download_requires_typed_authorization_before_portal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import download_completed_budgets_cdp as script

    settings = SimpleNamespace(
        MAX_COMPLETED_TO_PROCESS=1,
        downloads_dir_path=tmp_path / "downloads_sinteticos",
    )
    monkeypatch.setattr(script, "get_settings", lambda: settings)
    monkeypatch.setattr(
        script,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("Portal iniciado")),
    )

    with pytest.raises(OperationalBlockError) as exc:
        script.run_download_completed_budgets_cdp()

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"


def test_imported_first_solicitation_requires_typed_authorization_before_portal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import process_first_solicitation_cdp as script

    monkeypatch.setattr(script, "ensure_directories", lambda: None)
    monkeypatch.setattr(script, "setup_logger", lambda: None)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        script,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("Portal iniciado")),
    )

    with pytest.raises(OperationalBlockError) as exc:
        script._legacy_process_first_solicitation_cdp()

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"


@pytest.mark.parametrize(
    "module_name",
    ["connect_existing_edge", "inspect_portal_table"],
)
def test_diagnostic_main_is_blocked_before_external_access(
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__(f"scripts.{module_name}", fromlist=["main"])
    monkeypatch.setattr(module, "ensure_directories", lambda: None)
    monkeypatch.setattr(module, "setup_logger", lambda: None)
    monkeypatch.setattr(
        module,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("acesso externo iniciado")),
    )

    assert module.main() == 2


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("connect_existing_edge", "_legacy_connect_existing_edge"),
        ("inspect_portal_table", "_legacy_inspect_portal_table"),
    ],
)
def test_imported_legacy_diagnostic_requires_typed_authorization(
    module_name: str,
    function_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = __import__(f"scripts.{module_name}", fromlist=[function_name])
    monkeypatch.setattr(module, "ensure_directories", lambda: None)
    monkeypatch.setattr(module, "setup_logger", lambda: None)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        module,
        "create_portal_automation",
        lambda *_: (_ for _ in ()).throw(AssertionError("acesso externo iniciado")),
    )

    with pytest.raises(OperationalBlockError) as exc:
        getattr(module, function_name)()

    assert exc.value.code == "DIRECT_ROUTE_AUTHORIZATION_REQUIRED"
