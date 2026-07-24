# Spec — desktop_production_readiness

- Status: **rascunho para implementação futura**
- Tipo: auditoria técnica e especificação de prontidão; sem alteração funcional
- Data da auditoria: 2026-07-19
- Decisão arquitetural relacionada: `docs/ADR-001_DESKTOP_CANONICAL_PATH.md`

## 1. Problema

O projeto possui duas pilhas desktop e dois frontends completos. O entrypoint público atual
usa a pilha mais nova em `apps/desktop`, mas empacotamento, documentação, CI e parte das
abstrações ainda apontam para a pilha anterior. O conjunto funciona a partir da árvore de
fontes, porém ainda não há evidência suficiente de que o desktop instalado ou empacotado
preserve a mesma interface, os mesmos controles de segurança e o mesmo contrato de bridge.

Esta etapa registra o estado real e os critérios necessários para declarar o desktop pronto
para produção. Ela não autoriza execução produtiva nem mudanças no comportamento.

## 2. Comportamento atual confirmado

### 2.1 Caminho realmente executado

Em execução normal, sem `--dev`, o caminho é:

```text
desktop_app.py
  -> apps.desktop.main.main
  -> apps.desktop.window.run_desktop
  -> apps.desktop.window.DesktopVisualWindow
  -> apps/desktop/frontend/dist/index.html, se existir
  -> apps/desktop/frontend/static/index.html, na ausência do dist
```

Na auditoria de 2026-07-19, `apps/desktop/frontend/dist/index.html` **não existia**. Portanto,
o frontend efetivamente carregado era `apps/desktop/frontend/static/index.html`.

Com `--dev`, `apps.desktop.window` aponta diretamente para
`http://localhost:5173`, sem verificar previamente a disponibilidade do servidor.

### 2.2 Pilha paralela

`automacao_gd/presentation/desktop/main_window.py` é outra janela completa, com outra bridge,
outro worker e outro resolvedor de frontend. Ela procura `frontend/dist/index.html`, arquivo
existente e gerado em 2026-07-16 09:20:55. Nenhum entrypoint público encontrado na auditoria
chama essa janela; os usos restantes são testes e imports internos da própria pilha legada.

A pilha ativa ainda reutiliza `automacao_gd.presentation.desktop._qt`, de modo que a separação
entre as duas implementações não é completa.

### 2.3 Frontends

- `apps/desktop/frontend`: fonte React atual, fallback estático e assets premium. Não possui
  lockfile nem `dist` no momento da auditoria.
- `frontend`: aplicação React anterior, com `pnpm-lock.yaml`, `node_modules` e `dist` antigo.
- O fallback estático e o React atual duplicam marcação, estilos, assets e dados iniciais.
- Os assets premium WebP/PNG/SVG de `static` e `src` tinham hashes SHA-256 idênticos.
- O React atual usa mock seguro quando QWebChannel não está disponível e a tabela retorna a
  quatro registros mockados quando não recebe dados reais.

### 2.4 Bridges e ações

Na bridge ativa:

- preflight, teste CDP, dry-run de PDFs baixados, cleanup dry-run e abertura de arquivos têm
  implementação;
- `open_edge_cdp`, `inspect_portal` e `run_equipment_reformat_dry_run` são placeholders;
- produção chama o pipeline somente após confirmação literal
  `SIM, EXECUTAR PRODUÇÃO`;
- o worker usa `threading.Thread`; `stop_current_operation` marca uma flag, mas o caso de uso
  não recebe essa flag, portanto o cancelamento não é cooperativo de ponta a ponta.

Na pilha legada, a confirmação de produção aceita apenas `SIM`, contrato incompatível com a
pilha ativa.

### 2.5 Build, pacote, CI e rastreabilidade

- `pyproject.toml` declara o script `gd-neoenergia-desktop = desktop_app:main`, mas a descoberta
  de pacotes inclui somente `automacao_gd*`, `src*` e `scripts*`; `apps*` não entra no wheel.
- Não há `MANIFEST.in` nem declaração de package data para os HTML/CSS/JS/imagens do desktop.
- `scripts/build_windows.ps1` executa PyInstaller sem adicionar o frontend ativo como data.
- O CI compila/verifica `automacao_gd`, `src`, `scripts` e `tests`, mas omite `apps` e não
  instala nem compila nenhum frontend.
