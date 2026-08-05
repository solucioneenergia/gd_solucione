# Spec — desktop_production_readiness

- Status: **em homologação final controlada da candidata 2.0.2**
- Tipo: especificação normativa de hardening e prontidão offline; sem autorização de produção
- Data da auditoria: 2026-07-19
- Decisão arquitetural relacionada: `docs/adr/0005-canonical-desktop-path.md` (aceita)

## 0. Atualização normativa da candidata 2.0.2

Em 2026-08-03 foi aprovado `apps/desktop` como caminho canônico e
`apps/desktop/frontend` como frontend canônico. Esta implementação deve preservar
`automacao_gd/presentation/desktop` e `frontend` como legado congelado, sem removê-los.

O escopo da candidata 2.0.2 cobre empacotamento, build frontend reproduzível, empty state,
hardening de origem e navegação, proteção da bridge, desativação honesta de placeholders,
cancelamento cooperativo, confirmação vinculada ao lote, CI e validação de release.

Ficam fora do escopo: produção, canário, publicação, tags, remoção do legado, regras de parser,
PDF, Excel, arquivamento, limites operacionais e integração real com o Portal.

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

## 12. Execução controlada da candidata 2.0.2 (2026-08-03)

A implementação definida nesta SPEC foi executada no repositório Git real e está detalhada
em `docs/releases/release_2.0.2_audit_ledger.md`. Wheel, instalação isolada, frontend,
testes, auditoria de dependências, bundle estrutural e ZIP diagnóstico foram aprovados.

A Definition of Done de produção continua aberta: MyPy global reprova com dívida legada,
o secret scan e o CI remoto não foram executados, o EXE não recebeu smoke com configuração
sintética e a worktree contém mudanças preexistentes. Nenhuma tag foi criada e nenhuma
execução de produção foi autorizada.

## 13. Fechamento dos gates locais pré-promoção (2026-08-04)

### 13.1 Escopo

Esta etapa fecha somente os gates locais remanescentes da candidata 2.0.2: MyPy, sanitização
do frontend legado, Gitleaks, cenário de symlink, smoke offline do bundle, reprodução dos
comandos do CI e reconstrução em staging persistente. Produção, Portal, planilha oficial,
histórico Git, commit, tag, remote e CI remoto permanecem fora do escopo.

### 13.2 Baseline comprovada

- HEAD: `070a4206b017b51f142919e6e98a0b290e0c8953`, tag `v2.0.1`, sem `v2.0.2` e sem remote.
- Pytest: 841 aprovados e um skip por privilégio de symlink no Windows.
- MyPy do CI: 33 erros em nove arquivos.
- Frontend canônico: instalação imutável, três testes e build aprovados.
- Frontend legado: três identificadores longos ambíguos no fallback `sampleProtocols`.
- Gitleaks: action `v2` sem versão binária fixada; a execução local usará 8.30.1.
- Árvore destacada equivalente ao workspace por camadas, sem copiar `outputs/`.

### 13.3 Requisitos verificáveis

1. MyPy deve terminar com código zero sem `ignore_errors`, `Any` indiscriminado ou mudança de
   regra de negócio.
2. O fallback legado deve iniciar vazio ou usar somente dados explicitamente sintéticos, e a
   busca automatizada por identificadores operacionais deve retornar zero.
3. Gitleaks 8.30.1 deve verificar o estado atual e o histórico, com relatórios redigidos que não
   reproduzam valores encontrados.
4. A proteção contra symlink externo deve ser executada por equivalente não privilegiado quando
   o Windows negar a criação de symlink real, sem reduzir a propriedade testada.
5. O smoke do EXE deve usar somente variáveis e diretórios sintéticos, confirmar janela,
   frontend canônico, bridge autorizada, empty state, ausência de operação automática e
   encerramento controlado.
6. Cada comando dos jobs locais do workflow deve ser executado; CI remoto não pode ser inferido.
7. Wheel, ZIP e bundle devem ser reconstruídos na árvore isolada e copiados para
   `artifacts/release-candidate/2.0.2/`, com manifesto, SHA-256 e logs sanitizados.

### 13.4 Mapeamento de testes

- Tipagem: MyPy por arquivo durante GREEN e comando integral no gate final.
- Legado: teste que reprova identificadores longos e confirma exclusão de wheel/ZIP/bundle.
- Symlink: teste real quando suportado e teste equivalente por adaptador de filesystem controlado.
- Smoke: harness dedicado com configuração sintética e relatório estruturado.
- Artefatos: instalação isolada do wheel, validador do ZIP e inspeção do bundle.
- Segurança: Gitleaks atual e histórico, sem allowlist ampla.

### 13.5 Critérios desta etapa

- [ ] MyPy integral em código zero.
- [ ] Literais ambíguos do legado sanitizados e regressão aprovada.
- [ ] Gitleaks atual e histórico aprovados ou achados tratados explicitamente.
- [ ] Cenário de escape por symlink efetivamente coberto.
- [ ] Smoke offline do EXE integralmente aprovado.
- [ ] Comandos locais do CI aprovados.
- [ ] Artefatos isolados reconstruídos e manifestados fora de `%TEMP%`.
- [ ] Nenhum dado operacional acessado, apagado ou distribuído.

### 13.6 Rollback

