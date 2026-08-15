# SPEC-012 - Refatoracao estrutural segura e desktop instalavel local

Status: em implementacao incremental - Ciclo 7 Processamento PDF e Excel concluido
Data: 2026-08-15
Responsavel: Codex
Revisores: Operacao GD Neoenergia e revisao tecnica independente
ADRs relacionadas: [ADR 0001](../docs/adr/0001-strangler-migration.md), [ADR 0003](../docs/adr/0003-codex-engineering-governance.md), [ADR 0005](../docs/adr/0005-canonical-desktop-path.md)
Issues relacionadas: Preparacao para refatoracao completa e distribuicao desktop local

## Contexto

O projeto automatiza o Portal GD Neoenergia por CLI e desktop, com fluxo operacional critico
envolvendo CDP, download de orcamentos, extracao de PDF, atualizacao de Excel, arquivamento em
pastas de clientes, lock global, backups e relatorios. O CLI e o desktop canonicamente apontam
para o mesmo backend de aplicacao, mas ainda existem adaptadores grandes, codigo legado preservado
e artefatos locais ignorados pelo Git.

A arquitetura aceita usa migracao strangler: preservar fachadas compativeis, extrair componentes
em etapas pequenas e manter testes de contrato antes de qualquer remocao. A ADR 0005 define
`apps/desktop` como caminho canonico do desktop e preserva `frontend/` e
`automacao_gd/presentation/desktop` como legado congelado ate haver paridade comprovada.

## Problema

Uma refatoracao ampla sem contrato versionado pode quebrar o fluxo atual, principalmente a opcao 5
do OP5/CDP, a escrita da planilha e o arquivamento. Os arquivos mais sensiveis concentram muitas
responsabilidades:

- `automacao_gd/infrastructure/portal/cdp_service.py`;
- `automacao_gd/application/full_pipeline.py`;
- `automacao_gd/application/processing_service.py`;
- `automacao_gd/infrastructure/excel/service.py`.

Ao mesmo tempo, o desktop ja possui base PySide6/React e build PyInstaller, mas ainda precisa de
um contrato de instalacao local: icone customizavel, atalho local, validacao offline e separacao
entre instalacao de usuario e rotinas de desenvolvimento/teste.

## Evidencias do comportamento atual

- `app.py` e `python -m automacao_gd` delegam para `automacao_gd.presentation.cli`.
- `desktop_app.py` delega para `apps.desktop.main`.
- `pyproject.toml` declara os scripts `gd-neoenergia` e `gd-neoenergia-desktop`.
- `pyproject.toml` inclui `apps*` e package data de `apps.desktop`.
- `.github/workflows/ci.yml` cobre Python, frontend canonico, wheel, release zip, privacy scan e
  auditoria de dependencias.
- `scripts/build_windows.ps1` gera build PyInstaller incluindo `apps/desktop/frontend/dist` e
  `apps/desktop/frontend/static`.
- `scripts/build_windows.ps1` ainda nao define `--icon`.
- Nao ha script versionado especifico para criar atalho no Desktop/Menu Iniciar.
- `scripts/setup_windows.ps1` instala dependencias e executa `python -m pytest -q`, comportamento
  adequado para desenvolvimento, mas inadequado como instalador local de usuario final.
- `src/`, `frontend/` e `automacao_gd/presentation/desktop` ainda sao preservados por ADRs e
  testes; nao sao candidatos de remocao imediata.
- `git status --short --branch` indicou worktree rastreada limpa durante a auditoria read-only
  anterior a esta SPEC.

## Objetivo

Definir uma refatoracao estrutural segura, incremental e verificavel para melhorar organizacao,
SOLID, Clean Code e Clean Architecture sem alterar o comportamento atual do OP5, CDP, Excel,
PDF, arquivamento, CLI e desktop. Definir tambem a preparacao para um desktop instalavel local,
com icone customizavel e validacao offline, sem abrir Portal nem executar producao. Separar
explicitamente limpeza local ignorada de refatoracao de codigo, para que essas frentes nunca sejam
executadas, revisadas ou revertidas como se fossem a mesma mudanca.

## Controle de execucao da SPEC-012

Este controle deve ser atualizado ao concluir cada secao operacional. A cada fechamento de ciclo,
a SPEC-012 deve ser retornada atualizada, indicando o que ficou concluido, o que segue pendente e
qual e a proxima etapa de desenvolvimento.

