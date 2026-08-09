# Ledger de auditoria da candidata 2.0.2

Data: 2026-08-03
Estado: homologação final controlada concluída; apta para revisão de diff e commit local
Branch/commit: `main` / `070a4206b017b51f142919e6e98a0b290e0c8953`
Tag no HEAD: `v2.0.1`; nenhuma tag 2.0.2 foi criada ou movida.

## 1. Decisão executiva

A baseline 2.0.2 ficou instalável, empacotável e reproduzível nos gates locais aprovados. O
frontend canônico, wheel, instalação isolada, bundle PyInstaller, ZIP seguro, engineering
foundation, Gitleaks, pip-audit e smoke do EXE oficial do staging foram executados sem acessar
Portal, planilha, PDFs ou diretórios reais. A candidata está apta somente para revisão de diff e
commit local. CI remoto, tag `v2.0.2`, canário, release e produção permanecem pendentes e não
autorizados.

## 2. Ledger de evidências

| Entidade | Caminho | Símbolo/contrato | Evidência e comando | Resultado | Classificação |
|---|---|---|---|---|---|
| Git | raiz | branch/HEAD/tags/status | `git status`, `git branch --show-current`, `git rev-parse HEAD`, `git tag --points-at HEAD` | worktree já estava sujo; `main`; commit e tag acima | confirmado |
| Snapshot | fora do repositório | patch e manifestos preexistentes | snapshot antes da primeira edição | patch SHA-256 `2FF2DE86B0ADD15DECB9271B07AEEF544361F2791F765511BCC53B91AC22A721` | confirmado |
| Dados rastreados | índice/histórico Git | classificação por caminho | `git ls-files`; `git log --all --name-only` | zero `.env` sensível, perfil, log, PDF/planilha ou ZIP; um `.env.example` | confirmado por metadados |
| Dado local | `outputs/` | arquivo não rastreado preexistente | `git status --short`; política do gerador | preservado fisicamente e excluído do ZIP | confirmado |
| Mock histórico | antigo arquivo de dados do frontend canônico | registros ambíguos | diff e busca apenas por padrão | removido do worktree; caminho permanece no histórico Git | confirmado |
| Desktop canônico | `apps/desktop` | bootstrap, janela, bridge e worker | ADR 0005 e testes de contrato | única pilha que recebe mudanças; legado preservado | confirmado |
| Frontend canônico | `apps/desktop/frontend` | pnpm 11.9.0, lock, test, build | `pnpm install --frozen-lockfile`; `pnpm test`; `pnpm build` | 3 testes e build Vite aprovados; `dist` gerado | executado/aprovado |
| Frontend legado | `frontend` | pilha congelada | decisão ADR e teste de exclusão | preservado no repositório; zero entradas no ZIP | confirmado |
| Empty state | frontend canônico | tabela sem protocolos embutidos | testes Node/Pytest e busca por padrão | sem identificadores operacionais no runtime canônico | aprovado |
| Placeholders | bridge/UI | ações indisponíveis | testes de bridge e frontend | desabilitados na UI e falham fechado no backend | aprovado |
| Cancelamento | worker/bridge | `cancel_event` e sinal cancelado | testes cooperativos e de fronteira Qt | não emite sucesso após cancelamento | aprovado |
| Confirmação forte | bridge/backend | ambiente, operação e limite | testes negativos/positivos | reutiliza a autorização existente do pipeline | aprovado |
| WebEngine | `apps/desktop/window.py` | allowlist, interceptor e channel | testes de URL, navegação e attach | remoto/fora da raiz bloqueado; bridge só após load autorizado | aprovado estaticamente/testado |
| Versão | Python/frontend/changelog | 2.0.2 | teste de consistência | consistente; sem tag/publicação | aprovado |
| Wheel baseline | artefato 2.0.0 | descoberta setuptools | build, inspeção e venv limpo | não continha `apps`; import falhava | confirmado/reprovado |
| Wheel final | artefato 2.0.2 | packages e package data | `pip wheel`, inspeção ZIP | 152 entradas; `apps`, `dist`, `static`; 5 arquivos em `dist/assets` | aprovado |
| Instalação final | `C:\g202v2_75a4` | dependências/imports | `pip install`, `pip check`, imports com `-I` fora do workspace | sem requisitos quebrados; imports aprovados | executado/aprovado |
| Dependências | `requirements*.txt` | runtime/dev | `pip-audit -r requirements.txt` | vulnerabilidade de pytest removida do runtime; auditoria final sem achados | aprovado |
| Bundle Windows | caminho temporário curto | PyInstaller onedir | PyInstaller 6.21.0 e inspeção estrutural | 3.365 arquivos; EXE e frontends presentes; sem `.env`, perfil ou planilha | build aprovado; smoke não executado |
| Release ZIP | artefato diagnóstico externo | allowlist/denylist/manifestos | gerador e validador independentes | 309 incluídos; zero frontend legado, `.env` sensível, documento operacional, `node_modules` ou ZIP aninhado | aprovado como diagnóstico |
| Testes Python | workspace | suíte integral | `python -m pytest -q` em pytest 9.1.1 | 841 aprovados; 1 skip por privilégio de symlink Windows | aprovado com limitação ambiental |
| Ruff/compile/foundation | workspace | gates estáticos | comandos canônicos | todos código 0 | aprovado |
| MyPy | `automacao_gd apps` | tipagem global | `python -m mypy automacao_gd apps` | 33 erros em 9 arquivos; baseline era 38 em 11 | reprovado/preexistente |
| Secret scan | workspace/histórico | gitleaks | detecção de binário | ferramenta ausente, código equivalente 127 | não executado/bloqueado |
| CI | `.github/workflows/ci.yml` | quality/package/gitleaks | inspeção e validador de fundação | workflow criado; execução remota inexistente neste workspace sem remote | confirmado estaticamente/não executado |

