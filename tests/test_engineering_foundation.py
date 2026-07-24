from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.validate_engineering_foundation import (
    REQUIRED_FILES,
    SKILL_NAMES,
    parse_frontmatter,
    parse_invocation_policy,
    validate_foundation,
)


ROOT = Path(__file__).resolve().parents[1]


def test_required_engineering_foundation_files_exist() -> None:
    assert ".github/workflows/ci.yml" in REQUIRED_FILES
    missing = [relative for relative in REQUIRED_FILES if not (ROOT / relative).is_file()]
    assert not missing, f"Arquivos obrigatórios ausentes: {missing}"


def test_four_skills_have_valid_unique_metadata() -> None:
    names: list[str] = []
    for expected_name in SKILL_NAMES:
        skill_file = ROOT / ".agents" / "skills" / expected_name / "SKILL.md"
        metadata, _ = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        assert metadata["name"] == expected_name
        assert metadata["description"].strip()
        names.append(metadata["name"])

    assert len(names) == 4
    assert len(names) == len(set(names))


def test_templates_expose_required_sections() -> None:
    spec = (ROOT / "docs/templates/SPEC_TEMPLATE.md").read_text(encoding="utf-8")
    adr = (ROOT / "docs/templates/ADR_TEMPLATE.md").read_text(encoding="utf-8")

    for heading in ("## Contexto", "## Critérios de aceite", "## Rollback"):
        assert heading in spec
    for heading in ("## Contexto", "## Decisão", "## Plano de rollback"):
        assert heading in adr


def test_indexes_spec_and_adr_exist() -> None:
    for relative in (
        "docs/engineering/README.md",
        "specs/README.md",
        "docs/adr/README.md",
        "specs/SPEC-000-engineering-governance-foundation.md",
        "docs/adr/0003-codex-engineering-governance.md",
    ):
        assert (ROOT / relative).is_file()


def test_validator_succeeds_for_repository() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_engineering_foundation.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Engineering foundation: VALID" in result.stdout


def test_validator_detects_invalid_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".agents/skills/wrong-folder"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: another-name\ndescription: \"\"\n---\n\n# Skill\n",
        encoding="utf-8",
    )

    errors = validate_foundation(tmp_path)

    assert any("não corresponde ao diretório" in error for error in errors)
    assert any("description vazia" in error for error in errors)


def test_validator_detects_broken_local_markdown_link(tmp_path: Path) -> None:
    engineering_dir = tmp_path / "docs/engineering"
    engineering_dir.mkdir(parents=True)
    (engineering_dir / "README.md").write_text(
        "[documento ausente](missing.md)\n", encoding="utf-8"
    )

    errors = validate_foundation(tmp_path)

    assert any("link local inexistente" in error for error in errors)


def test_validator_detects_broken_local_markdown_image(tmp_path: Path) -> None:
    engineering_dir = tmp_path / "docs/engineering"
    engineering_dir.mkdir(parents=True)
    (engineering_dir / "README.md").write_text("![ausente](missing.png)\n", encoding="utf-8")

    errors = validate_foundation(tmp_path)

    assert any("link local inexistente" in error for error in errors)


def test_validator_requires_ci_gate_before_tests(tmp_path: Path) -> None:
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "- name: Tests\n  run: pytest\n"
        "- name: Validate engineering foundation\n"
        "  run: python scripts/validate_engineering_foundation.py\n",
        encoding="utf-8",
    )

    errors = validate_foundation(tmp_path)

    assert any("CI deve validar a fundação antes dos testes" in error for error in errors)


def test_commented_invocation_policy_does_not_satisfy_contract(tmp_path: Path) -> None:
    config = tmp_path / ".agents/skills/senior-code-review/agents/openai.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "policy:\n  # allow_implicit_invocation: false\n"
        "  allow_implicit_invocation: true\n",
        encoding="utf-8",
    )

    errors = validate_foundation(tmp_path)

    assert any("política de invocação incoerente" in error for error in errors)


def test_validator_does_not_read_env_or_data(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/secret.txt").write_text("private", encoding="utf-8")
    original_read_text = Path.read_text
    original_iterdir = Path.iterdir

    def guarded_read_text(path: Path, *args, **kwargs):
        assert path.name != ".env"
        assert "data" not in path.parts
        return original_read_text(path, *args, **kwargs)

    def guarded_iterdir(path: Path):
        assert path.name != "data"
        return original_iterdir(path)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    validate_foundation(tmp_path)


def test_skill_invocation_policies_are_coherent() -> None:
    for skill_name in SKILL_NAMES:
        config = (
            ROOT / ".agents" / "skills" / skill_name / "agents/openai.yaml"
        ).read_text(encoding="utf-8")
        expected = skill_name != "senior-code-review"
        assert parse_invocation_policy(config) is expected
        assert f"${skill_name}" in config