### Ciclos de desenvolvimento

- [x] Ciclo 0 - Auditoria tecnica e SPEC versionada.
  Evidencia: SPEC-012 criada como fonte de verdade para refatoracao segura e desktop instalavel
  local.
- [x] Ciclo 1 - Fechar CDP Navegacao.
  Evidencia: extracoes incrementais de navegacao/paginacao em `cdp_navigation.py`, fachada publica
  de `cdp_service.py` preservada e testes proporcionais confirmados pelo usuario e pelo Codex.
- [x] Ciclo 2 - CDP recuperacao de listagem.
  Evidencia: recuperacao de `Minhas Solicitacoes`, retorno apos detalhe e fallbacks de contexto
  extraidos para `cdp_navigation.py`, com fachada publica de `cdp_service.py` preservada.
- [x] Ciclo 3 - CDP detalhe do protocolo.
  Evidencia: clique de acompanhamento, espera de detalhe, leitura de cabecalho, texto do detalhe,
  status concluido e extracao da conclusao do ponto de conexao movidos para `cdp_detail.py`, com
  fachada publica de `cdp_service.py` preservada.
- [x] Ciclo 4 - CDP download/orcamento indisponivel.
  Evidencia: localizacao do orcamento, tentativa de download, timeout curto, descricao de PDF
  indisponivel, paths versionados, PDFs existentes e metadata de download movidos para
  `cdp_downloads.py`, com fachada publica de `cdp_service.py` preservada.
- [x] Ciclo 5 - CDP montagem de resumo e reducao final de `cdp_service.py`.
  Evidencia: resumo inicial, resultado por protocolo, classificadores de processamento e totais
  de download movidos para `cdp_summary.py`, com fachada publica de `cdp_service.py` preservada.
- [x] Ciclo 6 - Pipeline OP5/full_pipeline.py.
  Evidencia: contratos/autorizacao OP5 movidos para `op5_contracts.py`, selecao/congelamento
  de lote movidos para `op5_selection.py` e montagem/cobertura de plano OP5 movidas para
  `op5_plan.py`, mantendo `full_pipeline.py` como fachada publica.
- [x] Ciclo 7 - Processamento PDF e Excel sem mudanca de escrita.
  Evidencia: helpers puros de resultado, estado e metricas do processamento movidos para
  `processing_results.py`; helpers puros de payload e comparacao Excel movidos para
  `excel_update_helpers.py`; `process_downloaded_pdfs` e `update_excel_from_pdf_data`
  preservados como fachadas sem mudanca de escrita.
- [ ] Ciclo 8 - Desktop instalavel local com icone e atalho.
  Proxima etapa: preparar build/instalacao local a partir de `apps/desktop`, icone `.ico`
  validado e criacao opcional de atalho, sem abrir Portal/CDP nem tocar dados reais.
- [ ] Ciclo 9 - Validacao final, CI e preparo de release local.

### Trilhas

- [ ] Trilha A - Limpeza local ignorada.
  Status: pendente; nao foi misturada com as refatoracoes de codigo ja commitadas.
- [x] Trilha B - Refatoracao de codigo rastreado.
  Status: iniciada e com Ciclos 1 a 7 concluidos; deve continuar em commits pequenos e separados.

## Escopo

- Criar inventario verificavel de codigo ativo, legado preservado, artefatos gerados e candidatos
  a limpeza local.
- Definir fases de extracao dos adaptadores grandes com fachadas compativeis.
- Definir contratos de arquitetura para manter dominio isolado e efeitos colaterais explicitos.
- Preparar empacotamento desktop local Windows a partir de `apps/desktop`.
- Definir suporte a icone local customizavel para o executavel/atalho.
- Definir instalacao local sem dados reais, sem `.env` real e sem perfil CDP real no artefato.
- Definir testes de caracterizacao antes de cada refatoracao.
- Separar a trilha de limpeza local ignorada da trilha de refatoracao de codigo rastreado.

## Fora do escopo