- O frontend ativo não possui lockfile. O único lockfile encontrado pertence ao frontend
  legado.
- `node` e `npm` não estavam disponíveis no PATH; por isso o build ativo não pôde ser
  executado. `pnpm --version` respondeu `11.9.0`, mas não substitui a ausência do runtime Node.
- A pasta auditada não é um repositório Git. `git status --short` e `git diff` não estão
  disponíveis, impedindo comprovação automática do escopo por histórico.

## 3. Comportamento esperado

Uma distribuição desktop pronta para produção deve:

1. possuir um único entrypoint e uma única pilha canônica documentada;
2. carregar um build frontend reproduzível, versionado por lockfile e incluído no artefato;
3. manter o fallback estático somente como recuperação explícita, não como segunda aplicação
   independente;
4. empacotar `apps.desktop` e todos os assets necessários no wheel/PyInstaller;
5. bloquear navegação remota e limitar o QWebChannel à origem local confiável;
6. expor apenas ações implementadas, ou apresentar placeholders inequivocamente desabilitados;
7. manter confirmação forte de produção no frontend e no backend;
8. executar operações longas sem bloquear a UI e oferecer cancelamento com contrato real;
9. ter CI cobrindo Python desktop, frontend canônico, artefato e smoke test offline;
10. possuir rastreabilidade Git e evidência de homologação offline do mesmo artefato entregue.

## 4. Escopo futuro permitido

Quando esta spec for implementada, mudanças devem permanecer em:

- entrypoints e adaptadores de apresentação desktop;
- configuração de empacotamento e package data;
- `apps/desktop` e seu frontend;
- CI e scripts de build/release;
- documentação e testes específicos do desktop.

Não é permitido reescrever domínio, parser PDF, Equipment Format V2, Excel, download,
arquivamento, state/checkpoint, cleanup ou seleção/paginação do Portal.

## 5. Critérios de aceite

### AC-01 — caminho canônico

`desktop_app.py`, console script, documentação, build e CI apontam para `apps.desktop` e para
`apps/desktop/frontend`, sem resolver silenciosamente o frontend raiz legado.

### AC-02 — build reproduzível

O frontend canônico possui lockfile compatível com o gerenciador escolhido, instalação limpa
e build em CI. O `dist/index.html` referencia apenas assets relativos existentes.

### AC-03 — artefato completo

Wheel e executável incluem módulos `apps.desktop`, QWebEngine/QWebChannel necessários e o
frontend canônico. Um smoke test abre o artefato offline sem depender da árvore do repositório.

### AC-04 — segurança do WebEngine

Somente `file`, `qrc`, `data` e HTTP localhost em modo de desenvolvimento são permitidos. O
conteúdo local não acessa URLs remotas. A origem remota não recebe o objeto backend.

### AC-05 — contrato único de bridge

Há um único contrato de nomes, payloads, sinais, progresso, erros e confirmação produtiva. A
confirmação literal permanece `SIM, EXECUTAR PRODUÇÃO` nas duas fronteiras.

### AC-06 — paridade visual e funcional

O React compilado é a fonte primária. O fallback apresenta estado de recuperação seguro e não
mantém dados/regras paralelos capazes de divergir silenciosamente.

### AC-07 — ações honestas

Botões habilitados têm caso de uso real e teste; ações ainda não conectadas ficam
explicitamente desabilitadas e identificadas, sem emitir sucesso de operação.

### AC-08 — execução e cancelamento

Operações longas não bloqueiam a thread da UI. Cancelamento é cooperativo, testado e não marca
uma operação como concluída após pedido de parada.

### AC-09 — qualidade automatizada

CI executa compileall/ruff/testes sobre `apps`, testes frontend, build frontend, validação do
artefato e auditoria de dependências. Nenhum teste usa Portal, Edge, planilha ou dados reais.

### AC-10 — rastreabilidade e homologação

O repositório Git está disponível; o diff do release é revisável. A homologação offline é feita
no artefato final e a produção permanece pendente até validação assistida autorizada.

## 6. Casos de teste para o ciclo Red -> Green -> Refactor futuro

Esta auditoria não implementa comportamento e, por isso, não adiciona testes deliberadamente
falhos à suíte homologada. Antes de qualquer correção funcional, a etapa de implementação deve
materializar estes casos e registrar o RED:

