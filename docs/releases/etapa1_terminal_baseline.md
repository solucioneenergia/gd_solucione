# Baseline local da Etapa 1 — terminal

Data: 2026-08-09
Classificação: evidencia de engenharia; não é release nem autoriza produção

## Comandos de identidade executados

```text
git status --short --untracked-files=all
git branch --show-current
git rev-parse HEAD
git describe --tags --always --dirty
git tag --points-at HEAD
git log --oneline --decorate -n 10
git remote -v
git ls-remote origin refs/heads/main refs/heads/release/2.0.2-layered-local refs/tags/v2.0.2
```

## Resultado inicial

- Branch: `main`.
- HEAD: `070a4206b017b51f142919e6e98a0b290e0c8953`.
- Tag no HEAD: `v2.0.1`.
- Describe: `v2.0.1-dirty`.
- Upstream da branch: ausente.
- Estado expandido observado: 74 caminhos rastreados alterados/removidos e 41 arquivos não
  rastreados.
- Tag `v2.0.2`: commit `da4efa7ea1165e605c8eccac8cbb92aacac154fa`, também presente na
  branch local/remota `release/2.0.2-layered-local`.
- `origin/main` observado: `c5dc110962a62090f22467725ae548f8345a113f`.

## Classificação do drift

- 59 dos 74 caminhos rastreados diferem do HEAD, mas são idênticos à linha `v2.0.2`.
- 14 rastreados e uma exclusão possuem drift além de `v2.0.2`.
- Dos 36 arquivos não rastreados fora de `outputs/`, 32 são idênticos a `v2.0.2` e quatro
  possuem drift posterior.
- `outputs/` contém artefatos locais do usuário: devem ser preservados, ignorados pelo Git e
  excluídos de release.
- Caches, `node_modules` e `dist` ignorados não definem a baseline e precisam de scanner
  independente quando forem artefatos distribuíveis.

## Decisão de baseline

A baseline histórica canônica desta etapa é `v2.0.2`/`da4efa7`. O resultado da Etapa 1 deve ser
um novo commit de estabilização que importe somente o drift validado, preserve os artefatos
locais fora do Git e tenha todos os gates verdes. Não será feito reset destrutivo nem promoção
do worktree misto atual.

`origin/main` não foi escolhido porque o objeto remoto não estava disponível localmente para
auditoria sem alterar referências. A decisão pode ser revista por merge/PR após a estabilização.

## Linha de base de qualidade

```text
python -m pytest -q
855 passed, 1 skipped in 165.88s

python -m ruff check automacao_gd apps src scripts tests
All checks passed!

python -m mypy automacao_gd
Success: no issues found in 69 source files

python scripts/validate_engineering_foundation.py
Engineering foundation: VALID
```

O skip ocorreu por indisponibilidade de privilégio de symlink no Windows. Nenhum Portal, Edge,
PDF, workbook ou pasta operacional foi acionado durante esses gates.