## 3. Comandos e códigos de saída relevantes

| Fase | Comando resumido | Código | Resultado |
|---|---|---:|---|
| Baseline | `python -m pytest -q` | 0 | 815 aprovados; 1 skip |
| Baseline | `python -m mypy automacao_gd apps` | 1 | 38 erros/11 arquivos |
| RED | testes novos da candidata/release | 1 | 19 falhas pelo motivo esperado; 1 aprovado |
| Focado final | quatro arquivos de candidata/release/bridge | 0 | 44 aprovados |
| Compilação | `python -m compileall -q ...` | 0 | aprovado |
| Lint | `python -m ruff check automacao_gd apps src scripts tests` | 0 | aprovado |
| Tipagem | `python -m mypy automacao_gd apps` | 1 | 33 erros/9 arquivos |
| Fundação | `python scripts/validate_engineering_foundation.py` | 0 | `VALID` |
| Frontend | install imutável, test e build | 0 | 3 testes; Vite aprovado |
| Suíte final | Pytest 9.1.1 | 0 | 841 aprovados; 1 skip |
| Auditoria inicial | `pip-audit -r requirements.txt` | 1 | um achado em dependência de teste indevidamente no runtime |
| Auditoria final | mesmo comando | 0 | nenhum achado conhecido |
| Wheel final | `pip wheel . --no-deps --no-build-isolation` | 0 | SHA-256 `9f039be28dddad81e9ececad9b9efa1461e6c1344f77338943de186d705e5b64` |
| Instalação final | install, `pip check`, imports isolados | 0 | aprovado |
| PyInstaller 1 | adaptação com spec externo e dados relativos | 1 | caminho de dados incorreto da adaptação |
| PyInstaller 2 | dados absolutos, saída longa | 1 | limite de caminho no `COLLECT` do PySide6 |
| PyInstaller final | cache anterior e dist curto | 0 | bundle produzido |
| ZIP final | gerador + validador | 0 | aprovado como diagnóstico |
| Gitleaks local | detecção | 127 | ferramenta ausente |

