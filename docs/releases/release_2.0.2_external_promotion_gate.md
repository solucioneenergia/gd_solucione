# Gate externo de promoção controlada da candidata 2.0.2

Data: 2026-08-04

Classificação: **BLOQUEADA POR RISCO DE RELEASE**

Esta etapa não criou commit, tag, push, remote, release, produção ou canário. O gate local da
candidata continua reproduzível no ambiente principal, mas a validação da matriz expôs um risco
de empacotamento em Python 3.12: `pip wheel . --no-deps --no-build-isolation` falha em venv limpo
após instalar apenas `requirements-dev.txt` porque `setuptools.build_meta` não está presente.
Instalar `setuptools` somente no venv temporário confirmou a causa e a suíte Python 3.12 passou
em seguida.

## Identidade Git

| Afirmação | Resultado |
|---|---|
| Branch | `main` |
| Commit | `070a4206b017b51f142919e6e98a0b290e0c8953` |
| Tag no HEAD | `v2.0.1` |
| Tag `v2.0.2` | ausente |
| Remote | ausente |
| Estado inicial/final | worktree suja; nenhum commit/tag/push criado |

## Ledger atualizado

| ID | Afirmação | Fonte | Comando | Código | Resultado | Classificação |
|---|---|---|---:|---:|---|---|
| EXT-001 | Identidade Git permanece na candidata esperada | Git | `git status --short --branch`; `git rev-parse HEAD`; `git tag --points-at HEAD`; `git remote -v` | 0 | `main`, commit esperado, `v2.0.1`, sem remote | Confirmado por comando |
| EXT-002 | `v2.0.2` não foi criada | Git | `git tag --list v2.0.2` | 0 | sem saída | Confirmado por comando |
| EXT-003 | Staging relativo no workspace não existe | workspace | `Test-Path artifacts/release-candidate/2.0.2` | 0 | ausente | Não localizado |
| EXT-004 | Staging absoluto do ledger existe | artefato | `Test-Path C:\Users\Solucione\gd_neoenergia_rc_2_0_2_isolated_95f3a4bc\artifacts\release-candidate\2.0.2` | 0 | encontrado | Confirmado por artefato |
| EXT-005 | Manifesto de artefatos localizado | artefato | `Get-Content reports/artifact-manifest.json` | 0 | schema 1, versão 2.0.2 | Confirmado por artefato |
| EXT-006 | Wheel de staging íntegro | artefato | `Get-FileHash` | 0 | `0CA845821533970AAE1639E092E6692E6906D2B1AB47EDBD5457072EC9C2950B` | Confirmado por comando |
| EXT-007 | ZIP de staging íntegro | artefato | `Get-FileHash` | 0 | `5887DCE67114CCF224B713AA68B80567AE9091C51B6ACFDCC0F853FFF25FB74B` | Confirmado por comando |
| EXT-008 | EXE de staging íntegro | artefato | `Get-FileHash` | 0 | `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E` | Confirmado por comando |
| EXT-009 | Bundle manifest íntegro | artefato | `Get-FileHash reports/desktop-bundle-manifest.csv` | 0 | `BEAB26E244FA474708AEF67015EFCBC955A6A598CCC45684771327CE5453B3A6`; 3365 arquivos | Confirmado por comando |
| EXT-010 | Agregado do bundle registrado | manifesto | `artifact-manifest.json` | 0 | `F3943851323CB709E021BE97DEAB65D688F01B45E5D5DCA9959B4B77E422E096`; algoritmo não localizado para recomputação independente | Confirmado por artefato |
| EXT-011 | Smoke de staging continua aprovado por artefato | artefato | leitura de `desktop-smoke-approved.json` | 0 | `approved=true`, zero HTTP(S), shutdown 0 | Confirmado por artefato |
| EXT-012 | Smoke EXE offline reexecutado | comando | `python scripts/smoke_desktop_executable.py ...` | 0 | frontend, bridge, empty state e shutdown aprovados; zero HTTP(S) | Confirmado por comando |
| EXT-013 | ZIP de staging validado por artefato | artefato | leitura de `release_validation.json` | 0 | `valid=true`, zero problemas | Confirmado por artefato |
| EXT-014 | ZIP limpo reconstruído e validado | comando | `create_clean_release_zip.py`; `validate_release_zip.py` | 0 | 312 incluídos; `valid=true`; SHA temporário `04BB36D2000BF93410C80DECCD77FEC71A059123C861D689166D4904C2FBBEB7` | Confirmado por comando |
| EXT-015 | Gitleaks de staging sem achados | artefato | leitura dos relatórios JSON | 0 | três relatórios `[]` | Confirmado por artefato |
| EXT-016 | Gitleaks local reexecutado | comando | `gitleaks detect --source . --redact` | 0 | 2 commits, nenhum leak, relatório JSON vazio | Confirmado por comando |
| EXT-017 | MyPy local aprovado | comando | `python -m mypy automacao_gd apps` | 0 | 80 arquivos sem erro | Confirmado por comando |
| EXT-018 | Pytest local aprovado | comando | `python -m pytest -q` | 0 | 848 passed, 1 skipped | Confirmado por comando |
| EXT-019 | Ruff local aprovado | comando | `python -m ruff check automacao_gd apps src scripts tests` | 0 | sem achados | Confirmado por comando |
| EXT-020 | Compileall local aprovado | comando | `python -m compileall -q automacao_gd apps src scripts app.py desktop_app.py` | 0 | sem saída | Confirmado por comando |
| EXT-021 | Fundação aprovada | comando | `python scripts/validate_engineering_foundation.py` | 0 | `Engineering foundation: VALID` | Confirmado por comando |
| EXT-022 | `pip-audit` via PATH indisponível | comando | `pip-audit -r requirements.txt` | 1 | executável ausente | Bloqueado |
| EXT-023 | Auditoria de dependências aprovada pelo workflow | comando | `python -m pip_audit -r requirements.txt` | 0 | nenhuma vulnerabilidade conhecida | Confirmado por comando |
| EXT-024 | Frontend canônico falha sem Node no PATH | comando | `pnpm install --frozen-lockfile; pnpm test` | 1 | `node` não reconhecido | Bloqueado |
| EXT-025 | Frontend canônico aprovado com Node 24 local | comando | PATH com Node 24; `pnpm install`; `pnpm test`; `pnpm build` | 0 | 3 testes e Vite build aprovados | Confirmado por comando |
| EXT-026 | Frontend legado aprovado com Node 24 local | comando | PATH com Node 24; `pnpm install`; `pnpm build` | 0 | Vite build aprovado | Confirmado por comando |
| EXT-027 | Wheel reconstruído no Python local | comando | `python -m pip wheel . --no-deps --no-build-isolation` | 0 | wheel temporário contém `apps.desktop`, `desktop_app.py`, `dist`, zero `.env`, zero ZIP | Confirmado por comando |
| EXT-028 | Instalação limpa local aprovada | comando | venv temporário; `pip install --no-deps`; imports com `-I` | 0 | versão `2.0.2` importável fora do repo | Confirmado por comando |
| EXT-029 | Python 3.12 disponível sem ferramentas | ambiente | Python 3.12 Codex runtime | 1 | pytest/mypy/ruff ausentes inicialmente | Bloqueado |
| EXT-030 | Python 3.12 gates estáticos aprovados | comando | venv 3.12 + `requirements-dev.txt`; MyPy/Ruff/compile/foundation | 0 | todos aprovados | Confirmado por comando |
| EXT-031 | Python 3.12 suíte falha antes de `setuptools` | comando | venv 3.12 + `requirements-dev.txt`; `pytest -q` | 1 | 846 passed, 1 skipped, 2 errors em `pip wheel` | Divergente |
| EXT-032 | Causa Python 3.12 confirmada | comando | `pip wheel . --no-deps --no-build-isolation` | 2 | `Cannot import 'setuptools.build_meta'` | Confirmado por comando |
| EXT-033 | Python 3.12 passa com `setuptools` temporário | comando | `pip install setuptools>=69`; `pip wheel`; `pytest -q` | 0 | wheel gerado; 848 passed, 1 skipped | Confirmado por comando |
| EXT-034 | Python 3.13 indisponível | ambiente | `py -3.13 --version` | 0 | launcher reportou runtime ausente | Bloqueado |
| EXT-035 | Node 22 indisponível | ambiente | `node --version`; Node empacotado | 0/1 | `node` fora do PATH; Node empacotado é 24.14.0 | Bloqueado |
| EXT-036 | CI remoto não executado | Git | `git remote -v` | 0 | sem remote; push proibido | Não executado |