- Alterar fluxo OP5, paginacao, skip, selecao de lote, confirmacao forte ou plano congelado.
- Alterar parser PDF, regras de equipamentos, escrita Excel ou arquivamento como parte desta SPEC.
- Rodar Portal/CDP, abrir Edge, acessar planilha real ou executar producao.
- Remover `src/`, `frontend/` ou `automacao_gd/presentation/desktop` nesta etapa.
- Publicar release externo, tag, instalador assinado ou distribuicao para maquinas de terceiros.
- Alterar limite operacional da opcao 5.
- Apagar `data/`, downloads, logs, state, cache ou backups operacionais.

## Separacao obrigatoria entre trilhas

A preparacao da refatoracao deve ser conduzida em duas trilhas independentes. Elas podem aparecer
no mesmo planejamento, mas nao podem ser executadas no mesmo commit operacional.

### Trilha A - Limpeza local ignorada

Objetivo: remover apenas sujeira local regeneravel ou orientar sua remocao, sem alterar codigo,
testes, planilha, Portal, CDP, configuracao real ou comportamento observavel.

Pode incluir:

- `__pycache__/`;
- `.pytest_cache/`;
- `.mypy_cache/`;
- `.ruff_cache/`;
- `build/` e artefatos temporarios de empacotamento;
- `*.egg-info/`;
- arquivos temporarios explicitamente classificados como ignorados e regeneraveis.

Nao pode incluir:

- arquivos rastreados pelo Git;
- `data/`, logs operacionais, PDFs, planilhas, backups, state/cache operacional ou `lixeira/`;
- `.env`, credenciais, perfis de navegador ou storage state;
- `node_modules/`, salvo decisao manual explicita fora desta SPEC;
- qualquer mudanca em `automacao_gd/`, `apps/`, `src/`, `scripts/`, `tests/`, `specs/` ou `docs/`
  que altere comportamento.

Evidencia minima: lista de caminhos candidatos, prova de que estao ignorados ou sao
regeneraveis, e confirmacao de que nenhum arquivo rastreado sera removido.

### Trilha B - Refatoracao de codigo

Objetivo: reorganizar codigo rastreado com comportamento externo preservado.

Pode incluir:

- extracao de modulos pequenos;
- criacao de adaptadores internos;
- testes de caracterizacao antes da mudanca;
- atualizacao de imports internos mantendo fachadas publicas;
- documentacao tecnica diretamente ligada a contratos de arquitetura.

Nao pode incluir:

- limpeza de caches/builds no mesmo commit;
- remocao de arquivos rastreados por aparente ociosidade sem inventario e ADR quando aplicavel;
- mudanca funcional em OP5/CDP, Excel, arquivamento, CLI ou desktop;
- alteracao de limites, confirmacoes, locks, relatorios ou status operacionais;
- acesso ao Portal/CDP ou planilha real como parte da refatoracao.

Evidencia minima: teste de caracterizacao RED quando houver comportamento coberto por TDD,
GREEN direcionado, diff pequeno, fachada publica preservada e registro de riscos residuais.

## Glossario

- **Refatoracao segura:** mudanca interna sem alteracao observavel de comportamento.
- **Fachada compativel:** API ou entrypoint antigo mantido enquanto a implementacao interna muda.
- **Legado congelado:** codigo preservado por compatibilidade, sem receber novas features.
- **Artefato ignorado:** arquivo local coberto por `.gitignore`, como cache, build, dist ou
  ambiente virtual.
- **Instalacao local:** preparacao para uso em uma maquina local autorizada, sem publicacao externa.
- **Icone customizavel:** arquivo `.ico` local, versionado ou configuravel, usado pelo executavel
  ou atalho.

## Requisitos funcionais

### RF-001 - Inventario antes de remocao

Antes de qualquer remocao ou movimentacao, o projeto deve gerar ou manter um inventario com:

- arquivos rastreados por camada;
- artefatos ignorados locais;
- arquivos referenciados por imports, scripts, testes, docs, CI e package data;
- classificacao `manter`, `legado_preservado`, `gerado_regeneravel`, `limpeza_local_candidata` ou
  `remocao_exige_adr`.

Nenhum arquivo rastreado pode ser removido apenas por parecer nao utilizado.

### RF-002 - Codigo legado protegido

`src/`, `frontend/` e `automacao_gd/presentation/desktop` devem permanecer no repositorio enquanto
ADR especifica de remocao nao for aceita. Qualquer alteracao nesses caminhos deve ser justificada
por compatibilidade, seguranca ou teste existente.