## 4. Arquivos desta tarefa

Alterados/criados: `.github/workflows/ci.yml`, `.gitignore`, `CHANGELOG.md`, `README.md`,
`requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `desktop_app.py`,
`automacao_gd/__init__.py`, `apps/__init__.py`, `apps/desktop/window.py`,
`apps/desktop/bridge/automation_bridge.py`, `apps/desktop/bridge/file_bridge.py`,
`apps/desktop/workers/automation_worker.py`, o frontend canônico (manifestos, lock,
workspace, fonte, fallback e testes), `scripts/build_windows.ps1`, os três validadores/gerador,
ADR 0005 e índices de ADR, a SPEC de prontidão e os testes desktop/release relacionados.

Removido no diff: `apps/desktop/frontend/src/data/mockDashboard.ts`. Nenhum arquivo local de
dados foi apagado. As 21 modificações rastreadas e 24 arquivos não rastreados que já existiam
foram preservados; o patch/manifesto original está no snapshot externo.

## 5. Critérios e pendências

Atendidos com evidência: ZIP higienizado, frontend canônico reproduzível, wheel instalável,
recursos empacotados, versão 2.0.2 consistente, WebEngine/bridge restritos, empty state,
placeholders fechados, cancelamento cooperativo, confirmação backend, testes, Ruff,
compileall, auditoria de runtime e build estrutural do bundle.

Não atendidos: MyPy global; secret scan local; execução real do CI; smoke do EXE; remoção
dos literais ambíguos no frontend legado fora do escopo autorizado; worktree limpo e identidade
de release por commit/tag. Produção e canário permanecem proibidos.

Decisões humanas necessárias: autorizar ou planejar o saneamento tipado dos nove arquivos
legados; decidir se o frontend legado deve ser sanitizado no repositório ou apenas mantido fora
da distribuição; executar CI/gitleaks em ambiente com as ferramentas; aprovar um smoke offline
do EXE com configuração sintética; somente depois definir commit/tag 2.0.2.

## 6. Rollback

Não usar reset destrutivo. Restaurar somente os arquivos listados na seção 4 a partir do
commit base e remover somente os novos arquivos desta tarefa após conferir o manifesto. Em
seguida, reaplicar o patch preexistente do snapshot e comparar `preexisting-status.txt`. Os
artefatos e venvs temporários podem ser descartados apenas mediante autorização explícita.

## 7. Artefatos para a próxima etapa

- snapshot: `C:\CAMINHO\SINTETICO
- wheel final: subdiretório `final_artifacts_2_0_2\post_audit_wheel`;
- relatórios do ZIP: subdiretório `final_artifacts_2_0_2\final_validation_reports`;
- bundle estrutural: `C:\g202b_75a4\AutomacaoGDNeoenergia`;
- venv de instalação final: `C:\g202v2_75a4`.

Este ledger não autoriza lançamento nem execução em produção.

## 8. Fechamento dos gates locais pré-promoção — 2026-08-04

Esta seção substitui, para decisão local, as pendências registradas nas seções 1, 3 e 5.
A classificação atual é **PRONTA PARA GATE EXTERNO DE PROMOÇÃO**, não aprovada para
produção. Não houve commit, tag, push, acesso ao Portal, abertura de planilha oficial ou uso
de diretório real de cliente.

### 8.1 Identidade, isolamento e rollback

- workspace: branch `main`, commit `070a4206b017b51f142919e6e98a0b290e0c8953`, tag no
  HEAD `v2.0.1`, sem remote;
