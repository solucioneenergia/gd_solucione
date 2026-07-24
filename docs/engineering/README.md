# Fundação de engenharia

Esta fundação torna persistentes o fluxo Spec Driven Development, TDD, revisão independente, segurança e quality gates. O fluxo recomendado está no [modelo operacional](OPERATING_MODEL.md) e o critério de conclusão na [Definition of Done](DEFINITION_OF_DONE.md).

Fluxo recomendado: Entendimento → SPEC → ADR quando necessária → baseline → RED → GREEN → REFACTOR → suíte completa → revisão independente → evidências.

As Specs ficam em [`specs/`](../../specs/README.md), as ADRs canônicas em [`docs/adr/`](../adr/README.md), os templates em [`docs/templates/`](../templates/SPEC_TEMPLATE.md) e as Skills em `.agents/skills/`.

## Uso das Skills

```text
Use $engineering-orchestrator para conduzir esta alteração.
Use $spec-driven-development para criar ou revisar a SPEC.
Use $tdd-workflow para implementar a alteração.
Use $senior-code-review para revisar o diff sem modificar arquivos.
```

As três primeiras podem ser ativadas implicitamente quando a descrição corresponder à tarefa. `senior-code-review` exige invocação explícita para preservar a independência e o modo somente leitura.

## Validação e extensão

Execute `python scripts/validate_engineering_foundation.py`. Para adicionar uma Skill, crie `.agents/skills/<nome>/SKILL.md` com frontmatter `name` e `description`, adicione `agents/openai.yaml`, mantenha o nome igual ao diretório e amplie validador e testes se a Skill passar a ser obrigatória.