### RF-003 - Contrato de fluxo OP5 inalterado

Durante esta refatoracao, a opcao 5 deve preservar:

- menu e comandos existentes;
- limite e confirmacao forte;
- plano OP5 congelado;
- dry-run antes de aplicacao real;
- validacao de SHA da planilha e backup;
- comportamento de `batch_fast`, `inline_global` e `audit_global`;
- relatorios operacionais existentes;
- codigos de status `SUCESSO`, `PARCIAL`, `BLOQUEADO` e `FALHOU`.

Mudancas que alterem qualquer item acima devem sair desta SPEC e exigir SPEC propria.

### RF-004 - Extracao incremental do CDP

`cdp_service.py` deve ser refatorado por fatias pequenas, mantendo uma fachada publica compativel.
A ordem preferida e o estado atual sao:

1. [x] leitura da listagem;
2. [x] navegacao/paginacao;
3. [x] recuperacao de listagem;
4. [x] detalhe do protocolo;
5. [x] download/orcamento indisponivel;
6. [x] montagem de resumo.

Cada extracao deve ter teste de caracterizacao antes da mudanca.

A primeira refatoracao de codigo desta SPEC deve comecar por `cdp_service.py` e nao pode ser
misturada com limpeza local ignorada. A fachada publica existente deve continuar sendo o ponto de
entrada para OP5, CLI, testes e demais consumidores.

### RF-005 - Extracao incremental do pipeline

`full_pipeline.py` deve ser dividido apenas depois de congelar testes de contrato para:

- [x] validacao operacional;
- [x] montagem do plano;
- [x] selecao/congelamento do lote;
- [ ] execucao de download;
- [ ] processamento;
- [x] cobertura OP5;
- [ ] relatorios;
- [x] tratamento de erro de autorizacao, confirmacao forte e plano congelado.

Os itens ainda pendentes deste requisito nao bloqueiam o fechamento do Ciclo 6 porque dependem
de wrappers de compatibilidade para preservar monkeypatches e testes historicos; devem ser tratados
somente em ciclos proprios, sem misturar com processamento PDF/Excel.

O entrypoint `run_full_cdp_pipeline(settings, confirmation=...)` deve permanecer compativel.

### RF-006 - Extracao incremental do processamento e Excel

`processing_service.py` e `excel/service.py` devem ser refatorados sem alterar regra de escrita.
As extracoes candidatas sao:

- [x] helpers puros de resultado, estado e metricas do processamento;
- [x] helpers puros de payload e comparacao Excel;
- [ ] localizacao de linha/protocolo;
- [ ] writer de linha;
- [ ] formatacao visual da linha;
- [ ] backup e escrita atomica;
- [ ] planejamento de atualizacao;
- [ ] arquivamento;
- [ ] persistencia de state.

Qualquer mudanca de conteudo escrito na planilha fica fora desta SPEC.
As funcoes de escrita, salvamento, backup, arquivamento e persistencia operacional nao foram
extraidas no Ciclo 7; mover essas partes exigira caracterizacao propria para evitar mudanca de
efeito real.

### RF-007 - Fronteiras de Clean Architecture

A refatoracao deve preservar ou melhorar as seguintes fronteiras:

- `domain` nao importa infraestrutura, apresentacao, Playwright, openpyxl, PySide6 ou filesystem.
- `presentation` nao acessa Playwright, openpyxl ou regras de negocio diretamente.
- `apps/desktop` chama contratos/casos de uso, nao adaptadores CDP/Excel diretamente.
- side effects de Portal, Excel, filesystem, logs e subprocessos ficam explicitos.

### RF-008 - Desktop canonico instalavel local

O desktop instalavel deve usar o caminho canonico:

```text
desktop_app.py
  -> apps.desktop.main
  -> apps.desktop.window
  -> apps.desktop.bridge
  -> automacao_gd.application
  -> apps/desktop/frontend/dist/index.html
```

O fallback estatico pode existir apenas como recuperacao local explicita. O frontend legado raiz
`frontend/` nao pode ser empacotado como frontend canonico.

### RF-009 - Icone customizavel

O build local deve aceitar um icone `.ico` validado por caminho local seguro. O comportamento
esperado:

