from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_branding_defines_solucione_nordeste_identity() -> None:
    branding = importlib.import_module("apps.desktop.branding")

    assert branding.APP_NAME == "Solucione Nordeste"
    assert branding.EXECUTABLE_NAME == "Solucione Nordeste"
    assert branding.APP_WINDOW_TITLE == "Solucione Nordeste"
    assert branding.APP_ICON_PATH.name == "app_icon.ico"
    assert branding.APP_ICON_PATH.is_file()


def test_desktop_icon_assets_are_versioned_and_valid_for_windows() -> None:
    svg_path = ROOT / "apps" / "desktop" / "resources" / "app_icon.svg"
    ico_path = ROOT / "apps" / "desktop" / "resources" / "app_icon.ico"

    assert svg_path.is_file()
    assert ico_path.is_file()
    assert ico_path.read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert ico_path.stat().st_size > 1024


def test_desktop_window_uses_branding_name_and_icon() -> None:
    source = (ROOT / "apps" / "desktop" / "window.py").read_text(encoding="utf-8")

    assert "APP_WINDOW_TITLE" in source
    assert "APP_ICON_PATH" in source
    assert "setWindowTitle(APP_WINDOW_TITLE)" in source
    assert "setWindowIcon" in source


def test_windows_build_uses_solucione_name_and_validated_icon() -> None:
    source = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert '$AppName = "Solucione Nordeste"' in source
    assert "app_icon.ico" in source
    assert "--icon" in source
    assert '--name "$AppName"' in source
    assert "apps/desktop/resources" in source
    assert "DESKTOP_ICON_INVALID" in source
    assert "Assert-DesktopIconFile" in source
    assert "$Header[2] -ne 1" in source


def test_python_package_includes_desktop_icon_resources() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]["apps.desktop"]

    assert "resources/*.ico" in package_data
    assert "resources/*.svg" in package_data


def test_local_install_scripts_create_shortcut_without_dev_or_operational_steps() -> None:
    shortcut = ROOT / "scripts" / "create_desktop_shortcut.ps1"
    installer = ROOT / "scripts" / "install_local_desktop.ps1"

    assert shortcut.is_file()
    assert installer.is_file()

    combined = "\n".join(
        [
            shortcut.read_text(encoding="utf-8"),
            installer.read_text(encoding="utf-8"),
        ]
    )
    assert "Solucione Nordeste.lnk" in combined
    assert "apps\\desktop\\resources\\app_icon.ico" in combined
    assert "WScript.Shell" in combined
    assert "IconLocation" in combined
    assert "WorkingDirectory" in combined
    assert "Assert-DesktopIconFile" in combined

    forbidden = re.compile(r"\b(pytest|pnpm|playwright|storage_state|CDP_ENDPOINT)\b", re.I)
    assert forbidden.search(combined) is None