- estado inicial desta etapa: 57 caminhos rastreados alterados e 33 não rastreados;
- estado final: 67 caminhos rastreados alterados e 34 não rastreados; staging ignorado;
- snapshot: `C:\CAMINHO\SINTETICO;
- patch inicial SHA-256: `E58123EEBACF581CED0633760C79B239AFC19F8B896B6D6E710A4DAF5DBC7530`;
- patch de evidência anterior à consolidação deste próprio ledger SHA-256:
  `C18743CA081355FE9DA157BD90AA0E1D0A5108D19F4EDB7C863BAF58D1F0C47B`;
- manifesto dos 18 arquivos desta camada SHA-256:
  `836103C5E3F3561EA26D6261B448B5EC5396B0A765585D5A7524AF4C61973BDD`;
- worktree isolado: `C:\CAMINHO\SINTETICO
- os 18 hashes finais coincidem entre workspace principal e isolado;
- rollback: não usar reset destrutivo; preservar as camadas anteriores, aplicar o patch do
  snapshot e conferir os manifestos. Somente artefatos sintéticos desta etapa podem ser
  descartados. Dez diretórios sintéticos de diagnóstico foram removidos; nenhum dado
  preexistente do usuário foi removido.

### 8.2 Ledger de evidências desta etapa

| Entidade | Caminho/símbolo | Comando/evidência | Resultado | Classificação |
|---|---|---|---|---|
| MyPy | `automacao_gd` | `python -m mypy automacao_gd` | 69 arquivos, zero erro; baseline: 33 erros/9 arquivos | executado/aprovado |
| Tipagem Qt | `_qt.py`, `main_window.py`, `web_bridge.py`, `tkinter_app.py` | estreitamento de tipos | sem `ignore` novo ou mudança de contrato público | aprovado |
| Tipagem infraestrutura | cleanup, pasta de cliente, browser e CDP | MyPy + testes existentes | opcionais estreitados; literais tipados | aprovado |
| Frontend legado | `frontend/src/App.tsx` | busca, install congelado e build | três identificadores removidos; empty state; Vite aprovado | executado/aprovado |
| Symlink | inventário de manutenção | teste real + equivalente | real pulado por WinError 1314; equivalente passou e impede travessia | aprovado com limitação |
| Teste operacional | teste de backfill | suíte sem `data/logs` | acoplamento confirmado; substituído por workbook/protocolo sintéticos em `tmp_path` | corrigido/aprovado |
| Gitleaks | 8.30.1 oficial | checksum, `git` e `dir` com redação | histórico: 2 commits/0; isolado: 0; ZIP final: 0 | executado/aprovado |
| CI | workflow | action `v2.3.9`, `GITLEAKS_VERSION=8.30.1` | versão fixa; comandos equivalentes locais verdes | local aprovado; remoto não executado |
| Frontend canônico | `apps/desktop/frontend` | pnpm 11.9.0, frozen install, test, build | 3 testes; Vite 5.4.21; `dist` produzido | executado/aprovado |
| Gates Python | compile, Ruff, fundação | comandos do workflow | todos código 0 | executado/aprovado |
| Suíte final | `python -m pytest -q` | candidata isolada | 848 aprovados, 1 skip de privilégio symlink | aprovado |
| Cobertura | `pytest --cov=automacao_gd` | suíte anterior ao teste unitário final do harness | 847 aprovados, 1 skip, 77% | executado/aprovado |
| Dependências | `pip-audit -r requirements.txt` | ambiente local | nenhum achado conhecido | executado/aprovado |
| Wheel | 2.0.2 | build, inspeção e venv final | 152 entradas; `apps.desktop`, entrypoint, 6 entradas `dist`; zero proibida | executado/aprovado |
| Instalação | venv final | install `--no-deps`, imports `-I` fora do repo | versão/imports aprovados | executado/aprovado |
| ZIP | release limpa | gerador + validador | 312 incluídos; 20.770 excluídos; `APROVADO` | executado/aprovado |
| PyInstaller | onedir | PyInstaller 6.21.0/Python 3.14 | 3.365 arquivos; build código 0 | executado/aprovado |
| Smoke EXE | processo real | harness offline/CDP local | frontend, bridge, empty state e shutdown; zero HTTP(S); operação não iniciou | executado/aprovado |
| Versão | Python/pyproject/frontend | leitura estruturada | 2.0.2 consistente | aprovado; tag pendente |