- usar um icone padrao versionado quando existir;
- aceitar override local por parametro de build ou configuracao documentada;
- falhar com mensagem clara quando o icone informado nao existir ou nao for `.ico`;
- nao baixar icone da internet;
- nao embutir dados operacionais no recurso.

### RF-010 - Atalho local

A instalacao local deve oferecer criacao opcional de atalho no Desktop, apontando para o
executavel gerado ou para wrapper local aprovado. O atalho deve:

- usar working directory correto;
- usar icone configurado;
- nao incluir segredos, tokens, `.env` real ou argumentos sensiveis;
- ser recriavel sem duplicar atalhos conflitantes.

### RF-011 - Separacao entre setup de desenvolvimento e instalacao local

O setup de desenvolvimento pode instalar dependencias e rodar testes. A instalacao local de usuario
deve ser separada e nao deve executar automaticamente `pytest`, abrir Portal, alterar planilha ou
pedir credenciais.

### RF-012 - Limpeza local segura

Limpeza de caches e builds deve operar somente sobre artefatos ignorados e regeneraveis:

- `__pycache__/`;
- `.pytest_cache/`;
- `.mypy_cache/`;
- `.ruff_cache/`;
- `build/`;
- `*.egg-info/`;
- artefatos temporarios explicitamente classificados.

`data/`, `.env`, PDFs, planilhas, state/cache operacional, `node_modules` e `lixeira/` nao podem
ser apagados automaticamente por esta SPEC.

### RF-013 - Commits e revisao separados por natureza da mudanca

Mudancas de limpeza local ignorada e mudancas de refatoracao de codigo devem ser planejadas,
executadas, revisadas e revertidas separadamente.

- Uma limpeza local nao deve alterar arquivos rastreados, exceto documentacao ou scripts criados
  especificamente para listar/validar candidatos.
- Uma refatoracao de codigo nao deve apagar caches, builds, logs, downloads, state ou outros
  artefatos locais.
- O primeiro commit de refatoracao de codigo deve focar em `cdp_service.py` ou em testes de
  caracterizacao diretamente necessarios para essa extracao.
- Qualquer remocao de arquivo rastreado exige inventario previo; quando envolver legado protegido
  ou decisao dificil de reverter, exige ADR antes da remocao.

## Requisitos nao funcionais

- Compatibilidade Windows como ambiente principal.
- CI Linux/Windows deve continuar verde.
- Refatoracoes devem ser pequenas, reversiveis e revisaveis por commit.
- Nenhum teste pode depender de Portal real, CDP, planilha oficial, unidade de rede ou dados reais.
- Operacoes criticas continuam idempotentes e com gravacao atomica quando aplicavel.
- Logs e relatorios nao devem expor segredos, cookies, tokens ou dados pessoais desnecessarios.

## Contratos e interfaces

Entrypoints preservados:

```text
python app.py
python -m automacao_gd
python desktop_app.py
python -m apps.desktop.main
gd-neoenergia
gd-neoenergia-desktop
```

Scripts candidatos a evolucao:

```text
scripts/build_windows.ps1
scripts/smoke_desktop_executable.py
scripts/create_clean_release_zip.py
scripts/validate_release_zip.py
scripts/validate_engineering_foundation.py
```

Novos scripts permitidos por implementacao futura:

```text
scripts/install_local_desktop.ps1
scripts/create_desktop_shortcut.ps1
```

## Dados e persistencia

Esta SPEC nao autoriza nova persistencia operacional obrigatoria. Artefatos futuros de instalacao
local devem ficar fora de `data/` e nao podem conter dados reais. Relatorios de auditoria podem
ficar em `data/logs` quando forem privados operacionais, mas nao devem entrar em release.

## Estados e tratamento de erros

Novos codigos futuros recomendados:

- `REFACTOR_INVENTORY_BLOCKED`;
- `LEGACY_REMOVAL_REQUIRES_ADR`;
- `DESKTOP_ICON_INVALID`;
- `DESKTOP_FRONTEND_BUILD_MISSING`;
- `DESKTOP_SHORTCUT_CREATION_FAILED`;
- `LOCAL_INSTALL_VALIDATION_FAILED`.

Erros devem falhar fechado sem executar OP5, sem abrir CDP e sem tocar na planilha.

## Seguranca e privacidade

