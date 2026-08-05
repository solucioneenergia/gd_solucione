"""Validate the repository's engineering-governance contracts using stdlib only."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


SKILL_NAMES = (
    "engineering-orchestrator",
    "spec-driven-development",
    "tdd-workflow",
    "senior-code-review",
)

AGENTS_FILES = (
    "AGENTS.md",
    "automacao_gd/AGENTS.md",
    "apps/desktop/AGENTS.md",
    "frontend/AGENTS.md",
    "tests/AGENTS.md",
)

DOCUMENT_FILES = (
    "docs/engineering/README.md",
    "docs/engineering/OPERATING_MODEL.md",
    "docs/engineering/DEFINITION_OF_DONE.md",
    "docs/templates/SPEC_TEMPLATE.md",
    "docs/templates/ADR_TEMPLATE.md",
    "docs/adr/README.md",
    "specs/README.md",
    "specs/SPEC-000-engineering-governance-foundation.md",
    "docs/adr/0003-codex-engineering-governance.md",
    "docs/adr/0005-canonical-desktop-path.md",
    "specs/desktop_production_readiness.md",
)

SKILL_FILES = tuple(
    relative
    for skill_name in SKILL_NAMES
    for relative in (
        f".agents/skills/{skill_name}/SKILL.md",
        f".agents/skills/{skill_name}/agents/openai.yaml",
    )
)

REQUIRED_FILES = AGENTS_FILES + DOCUMENT_FILES + SKILL_FILES + (
    ".github/workflows/ci.yml",
    "scripts/validate_engineering_foundation.py",
    "tests/test_engineering_foundation.py",
    "apps/__init__.py",
    "apps/desktop/frontend/package.json",
    "apps/desktop/frontend/pnpm-lock.yaml",
    "apps/desktop/frontend/pnpm-workspace.yaml",
    "tests/test_release_candidate_202.py",
    "tests/test_release_validator_hardening.py",
)

SPEC_TEMPLATE_SECTIONS = (
    "Contexto",
    "Problema",
    "Evidências do comportamento atual",
    "Objetivo",
    "Escopo",
    "Fora do escopo",
    "Glossário",
    "Requisitos funcionais",
    "Requisitos não funcionais",
    "Contratos e interfaces",
    "Dados e persistência",
    "Estados e tratamento de erros",
    "Segurança e privacidade",
    "UX e acessibilidade, quando aplicável",
    "Observabilidade",
    "Compatibilidade",
    "Migração ou backfill",
    "Estratégia de testes",
    "Critérios de aceite",
    "Rollout",
    "Rollback",
    "Riscos",
    "Decisões pendentes",
    "Evidências de homologação",
)

ADR_TEMPLATE_SECTIONS = (
    "Contexto",
    "Problema arquitetural",
    "Forças de decisão",
    "Decisão",
    "Alternativas consideradas",
    "Consequências positivas",
    "Consequências negativas",
    "Riscos",
    "Segurança",
    "Compatibilidade",
    "Plano de implementação",
    "Plano de rollback",
    "Validação da decisão",
    "Evidências",
    "Referências",
)

MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
IGNORED_LINK_PREFIXES = ("http://", "https://", "mailto:", "#")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the intentionally simple top-level YAML used by SKILL.md."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter deve iniciar com ---")
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("frontmatter sem delimitador final ---") from exc

    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"linha de frontmatter inválida: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError("chave vazia no frontmatter")
        metadata[key] = value.strip().strip('"\'')
    return metadata, "\n".join(lines[closing + 1 :])


def _read(path: Path, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"não foi possível ler {path}: {exc}")
        return None


def _check_required_files(root: Path, errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"arquivo obrigatório ausente: {relative}")


def _skill_directories(root: Path) -> list[Path]:
    skills_root = root / ".agents" / "skills"
    if not skills_root.is_dir():
        return []
    try:
        return sorted((path for path in skills_root.iterdir() if path.is_dir()), key=lambda p: p.name)
    except OSError:
        return []


def _check_skills(root: Path, errors: list[str]) -> None:
    names: list[str] = []
    for skill_dir in _skill_directories(root):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            errors.append(f"Skill sem SKILL.md: {skill_dir.name}")
            continue
        content = _read(skill_file, errors)
        if content is None:
            continue
        try:
            metadata, _ = parse_frontmatter(content)
        except ValueError as exc:
            errors.append(f"frontmatter inválido em {skill_file}: {exc}")
            continue

        name = metadata.get("name", "").strip()
        description = metadata.get("description", "").strip()
        if not name:
            errors.append(f"name ausente em {skill_file}")
        else:
            names.append(name)
            if name != skill_dir.name:
                errors.append(
                    f"name '{name}' não corresponde ao diretório '{skill_dir.name}'"
                )
        if not description:
            errors.append(f"description vazia em {skill_file}")

    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append(f"name de Skill duplicado: {name}")


def _check_template(path: Path, sections: tuple[str, ...], errors: list[str]) -> None:
    if not path.is_file():
        return
    content = _read(path, errors)
    if content is None:
        return
    for section in sections:
        if f"## {section}" not in content:
            errors.append(f"seção ausente em {path}: {section}")


def _check_agents(root: Path, errors: list[str]) -> None:
    for relative in AGENTS_FILES:
        path = root / relative
        if not path.is_file():
            continue
        content = _read(path, errors)
        if content is not None and not content.strip():
            errors.append(f"AGENTS.md vazio: {relative}")


def _check_governance_records(root: Path, errors: list[str]) -> None:
    checks = {
        "specs/SPEC-000-engineering-governance-foundation.md": (
            "Status: aceita",
            "## Critérios de aceite",
            "## Rollback",
        ),
        "docs/adr/0003-codex-engineering-governance.md": (
            "Status: aceita",
            "## Decisão",
            "## Plano de rollback",
        ),
        "docs/adr/0005-canonical-desktop-path.md": (
            "Status: aceita",
            "## Decisão",
            "## Plano de rollback",
        ),
        "specs/desktop_production_readiness.md": (
            "em homologação final controlada da candidata 2.0.2",
            "apps/desktop",
            "## 5. Critérios de aceite",
        ),
    }
    for relative, tokens in checks.items():
        path = root / relative
        if not path.is_file():
            continue
        content = _read(path, errors)
        if content is None:
            continue
        for token in tokens:
            if token not in content:
                errors.append(f"contrato ausente em {relative}: {token}")


def _link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0].strip('"\'')
    return unquote(target.split("#", 1)[0])


def _check_local_links(root: Path, errors: list[str]) -> None:
    markdown_files = AGENTS_FILES + DOCUMENT_FILES + tuple(
        f".agents/skills/{name}/SKILL.md" for name in SKILL_NAMES
    )
    for relative in markdown_files:
        path = root / relative
        if not path.is_file():
            continue
        content = _read(path, errors)
        if content is None:
            continue
        for match in MARKDOWN_LINK.finditer(content):
            raw_target = match.group(1).strip()
            if raw_target.lower().startswith(IGNORED_LINK_PREFIXES):
                continue
            target = _link_target(raw_target)
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"link local fora do projeto em {relative}: {raw_target}")
                continue
            if not resolved.exists():
                errors.append(f"link local inexistente em {relative}: {raw_target}")


def parse_invocation_policy(content: str) -> bool | None:
    """Return one active boolean policy value, rejecting comments and duplicates."""
    in_policy = False
    policy_indent = 0
    values: list[bool] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if not in_policy:
            if indent == 0 and stripped == "policy:":
                in_policy = True
                policy_indent = indent
            continue
        if indent <= policy_indent:
            break
        match = re.fullmatch(
            r"allow_implicit_invocation:\s*(true|false)\s*(?:#.*)?", stripped
        )
        if match:
            values.append(match.group(1) == "true")
    return values[0] if len(values) == 1 else None


def _check_invocation_policies(root: Path, errors: list[str]) -> None:
    for skill_name in SKILL_NAMES:
        path = root / ".agents" / "skills" / skill_name / "agents" / "openai.yaml"
        if not path.is_file():
            continue
        content = _read(path, errors)
        if content is None:
            continue
        expected = skill_name != "senior-code-review"
        if parse_invocation_policy(content) is not expected:
            errors.append(f"política de invocação incoerente em {path}")
        if f"${skill_name}" not in content:
            errors.append(f"default_prompt não menciona ${skill_name} em {path}")


def _check_ci(root: Path, errors: list[str]) -> None:
    path = root / ".github/workflows/ci.yml"
    if not path.is_file():
        return
    content = _read(path, errors)
    if content is None:
        return
    active = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    validate_step = active.find("- name: Validate engineering foundation")
    validate_run = active.find("run: python scripts/validate_engineering_foundation.py")
    tests_step = active.find("- name: Tests")
    if not (0 <= validate_step < validate_run < tests_step):
        errors.append("CI deve validar a fundação antes dos testes")
    required_tokens = (
        "python -m mypy automacao_gd",
        "pnpm install --frozen-lockfile",
        "pnpm test",
        "pnpm build",
        "python -m pip wheel",
        "import desktop_app; import apps.desktop",
        "scripts/validate_release_zip.py",
        "gitleaks/gitleaks-action",
    )
    for token in required_tokens:
        if token not in active:
            errors.append(f"gate obrigatório ausente no CI: {token}")


def validate_foundation(root: Path) -> list[str]:
    """Return all structural errors without traversing operational directories."""
    root = root.resolve()
    errors: list[str] = []
    _check_required_files(root, errors)
    _check_skills(root, errors)
    _check_template(root / "docs/templates/SPEC_TEMPLATE.md", SPEC_TEMPLATE_SECTIONS, errors)
    _check_template(root / "docs/templates/ADR_TEMPLATE.md", ADR_TEMPLATE_SECTIONS, errors)
    _check_agents(root, errors)
    _check_governance_records(root, errors)
    _check_local_links(root, errors)
    _check_invocation_policies(root, errors)
    _check_ci(root, errors)
    return errors


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    errors = validate_foundation(project_root())
    if errors:
        print("Engineering foundation: INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Engineering foundation: VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