| Caso | Falha atual esperada | Evidência Green exigida |
|---|---|---|
| Wheel contém `apps.desktop` | pacote não descoberto pelo `pyproject` | import a partir de venv limpa |
| Wheel contém frontend | não há package data | HTML/CSS/JS/imagens presentes e carregáveis |
| PyInstaller abre offline | frontend não é adicionado como data | smoke test do executável isolado |
| Frontend ativo tem lockfile | lockfile ausente | instalação imutável e build repetível |
| CI compila `apps` | caminho omitido | job falha se `apps` tiver erro sintático |
| CI compila frontend ativo | job inexistente | build limpo e testes frontend em CI |
| Navegação externa é bloqueada | janela ativa usa página padrão | teste de `acceptNavigationRequest` |
| Origem remota não acessa bridge | channel sempre registrado | teste de política por origem |
| Contrato de produção é único | legado aceita `SIM` | literal forte validado nas duas bordas |
| Placeholder não simula sucesso | emite `operationFinished("placeholder")` | ação desabilitada ou status específico |
| Cancelamento alcança caso de uso | flag fica somente no worker | fake cooperativo interrompido em teste |
| React e fallback não divergem | implementações paralelas | testes de contrato/estado compartilhado |

Após o Green, o Refactor deve remover duplicação apenas quando todos os consumidores e testes
estiverem migrados; não deve haver remoção antecipada da pilha legada.

## 7. Matriz de componentes

| Componente | Ativo | Legado | Duplicado | Decisão futura |
|---|---:|---:|---:|---|
| `desktop_app.py` | Sim | Não | Não | Preservar como fachada pública |
| `apps/desktop/main.py` | Sim | Não | Parcial | Tornar único bootstrap desktop |
| `apps/desktop/window.py` | Sim | Não | Sim | Manter; incorporar hardening validado |
| `apps/desktop/bridge/*` | Sim | Não | Sim | Consolidar por contrato de application |
| `apps/desktop/workers/*` | Sim | Não | Sim | Manter após definir cancelamento real |
| `apps/desktop/frontend/src` | Condicional (`--dev`/build futuro) | Não | Sim | Fonte frontend canônica |
| `apps/desktop/frontend/dist` | Não existe | Não | Não | Gerar no build, nunca editar à mão |
| `apps/desktop/frontend/static` | Sim no estado auditado | Não | Sim | Fallback de recuperação controlado |
| `automacao_gd/presentation/desktop/main_window.py` | Não pelo entrypoint | Sim | Sim | Congelar e remover só após migração |
| `automacao_gd/presentation/desktop/web_bridge.py` | Não pelo entrypoint | Sim | Sim | Migrar capacidades úteis; depois retirar |
| `automacao_gd/presentation/desktop/_qt.py` | Sim, como dependência | Sim | Não | Mover/adaptar sem duplicar compatibilidade |
| `frontend/src` | Não | Sim | Sim | Congelar; não receber novas features |
| `frontend/dist` | Não | Sim | Sim | Não empacotar como frontend canônico |
| `frontend/pnpm-lock.yaml` | Não | Sim | Não | Não reutilizar sem decisão explícita |
| `scripts/build_windows.ps1` | Candidato de release | Não | Não | Atualizar para empacotar pilha canônica |
| `.github/workflows/ci.yml` | Sim | Não | Não | Cobrir `apps` e frontend canônico |
| `docs/DESKTOP_VISUAL_UI.md` | Sim | Não | Parcial | Documento operacional canônico |
| `docs/DESKTOP_WEB_UI.md` | Não | Sim | Sim | Marcar como histórico após migração |

## 8. Bloqueadores

### P0 — impede release desktop produtivo

1. **Pacote Python incompleto:** o console script aponta para `desktop_app`, mas `apps.desktop`
   não é incluído pela descoberta configurada no `pyproject.toml`.
2. **Artefato PyInstaller incompleto:** o script não adiciona o frontend canônico nem valida
   sua presença no executável isolado.
3. **Canal privilegiado sem hardening equivalente:** a janela ativa usa `QWebEnginePage`
   padrão, sem a allowlist local e sem desabilitar acesso remoto já existentes na janela
   legada. A bridge expõe ações de filesystem e produção.