- `.env` real, perfis de navegador, `storage_state.json`, logs reais, PDFs reais e planilhas reais
  nao podem entrar no instalador.
- O build desktop nao deve baixar recursos externos durante execucao.
- O QWebChannel continua restrito a frontend local autorizado.
- O instalador local nao deve abrir Portal nem capturar credenciais.
- O scanner de privacidade deve continuar gate obrigatorio para release e artefatos.

## UX e acessibilidade

O desktop instalavel deve preservar a interface canonica atual. Alteracoes visuais ficam fora desta
SPEC, exceto quando necessarias para icone, nome da aplicacao, estado de instalacao incompleta ou
mensagem de frontend ausente.

O usuario deve conseguir identificar:

- nome da aplicacao;
- modo simulacao/producao;
- se o frontend canonico foi carregado;
- se a instalacao esta incompleta;
- caminho seguro para abrir o app pelo atalho local.

## Observabilidade

Cada fase futura deve registrar evidencias:

- arquivos alterados;
- contratos preservados;
- testes direcionados executados;
- riscos residuais;
- status do build desktop;
- status do smoke offline;
- diferencas entre codigo ativo, legado e artefatos ignorados.

## Compatibilidade

A compatibilidade com CLI tem prioridade sobre a organizacao interna. `src.*` continua como camada
de compatibilidade ate uma versao principal futura e ADR propria. A remocao de legado desktop so
pode ocorrer depois de paridade funcional, teste de pacote instalado e rollback comprovado.

## Migracao ou backfill

Nao ha migracao de dados nem backfill de planilha nesta SPEC. A unica migracao permitida e interna
ao codigo, por extracao de modulos com fachadas compativeis.

## Estrategia de testes

Como esta SPEC e documento, nao ha RED/GREEN nesta criacao. Na implementacao futura:

- escrever teste de caracterizacao antes de cada extracao;
- rodar teste direcionado da area alterada;
- rodar `python -m ruff check automacao_gd apps src scripts tests`;
- rodar `python -m mypy automacao_gd` quando a mudanca tocar tipos ou contratos;
- rodar `python scripts/validate_engineering_foundation.py`;
- rodar frontend com `pnpm install --frozen-lockfile`, `pnpm test` e `pnpm build` quando tocar
  `apps/desktop/frontend`;
- rodar smoke offline do desktop quando tocar build, package data, icone ou shortcut;
- rodar suite proporcional antes de commit e CI completo antes de liberar.

Testes futuros minimos:

- `domain` nao importa infraestrutura/apresentacao.
- `apps/desktop` nao importa Playwright/openpyxl/adaptadores CDP ou Excel diretamente.
- entrypoints continuam importaveis.
- `run_full_cdp_pipeline` preserva assinatura publica.
- build PyInstaller inclui frontend canonico.
- build com icone invalido falha fechado.
- atalho local usa working directory e icone corretos.
- release zip nao inclui `.env`, `data/`, planilhas, PDFs, perfis, `node_modules` ou frontend
  legado empacotado.

## Criterios de aceite

- [ ] Dado o inventario da refatoracao, quando classificar arquivos rastreados, entao nenhum
      arquivo protegido por ADR aparece como removivel imediato.
- [ ] Dado o fluxo OP5 atual, quando uma refatoracao estrutural for aplicada, entao os contratos
      de lote, plano, confirmacao, backup, relatorios e status permanecem iguais.
- [ ] Dado `python app.py`, quando o menu abrir, entao as opcoes existentes permanecem
      disponiveis.
- [ ] Dado `desktop_app.py`, quando importado ou executado, entao continua delegando para
      `apps.desktop.main`.
- [ ] Dado `apps/desktop`, quando analisado estaticamente, entao nao importa Playwright nem
      openpyxl diretamente.
- [ ] Dado um build desktop sem `dist/index.html`, quando executado em modo release, entao falha
      com mensagem clara de instalacao incompleta.
- [ ] Dado um icone `.ico` valido, quando o build local for executado, entao o executavel/atalho
      usa esse icone.
- [ ] Dado um icone inexistente ou nao `.ico`, quando o build local for executado, entao a
      instalacao falha antes de gerar artefato enganoso.
- [ ] Dado o instalador local, quando executado, entao nao roda `pytest`, nao abre Portal, nao
      altera planilha e nao acessa dados reais automaticamente.