### 8.3 RED/GREEN e falhas registradas

1. RED inicial: 6 falhas corretas (literais legados, pin, staging, harness e symlink);
   GREEN: 6 aprovados e 1 skip ambiental.
2. Primeira suíte isolada: 846 aprovados, 1 falha, 1 skip. A falha lia plano em
   `data/logs`; o teste passou a usar fixture sintética.
3. Primeiro smoke: falha fechada porque a URL sintética HTTP contrariava a validação HTTPS;
   o ambiente foi corrigido e passou a ser validado em teste.
4. Segundo smoke: o CDP subiu, mas o Playwright tentou gerenciamento de contexto não
   suportado pelo Qt WebEngine. O harness passou a usar WebSocket CDP local da biblioteca
   padrão, allowlist loopback e teste de alvo canônico.
5. Smoke final: código 0, `approved=true`, bridge/frontend/empty state verdadeiros,
   `external_requests=0`, `operation_started=false`, shutdown código 0.
6. PyInstaller avisou sobre `tzdata` e um plugin QML privado ausente. O aplicativo não usa
   QML e o smoke real passou; os avisos foram preservados para revisão externa.

### 8.4 Arquivos desta camada

Alterados/criados: `.github/workflows/ci.yml`, `.gitignore`, `frontend/src/App.tsx`,
`specs/desktop_production_readiness.md`, `scripts/smoke_desktop_executable.py`,
`tests/test_pre_promotion_gates.py`, `tests/test_project_maintenance_inventory_service.py`,
`tests/test_backfill_apply_operational_safety.py`,
`automacao_gd/application/project_maintenance_inventory_service.py`,
`automacao_gd/infrastructure/files/cleanup.py`, `client_folder_service.py`, `browser.py`,
`cdp_browser.py`, `automacao_gd/presentation/operational_output.py`, `tkinter_app.py` e
`automacao_gd/presentation/desktop/{_qt.py,main_window.py,web_bridge.py}`. Nenhum arquivo foi
removido do versionamento nesta camada. Dados locais, `outputs/`, `.env`, perfis e evidências
preexistentes foram preservados e não copiados para a candidata.

### 8.5 Artefatos finais e limitações

