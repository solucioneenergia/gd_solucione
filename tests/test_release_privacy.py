from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.create_clean_release_zip import create_clean_release_zip
from scripts import privacy_scan
from scripts.privacy_scan import scan_archive, scan_paths


PROJECT_ROOT = Path(__file__).parents[1]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_privacy_scanner_blocks_content_without_echoing_detected_value(
    tmp_path: Path,
) -> None:
    personal_path = "C:" + "\\Users\\Operador\\documento.xlsx"
    credential = "sk-" + "live-sensitive-value"
    identifier = "260" + "9876543"
    source = tmp_path / "module.py"
    source.write_text(
        f'path = r"{personal_path}"\ntoken = "{credential}"\nprotocol = "{identifier}"\n',
        encoding="utf-8",
    )

    report = scan_paths(tmp_path, [Path("module.py")])

    assert report["valid"] is False
    assert {finding["rule"] for finding in report["findings"]} >= {
        "personal_absolute_path",
        "credential_value",
        "operational_identifier",
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert personal_path not in serialized
    assert credential not in serialized
    assert identifier not in serialized


def test_privacy_scanner_blocks_python_literal_with_escaped_personal_path(
    tmp_path: Path,
) -> None:
    separator = "\\" * 2
    personal_path = f"C:{separator}Users{separator}Operador{separator}perfil"
    source = tmp_path / "module.py"
    source.write_text(f'path = "{personal_path}"\n', encoding="utf-8")

    report = scan_paths(tmp_path, [source.relative_to(tmp_path)])

    assert report["valid"] is False
    assert "personal_absolute_path" in {
        finding["rule"] for finding in report["findings"]
    }
    assert personal_path not in json.dumps(report, ensure_ascii=False)


def test_privacy_scanner_blocks_utf16le_personal_path_after_nul(tmp_path: Path) -> None:
    personal_path = "C:" + "\\Users\\Operador\\perfil"
    payload = tmp_path / "bundle.bin"
    payload.write_bytes(personal_path.encode("utf-16-le"))

    report = scan_paths(tmp_path, [payload.relative_to(tmp_path)])

    assert report["valid"] is False
    assert "personal_absolute_path" in {
        finding["rule"] for finding in report["findings"]
    }
    assert personal_path not in json.dumps(report, ensure_ascii=False)


def test_privacy_scanner_hashes_each_detected_match_independently(tmp_path: Path) -> None:
    first = "sk-" + "live-first-sensitive"
    second = "sk-" + "live-second-sensitive"
    source = tmp_path / "module.txt"
    source.write_text(f"{first}\n{second}\n", encoding="utf-8")

    report = scan_paths(tmp_path, [source.relative_to(tmp_path)])

    hashes = [
        finding["match_hash"]
        for finding in report["findings"]
        if finding["rule"] == "credential_value"
    ]
    assert len(hashes) == 2
    assert len(set(hashes)) == 2


def test_privacy_scanner_accepts_only_official_synthetic_markers(tmp_path: Path) -> None:
    fixture = tmp_path / "tests" / "fixtures" / "synthetic" / "synthetic_fixture.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        "\n".join(
            (
                "protocolo=2600000000",
                "protocolo=2600000001",
                "protocolo=2600000002",
                "cliente=CLIENTE SINTETICO LTDA",
                "uc=0000000000",
                r"path=C:\CAMINHO\SINTETICO",
            )
        ),
        encoding="utf-8",
    )

    report = scan_paths(tmp_path, [fixture.relative_to(tmp_path)])

    assert report["valid"] is True
    assert report["findings"] == []


def test_test_path_does_not_authorize_undeclared_identifier() -> None:
    identifier = "260" + "0009999"

    report = privacy_scan.scan_entries(
        {"tests/test_portal.py": f'protocol = "{identifier}"'.encode("utf-8")}
    )

    assert report["valid"] is False
    assert identifier not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize(
    "path",
    (
        "apps/desktop/frontend/dist/assets/index.js",
        "automacao_gd/runtime.py",
        "release-manifest.json",
    ),
)
def test_official_fixture_identifier_is_blocked_outside_fixture_context(
    path: str,
) -> None:
    report = privacy_scan.scan_entries(
        {path: b'protocol = "2600001048"'}
    )

    assert report["valid"] is False