## Diff agrupado

| Grupo | Arquivos rastreados |
|---|---:|
| Backend | 16 |
| CI | 1 |
| Desktop | 8 |
| Documentação | 9 |
| Frontend | 10 |
| Outros | 4 |
| Release/Scripts | 6 |
| Testes | 13 |

Snapshot prévio: 57 rastreados alterados e 33 não rastreados. Estado observado nesta etapa:
67 rastreados alterados e 34 não rastreados antes deste relatório. A camada de promoção
registrada em `reports/pre-promotion-source-layer.csv` contém 18 arquivos com hashes próprios.

## Artefatos

| Artefato | Caminho | SHA-256 / resultado |
|---|---|---|
| Manifesto | `...\reports\artifact-manifest.json` | localizado; versão 2.0.2 |
| Wheel staging | `...\wheelhouse\automacao_gd_neoenergia-2.0.2-py3-none-any.whl` | `0CA845821533970AAE1639E092E6692E6906D2B1AB47EDBD5457072EC9C2950B` |
| ZIP staging | `...\release\gd-neoenergia-2.0.2.zip` | `5887DCE67114CCF224B713AA68B80567AE9091C51B6ACFDCC0F853FFF25FB74B` |
| EXE staging | `...\desktop\AutomacaoGDNeoenergia\AutomacaoGDNeoenergia.exe` | `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E` |
| Bundle | `...\desktop\AutomacaoGDNeoenergia` | 3365 arquivos; CSV hash `BEAB26E244FA474708AEF67015EFCBC955A6A598CCC45684771327CE5453B3A6` |
| Gitleaks | `...\reports\gitleaks-*.json` | três relatórios vazios |
| Smoke | `...\reports\desktop-smoke-approved.json` | `approved=true` |
| Validação ZIP | `...\reports\release-validation-final\release_validation.json` | `valid=true` |

## Decisão

A candidata não deve avançar diretamente para commit/tag/publicação. Antes do commit, corrigir ou
validar explicitamente a dependência de build backend para Python 3.12 (`setuptools`) e executar
CI remoto em Python 3.12/3.13 com Node 22. Sem CI remoto, tag `v2.0.2`, produção e canário seguem
proibidos.

Rollback: não usar `git reset --hard`. Preservar o snapshot
`C:\Users\Solucione\AppData\Local\Temp\gd_neoenergia_pre_promotion_20260804T083508_95f3a4bc1d374d4d815c2fee95a5da4f`
e aplicar/remover apenas as camadas documentadas nos manifestos.