- [ ] Dado o release zip, quando validado, entao nao contem segredos, dados operacionais, planilhas,
      PDFs, `node_modules`, `.venv` ou frontend legado como frontend canonico.
- [ ] Dado um lote de limpeza local ignorada, quando revisado, entao o diff de arquivos rastreados
      nao contem refatoracao de codigo nem alteracao funcional.
- [x] Dado um lote de refatoracao de codigo, quando revisado, entao nao contem remocao de caches,
      builds, logs, downloads, state, dados operacionais ou outros artefatos locais.
- [x] Dado o primeiro lote de refatoracao, quando implementado, entao `cdp_service.py` permanece
      como fachada publica compativel e OP5 continua consumindo o mesmo contrato externo.

## Rollout

1. Aprovar esta SPEC.
2. Criar ADR complementar somente se houver remocao de legado ou mudanca dificil de reverter.
3. Classificar candidatos em duas trilhas: limpeza local ignorada ou refatoracao de codigo.
4. Executar limpeza local ignorada apenas em etapa propria, sem refatoracao e sem arquivos
   rastreados, salvo documentacao ou validador especifico.
5. Implementar inventario/validadores primeiro, sem mexer no OP5.
6. Refatorar primeiro `cdp_service.py`, uma fatia por vez, com fachada publica preservada e testes
   de caracterizacao.
7. Refatorar outras frentes apenas depois de estabilizar o CDP.
8. Preparar build desktop com icone e atalho em etapa separada.
9. Validar localmente, commit pequeno, push e CI.
10. Liberar apenas depois de CI verde e smoke offline.

## Rollback

Cada etapa deve ser revertivel por commit. Se uma extracao quebrar contrato, reverter somente a
extracao afetada e manter a fachada anterior. Se o build desktop falhar, preservar CLI e artefatos
anteriores; nao apontar silenciosamente para o frontend legado.

## Riscos

- Refatorar arquivos grandes pode alterar comportamento por acidente.
- Remover legado cedo demais pode quebrar testes, scripts ou consumidores externos.
- Misturar instalador desktop com setup de desenvolvimento pode criar dependencia indevida de
  pytest, Node ou ambiente do desenvolvedor.
- Icone/atalho podem introduzir caminhos absolutos locais no artefato.
- Limpeza automatica ampla pode apagar dados operacionais.

## Decisoes pendentes

- Nome final do executavel e do atalho.
- Caminho padrao do icone versionado.
- Se o instalador local sera apenas PowerShell ou tambem um instalador Windows formal.
- Quando propor ADR para remocao de `frontend/` e `automacao_gd/presentation/desktop`.
- Politica de retencao de `lixeira/` e artefatos ignorados antigos.

## Evidencias de homologacao

- [x] SPEC-012 criada e versionada para planejamento incremental.
- [x] Primeira fatia de `cdp_service.py` executada sem limpeza local ignorada misturada:
  `cdp_errors.py`, `cdp_extractors.py`, teste de fachada e SPEC-012.
- [x] Ciclo 1 - CDP Navegacao concluido em commits pequenos:
  `4a58939`, `fcf6e65`, `a1d8a02`, `42b7748`, `62a4937`, `6549d6f`, `a2a09b1`.
- [x] Validacao proporcional confirmada pelo usuario em 2026-08-15:
  `python -m pytest tests/test_cdp_service_facade_contract.py tests/test_cdp_portal_navigation.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py -q`
  com 194 testes passando antes do fechamento definitivo do Ciclo 1.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 1:
  mesma suite direcionada com 197 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 1 sem erros.
- [x] `git diff --check` sem falha; apenas avisos de CRLF existentes no ambiente Windows.
- [x] Ciclo 2 - CDP recuperacao de listagem concluido em `cdp_navigation.py`, mantendo
  `cdp_service.py` como fachada para `return_to_listing`, `ensure_listing_page`,
  `ensure_minhas_solicitacoes`, `_return_to_listing_after_detail`,
  `_recover_listing_in_new_context_page`, `_recover_listing_by_detail_return_control`,
  `_recover_listing_by_authenticated_home_icon` e `_recover_minhas_solicitacoes`.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 2:
  `python -m pytest tests/test_cdp_service_facade_contract.py tests/test_cdp_portal_navigation.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py -q`
  com 207 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 2 sem erros.