### P1 — alta prioridade antes da homologação assistida

1. Duas pilhas desktop e dois contratos de bridge incompatíveis coexistem.
2. Não há `dist` nem lockfile no frontend ativo; o build não é reproduzível.
3. CI omite `apps`, frontend, empacotamento e smoke test do desktop.
4. Documentação principal (`README.md` e `docs/DESKTOP_WEB_UI.md`) direciona para o frontend
   legado, enquanto a documentação visual direciona para o ativo.
5. `open_edge_cdp`, `inspect_portal` e reformatação são placeholders em botões expostos.
6. Cancelamento não alcança os casos de uso em execução.
7. A pasta auditada não possui metadados Git, inviabilizando diff, revisão e rollback por
   commit.
8. A camada `apps.desktop` importa infraestrutura/configuração e o shim `_qt` legado; a
   fronteira arquitetural ainda não está consolidada.

### P2 — dívida controlável

1. Fallback estático e React repetem layout, assets e dados iniciais.
2. O card visual informa Python 3.11, mas o projeto exige Python 3.12–3.14 e a auditoria rodou
   em Python 3.14.
3. `--dev` não apresenta diagnóstico específico quando o Vite não está disponível.
4. Existem `__pycache__` e `node_modules` locais; estão ignorados, mas precisam permanecer fora
   do release.
5. Os dois frontends contêm registros demonstrativos com aparência operacional; devem ser
   inequivocamente sintéticos no produto distribuído.

## 9. Riscos e rollback

| Risco futuro | Mitigação | Rollback |
|---|---|---|
| Quebrar o wrapper existente | teste de import e smoke test do entrypoint | restaurar apenas a fachada anterior |
| Perder capacidade presente na bridge legada | inventário e testes de contrato antes da migração | manter módulo legado congelado |
| Empacotar segredo/dado operacional | allowlist de package data e auditoria do artefato | reprovar e destruir artefato |
| React falhar em `file://` | `base: "./"` e teste offline | ativar fallback estático seguro |
| Hardening bloquear assets locais | teste de cada esquema/origem permitido | reverter somente política de página |
| Cancelamento deixar estado parcial | checkpoints existentes e testes com fake | desabilitar botão até contrato cooperativo |

Nenhum rollback deve usar comandos destrutivos sobre alterações não identificadas. Toda futura
mudança deve ocorrer em repositório Git válido e em commits pequenos.

## 10. Definition of Done para produção

- [ ] Todos os P0 e P1 encerrados com teste automatizado.
- [ ] ADR canônico aceito pela equipe.
- [ ] Uma única pilha recebe novas features; legado está congelado e sinalizado.
- [ ] Lockfile e build frontend reproduzível em máquina limpa e CI.
- [ ] Wheel e executável contêm `apps.desktop` e frontend canônico.
- [ ] Smoke test offline abre exatamente o artefato a distribuir.
- [ ] WebEngine bloqueia navegação/origens não confiáveis.
- [ ] Confirmação forte de produção validada em todas as fronteiras.
- [ ] Placeholders não aparecem como ações operacionais habilitadas.
- [ ] Progresso, erro e cancelamento validados sem travar a UI.
- [ ] `pytest`, `compileall`, ruff e auditorias de dependência aprovados.
- [ ] Build e testes do frontend aprovados em CI.
- [ ] ZIP/release validado sem segredos, dados pessoais ou artefatos reais.
- [ ] Git status/diff limpos e release identificável por commit/tag.
- [ ] Checklist manual executado em dry-run.
- [ ] Produção assistida com um protocolo realizada somente após autorização explícita.

## 11. Evidências desta auditoria

- Baseline: `python -m pytest -q` -> `352 passed`.
- `apps/desktop/frontend/dist`: ausente antes da documentação desta auditoria.
- `frontend/dist/index.html`: presente; última modificação 2026-07-16 09:20:55.
- `node --version`: comando indisponível.
- `npm --version` e `npm run build`: comandos indisponíveis.
- `pnpm --version`: `11.9.0`.
- Descoberta setuptools configurada: `apps included? False`.
- Git: `fatal: not a git repository`.

Essas evidências descrevem prontidão técnica/offline; não constituem homologação de produção.