def test_privacy_scanner_blocks_embedded_client_name(tmp_path: Path) -> None:
    private_value = "PESSOA" + " OPERACIONAL"
    field_name = "client_" + "name"
    source = tmp_path / "module.py"
    source.write_text(f'{field_name} = "{private_value}"\n', encoding="utf-8")

    report = scan_paths(tmp_path, [source.relative_to(tmp_path)])

    assert report["valid"] is False
    assert "client_name" in {finding["rule"] for finding in report["findings"]}
    assert private_value not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("suffix", [".zip", ".whl"])
def test_privacy_scanner_inspects_compressed_artifact_content(
    tmp_path: Path, suffix: str
) -> None:
    identifier = "260" + "9876543"
    artifact = tmp_path / f"artifact{suffix}"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/module.py", f'protocol = "{identifier}"')

    report = scan_archive(artifact)

    assert report["valid"] is False
    assert report["findings"][0]["path"] == "package/module.py"
    assert identifier not in json.dumps(report)


def test_tree_privacy_scan_opens_compressed_artifacts(tmp_path: Path) -> None:
    identifier = "260" + "9876543"
    artifact = tmp_path / "package.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("package/module.py", f'protocol = "{identifier}"')

    report = scan_paths(tmp_path, [artifact.relative_to(tmp_path)])

    assert report["valid"] is False
    assert report["findings"][0]["path"] == "package.whl!package/module.py"


def test_tree_archive_scan_preserves_inner_synthetic_test_context(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "package.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr(
            "tests/test_synthetic_fixture.py",
            'protocol = "2600001048"',
        )

    report = scan_paths(tmp_path, [artifact.relative_to(tmp_path)])

    assert report["valid"] is True


def test_privacy_scanner_allows_digest_digits_but_not_manifest_protocol() -> None:
    undeclared_identifier = "260" + "0001234"
    digest = "a" * 20 + undeclared_identifier + "b" * 34
    clean = privacy_scan.scan_entries(
        {
            "release-manifest.json": json.dumps(
                {"files": {"module.py": digest}}
            ).encode("utf-8")
        }
    )
    contaminated = privacy_scan.scan_entries(
        {
            "release-manifest.json": json.dumps(
                {
                    "files": {"module.py": digest},
                    "protocol": undeclared_identifier,
                }
            ).encode("utf-8")
        }
    )

    assert clean["valid"] is True
    assert contaminated["valid"] is False


def test_privacy_scanner_does_not_skip_binary_payload_after_nul(tmp_path: Path) -> None:
    identifier = "260" + "9876543"
    bundle = tmp_path / "bundle.bin"
    bundle.write_bytes(b"\x00\x01compiled:" + identifier.encode("ascii"))

    report = scan_paths(tmp_path, [bundle.relative_to(tmp_path)])

    assert report["valid"] is False
    assert "operational_identifier" in {
        finding["rule"] for finding in report["findings"]
    }


def test_privacy_scanner_blocks_unlabeled_protocol_in_markup(tmp_path: Path) -> None:
    identifier = "259" + "8765432"
    markup = tmp_path / "index.html"
    markup.write_text(f"<td>{identifier}</td>", encoding="utf-8")

    report = scan_paths(tmp_path, [markup.relative_to(tmp_path)])

    assert report["valid"] is False
    assert "operational_identifier" in {
        finding["rule"] for finding in report["findings"]
    }
    assert identifier not in json.dumps(report)


@pytest.mark.parametrize("ignored_directory", ["node_modules", "data", "outputs"])
def test_tree_privacy_scan_skips_non_source_directories(
    tmp_path: Path, ignored_directory: str
) -> None:
    identifier = "260" + "9876543"
    (tmp_path / "source.py").write_text("synthetic", encoding="utf-8")
    dependency = tmp_path / ignored_directory / "package" / "bundle.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text(identifier, encoding="utf-8")

    report = scan_paths(tmp_path, [Path(".")])

    assert report["valid"] is True
    assert report["scanned_files"] == 1