- [x] Ciclo 3 - CDP detalhe do protocolo concluido em `cdp_detail.py`, mantendo `cdp_service.py`
  como fachada para `click_follow_eye_button`, `wait_detail_loaded`,
  `extract_detail_header`, `extract_completion_date`,
  `extract_point_of_connection_completion` e `detail_has_completed_status`.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 3:
  `python -m pytest tests/test_cdp_detail.py tests/test_cdp_service_facade_contract.py tests/test_cdp_portal_navigation.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py -q`
  com 214 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 3 sem erros.
- [x] Ciclo 4 - CDP download/orcamento indisponivel concluido em `cdp_downloads.py`, mantendo
  `cdp_service.py` como fachada para `find_connection_budget_target`,
  `download_connection_budget`, `find_existing_connection_budget_pdfs`,
  `find_existing_connection_budget_pdf`, `find_existing_download_metadata`,
  `should_open_detail_for_budget`, `_describe_no_download` e `_next_budget_path`.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 4:
  `python -m pytest tests/test_cdp_downloads.py tests/test_cdp_detail.py tests/test_cdp_service_facade_contract.py tests/test_cdp_portal_navigation.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py -q`
  com 221 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 4 sem erros.
- [x] Ciclo 5 - CDP montagem de resumo concluido em `cdp_summary.py`, mantendo `cdp_service.py`
  como fachada para `_initial_download_summary`, `_download_result_from_record`,
  `_result_has_valid_pdf_for_processing`, `_result_is_metadata_only_for_processing` e
  `_refresh_download_totals`.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 5:
  `python -m pytest tests/test_cdp_summary.py tests/test_cdp_downloads.py tests/test_cdp_detail.py tests/test_cdp_service_facade_contract.py tests/test_cdp_portal_navigation.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py -q`
  com 225 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 5 sem erros.
- [x] Ciclo 6 - Pipeline OP5/full_pipeline.py concluido com extracao de:
  `op5_contracts.py` para contratos/autorizacao/confirmacao forte, `op5_selection.py` para
  limite global, paths de processamento e escopo congelado de PDF/metadados, e `op5_plan.py`
  para montagem/cobertura de plano OP5.
- [x] `full_pipeline.py` preservado como fachada para `run_full_cdp_pipeline`,
  `run_op5_archive_plan`, contratos OP5 e helpers historicos usados por CLI/testes.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 6:
  `python -m pytest tests/test_full_pipeline_facade_contract.py tests/test_option5_batch_authorization.py tests/test_stage0_production_safety.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py tests/test_operational_output.py tests/test_progress_and_errors.py -q`
  com 270 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 6 sem erros.
- [x] Ciclo 7 - Processamento PDF e Excel sem mudanca de escrita concluido com extracao de:
  `processing_results.py` para helpers puros de resultado/estado/metrica do processamento e
  `excel_update_helpers.py` para helpers puros de payload/comparacao Excel.
- [x] `processing_service.py` preservado como fachada para `process_downloaded_pdfs` e helpers
  historicos; `excel/service.py` preservado como fachada para `update_excel_from_pdf_data` e
  helpers historicos usados por testes/consumidores.
- [x] Validacao de caracterizacao do Ciclo 7 executada pelo Codex:
  `python -m pytest tests/test_processing_excel_facade_contract.py -q` com 3 testes passando.
- [x] Validacao proporcional executada pelo Codex no fechamento do Ciclo 7:
  `python -m pytest tests/test_processing_excel_facade_contract.py tests/test_processing_service.py tests/test_processing_resilience.py tests/test_stage1_review_regressions.py tests/test_stage1_terminal_persistence.py tests/test_terminal_operation_safety.py tests/test_equipment_format_v2.py tests/test_full_cdp_pipeline.py tests/test_op5_stage_b_optimization.py tests/test_stage0_production_safety.py -q`
  com 371 testes passando.
- [x] `ruff check` nos arquivos tocados do Ciclo 7 sem erros.
- [ ] Suite completa do projeto apos concluir todos os ciclos.
- [ ] CI verde apos push final das etapas aplicaveis.
- [ ] Smoke offline do desktop instalavel.
- [ ] Homologacao operacional com Portal/CDP, planilha e arquivamento quando autorizada.