Preservar o snapshot criado antes desta etapa. O rollback deve restaurar somente os arquivos
listados no delta incremental desta etapa e nunca usar reset ou limpeza destrutiva. A árvore
isolada e o staging só podem ser removidos após conferência dos manifestos.

## 14. Fase 6 - Bootstrap manual do EXE

A abertura direta do bundle Windows deve configurar logging antes de carregar a janela mesmo
quando o executavel estiver em modo frozen/windowed e nao houver console associado. Nesse cenario,
`sys.stderr` pode ser `None`; portanto, o bootstrap nunca deve chamar `logger.add(None)`.

Quando nao houver diretorio de logs configurado no modo frozen/manual, o fallback deve ser um
diretorio local do usuario para a aplicacao, nao um caminho operacional do projeto. A correcao nao
autoriza Portal, planilha oficial, unidade de rede, perfil real de navegador, producao ou canario.

Criterios verificaveis:

- `setup_logger()` nao lanca excecao com `sys.stderr` e `sys.__stderr__` ausentes.
- `setup_logger()` cria `app.log` em fallback local seguro quando `logs_dir_path` nao existe.
- O EXE copiado abre com frontend, bridge e empty state, sem iniciar operacao e sem requisicao
  HTTP(S) externa.

## 15. Homologação final controlada da candidata 2.0.2 (2026-08-05)

### 15.1 Problema e evidências

O ZIP de staging aprovado pelo validador de release não é autocontido para executar o gate de
engineering foundation que ele próprio distribui. O gerador exclui `AGENTS.md` e `.agents/`,
enquanto `scripts/validate_engineering_foundation.py` exige esses arquivos. Executado a partir
do ZIP de staging, o validator reprova pelos dez arquivos de governança ausentes. O mesmo ZIP
tem 312 entradas no artefato disponível nesta etapa, e não as 314 registradas como evidência
preliminar.

O staging também contém o EXE anterior à correção da Fase 6. Seu SHA-256 é
`9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E`, enquanto o bundle
corrigido em `dist/AutomacaoGDNeoenergia` tem SHA-256
`91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467`.

### 15.2 Escopo e contrato

Esta homologação pode somente:

1. incluir no ZIP fonte os `AGENTS.md` e `.agents/skills/**` versionados e exigidos pela
   foundation;
2. exigir esses caminhos no release validator, sem relaxar denylist ou controles de dados;
3. reconstruir o ZIP seguro e seus relatórios;
4. sincronizar o bundle corrigido completo com o staging e recalcular manifestos e hashes;
5. executar gates locais e smoke offline exclusivamente com ambiente sintético.

Produção, canário, Portal, planilha oficial, unidade de rede, `.env` real, perfil real de
navegador, commit, tag, push, release e alteração de regra de negócio permanecem fora do escopo.
Não há nova decisão arquitetural: o desktop canônico continua definido pela ADR 0005 e o legado
permanece preservado e congelado.

### 15.3 Requisitos verificáveis

- O ZIP fonte deve ser aprovado por `validate_release_zip.py` e, após extração temporária, por
  `validate_engineering_foundation.py`.
- O release validator deve reprovar um pacote sem `AGENTS.md`, `frontend/AGENTS.md` ou qualquer
  arquivo versionado de `.agents/skills/**` exigido pela foundation.
- A inclusão da governança não pode permitir `.env`, credenciais, perfis, dados operacionais,
  documentos reais, artefatos de build ou arquivos temporários.
- O bundle de staging deve ser uma sincronização completa do bundle corrigido e seu EXE deve ter
  o mesmo SHA-256 da origem.
- Manifestos devem descrever o estado final do staging, e o smoke deve executar diretamente o
  EXE desse staging com diretórios e variáveis sintéticos.
- Contagens de entradas e hashes são evidências calculadas, não constantes contratuais.

### 15.4 Estratégia de testes e critérios de aceite

- [x] Dado um pacote sintético sem governança, quando validado, então ele é reprovado pelos
      caminhos de foundation ausentes.
- [x] Dado o gerador de release, quando avaliados `AGENTS.md` e `.agents/skills/**`, então esses
      caminhos são incluídos, mantendo as exclusões sensíveis existentes.
- [x] Dado o ZIP final extraído em diretório temporário, quando o validator de foundation roda,
      então termina com código zero.
- [x] Dado o bundle corrigido e o staging, quando a sincronização termina, então contagem,
      manifesto e hash do EXE correspondem.
- [x] Dado o EXE oficial do staging, quando o smoke sintético é executado, então abre o frontend
      canônico, inicializa a bridge, mantém empty state, não inicia operação nem requisição
      externa e encerra controladamente.

Resultado local desta homologação: **APTA PARA REVISÃO DE DIFF E COMMIT LOCAL**. CI remoto,
tag `v2.0.2`, canário, release e produção continuam pendentes e não autorizados.

### 15.5 Rollback e riscos

O rollback deve restaurar somente ZIP, bundle e relatórios substituídos nesta etapa a partir de
cópia temporária controlada, sem `git reset`, limpeza ampla ou remoção de dados do usuário. O
principal risco é promover manifestos que descrevam o bundle anterior; por isso hashes,
contagens, validator de release, foundation extraída e smoke do staging são gates obrigatórios.