def test_tree_scan_inspects_nested_frontend_source_data_directory(
    tmp_path: Path,
) -> None:
    identifier = "259" + "8765432"
    fixture = (
        tmp_path
        / "apps/desktop/frontend/src/data"
        / "demoDashboard.fixture.ts"
    )
    fixture.parent.mkdir(parents=True)
    fixture.write_text(identifier, encoding="utf-8")

    report = scan_paths(tmp_path, [Path(".")])

    assert report["valid"] is False
    assert report["findings"][0]["path"] == (
        "apps/desktop/frontend/src/data/demoDashboard.fixture.ts"
    )


def test_tree_scan_skips_stale_root_dist_but_scans_frontend_dist(
    tmp_path: Path,
) -> None:
    identifier = "259" + "8765432"
    stale = tmp_path / "dist" / "bundle.js"
    canonical = tmp_path / "apps/desktop/frontend/dist" / "bundle.js"
    stale.parent.mkdir(parents=True)
    canonical.parent.mkdir(parents=True)
    stale.write_text(identifier, encoding="utf-8")
    canonical.write_text(identifier, encoding="utf-8")

    report = scan_paths(tmp_path, [Path(".")])

    assert report["finding_count"] == 1
    assert report["findings"][0]["path"] == "apps/desktop/frontend/dist/bundle.js"


def test_tree_scan_blocks_environment_file_without_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = tmp_path / ".env.production"
    environment.write_text("SYNTHETIC_VALUE=true", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == environment:
            raise AssertionError("environment file must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    report = scan_paths(tmp_path, [Path(".")])

    assert report["valid"] is False
    assert "environment_file" in {
        finding["rule"] for finding in report["findings"]
    }


def test_canonical_frontend_index_contains_no_embedded_operational_data() -> None:
    report = scan_paths(
        PROJECT_ROOT,
        [Path("apps/desktop/frontend/index.html")],
    )

    assert report["valid"] is True


def test_cdp_helper_contains_no_personal_path_literal() -> None:
    report = scan_paths(PROJECT_ROOT, [Path("scripts/connect_existing_edge.py")])

    assert "personal_absolute_path" not in {
        finding["rule"] for finding in report["findings"]
    }


def test_privacy_scanner_source_does_not_trigger_its_own_rules() -> None:
    report = scan_paths(PROJECT_ROOT, [Path("scripts/privacy_scan.py")])

    assert report["valid"] is True


def test_release_generator_rejects_non_git_directory(tmp_path: Path) -> None:
    project = tmp_path / "not-a-repository"
    project.mkdir()

    with pytest.raises(RuntimeError, match="RELEASE_PROVENANCE_INVALID"):
        create_clean_release_zip(project, tmp_path / "release.zip")


def test_release_generator_rejects_dirty_worktree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("synthetic", encoding="utf-8")
    _git(project, "init")
    _git(project, "add", "README.md")
    _git(
        project,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )
    (project / "README.md").write_text("dirty", encoding="utf-8")

    with pytest.raises(RuntimeError, match="DIRTY_WORKTREE"):
        create_clean_release_zip(project, tmp_path / "release.zip")


def test_release_generator_rechecks_cleanliness_after_privacy_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("synthetic", encoding="utf-8")
    _git(project, "init")
    _git(project, "add", "README.md")
    _git(
        project,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )

    def dirty_scan(root: Path, paths: object) -> dict[str, object]:
        readme.write_text("changed during release", encoding="utf-8")
        return {
            "valid": True,
            "scanned_files": 1,
            "finding_count": 0,
            "findings": [],
        }

    monkeypatch.setattr(privacy_scan, "scan_paths", dirty_scan)

    with pytest.raises(RuntimeError, match="DIRTY_WORKTREE"):
        create_clean_release_zip(project, tmp_path / "release.zip")


def test_ci_keeps_build_artifacts_outside_workspace_and_scans_all_packages() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert '--wheel-dir "${RUNNER_TEMP}/wheelhouse"' in workflow
    assert 'python -m venv "${RUNNER_TEMP}/smoke-venv"' in workflow
    assert "python scripts/privacy_scan.py . ." in workflow
    assert 'python scripts/privacy_scan.py "${RUNNER_TEMP}/wheelhouse" .' in workflow
    assert '--expected-head-sha "${EXPECTED_HEAD_SHA}"' in workflow
    assert "--wheel-dir wheelhouse" not in workflow
    assert "python -m venv smoke-venv" not in workflow