Staging: `C:\CAMINHO\SINTETICO.

- wheel SHA-256: `0CA845821533970AAE1639E092E6692E6906D2B1AB47EDBD5457072EC9C2950B`;
- ZIP SHA-256 final: registrado externamente em `reports/artifact-manifest.json` para evitar
  autorreferência deste ledger incluído no próprio ZIP;
- EXE SHA-256: `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E`;
- bundle agregado SHA-256: `F[PROTOCOLO REDIGIDO]CB709E021BE97DEAB65D688F01B45E5D5DCA9959B4B77E422E096`;
- manifesto: `reports/artifact-manifest.json`; smoke: `reports/desktop-smoke-approved.json`;
  validação: `reports/release-validation-final/`; scans: `reports/gitleaks-*.json`;
  avisos: `reports/pyinstaller-warnings.txt`.

Limitações: a máquina oferece apenas Python 3.14, enquanto o workflow declara 3.12/3.13 em
Linux; o CI remoto ainda precisa ser executado; o teste de symlink real requer privilégio
Windows, embora o equivalente tenha passado; o worktree segue deliberadamente sujo e a tag
no HEAD é 2.0.1. Próximas decisões humanas: revisar/aprovar o diff, criar commit imutável,
executar CI externo e somente então decidir a tag 2.0.2. Produção e canário seguem proibidos.

## 9. Homologação final controlada — 2026-08-05

### 9.1 Diagnóstico confirmado

- Git: `main`, commit `070a4206b017b51f142919e6e98a0b290e0c8953`, tag no HEAD
  `v2.0.1`, sem tag `v2.0.2` e sem remote.
- O ZIP inicialmente disponível tinha 312 entradas, não as 314 da evidência preliminar; o
  release validator aprovava, mas o foundation extraído reprovava com dez arquivos ausentes.
- O workspace continha todos os `AGENTS.md` e `.agents/skills/**` obrigatórios.
- O staging tinha o EXE antigo `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E`;
  `dist` e a cópia externa aprovada tinham `91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467`.
- `specs/README.md` ainda classificava a SPEC desktop como rascunho, enquanto a própria SPEC
  estava em implementação controlada.

### 9.2 Correções localizadas

- O gerador passou a incluir `AGENTS.md`, `.agents/` e somente `frontend/AGENTS.md`, sem liberar
  o frontend legado nem relaxar a denylist de dados sensíveis.
- O release validator passou a exigir a lista canônica `REQUIRED_FILES` da engineering
  foundation.
- Fixtures sintéticos de release foram atualizados para o novo contrato; nenhum assert foi
  enfraquecido.
- SPEC e índice agora registram “em homologação final controlada da candidata 2.0.2”.
- Bundle, wheel, ZIP e relatórios do staging foram atualizados; os artefatos anteriores foram
  preservados no backup de recuperação da seção 9.4.

### 9.3 Evidências e gates

| ID | Afirmação | Fonte/comando | Código | Resultado | Classificação |
|---|---|---|---:|---|---|
| HF-001 | Foundation do workspace | `python scripts/validate_engineering_foundation.py` | 0 | `VALID` | aprovado |
| HF-002 | RED do ZIP anterior | validator executado após extração temporária | 1 | dez arquivos de governança ausentes | causa confirmada |
| HF-003 | Contrato release/foundation | dois testes TDD focados | 0 | 2 aprovados após RED esperado | aprovado |
| HF-004 | Staging sincronizado | manifesto por arquivo e SHA-256 | 0 | 3.435 arquivos; zero diferenças para bundle externo aprovado | aprovado |
| HF-005 | EXE oficial | `Get-FileHash` | 0 | `91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467` | aprovado |
| HF-006 | Smoke do staging | `scripts/smoke_desktop_executable.py` | 0 | `approved=true`; zero HTTP(S); operação não iniciou | aprovado |
| HF-007 | Compile/Ruff/MyPy | comandos do workflow | 0 | sem saída/achados; MyPy 69 arquivos | aprovado |
| HF-008 | Testes relacionados | foundation, candidata e release | 0 | 14, 15 e 19 aprovados | aprovado |
| HF-009 | Suíte local equivalente | `pytest -q --cov=automacao_gd` | 0 | 852 aprovados; 1 skip ambiental; 77% | aprovado com limitação |
| HF-010 | Frontend | frozen install, test, build | 0 | 3 testes; build Vite; `dist` idêntico ao ZIP/bundle | aprovado |
| HF-011 | Dependências | `python -m pip_audit -r requirements.txt` | 0 | nenhum achado conhecido | aprovado |
| HF-012 | Segredos | Gitleaks 8.30.1 `git` e `dir` no ZIP seguro | 0 | 2 commits/0 leaks; ZIP/0 leaks | aprovado |
| HF-013 | Wheel | build, install `--no-deps`, import `-I` | 0 | SHA-256 `9B182C5051634FDF9A2A3D0D197940C632F8E68FE9343260D07AA75F8392D99C` | aprovado |
| HF-014 | CI remoto | inspeção do workspace | — | não executado; não há remote | pendente externo |

### 9.4 Artefatos, rollback e decisão

Staging oficial:
`C:\CAMINHO\SINTETICO.

Backup recuperável do bundle, ZIP, wheel e relatórios anteriores:
`C:\CAMINHO\SINTETICO

O hash final do ZIP e o manifesto consolidado ficam em `reports/artifact-manifest.json` para
evitar autorreferência deste ledger incluído no próprio ZIP. A raiz sintética do smoke e o venv
de import foram criados sob `%TEMP%`, sem dados reais.

Decisão: **APTA PARA REVISÃO DE DIFF E COMMIT LOCAL**. Não foram executados commit, tag, push,
release, CI remoto, canário ou produção.
