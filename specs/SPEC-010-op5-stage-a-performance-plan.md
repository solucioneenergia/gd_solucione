# SPEC-010 — OP5 Etapas A/B: plano congelado e otimização central auditável

Status: proposta para implementação
Data: 2026-08-10
Responsável: Codex
Revisores: Operação GD Neoenergia e revisão independente
ADRs relacionadas: [ADR 0002](../docs/adr/0002-atomic-local-persistence.md), [ADR 0006](../docs/adr/0006-traceable-partial-terminal-operation.md)
Issues relacionadas: Otimização segura da opção 5 após aceite da Etapa 1

## Contexto

A opção 5 já possui autorização forte, limite configurável até 60, lock global neutro,
lote congelado e reaplicação do dry-run na execução real. O canário ampliado validou que o
real pode reaplicar o lote congelado sem nova navegação CDP, mas o plano ainda é derivado do
relatório operacional `pipeline_cdp_completo.json`, que é sobrescrito pela execução seguinte.

Além disso, o fluxo de lote continua acoplado à reconciliação global Portal × planilha e à
leitura de muitas páginas antes de aplicar o limite operacional. Isso aumenta o tempo de
execução e a chance de divergência operacional para lotes limitados.

## Problema

Para lotes controlados, o operador precisa de um fluxo mais rápido sem perder segurança:

- o dry-run deve gerar um plano de aplicação explícito e estável;
- o real deve aplicar esse plano sem reler Portal/CDP;
- a seleção do lote deve parar quando atingir N elegíveis seguros;
- a reconciliação global deve ser uma auditoria separada, não um bloqueio obrigatório de todo
  lote rápido;
- reuso de PDFs e metadados deve ser validado por hash e versão.

## Evidências do comportamento atual

- O real já pula CDP quando encontra dry-run válido, mas lê o plano de
  `pipeline_cdp_completo.json`.
- O relatório `pipeline_cdp_completo.json` é um relatório operacional consolidado e pode ser
  sobrescrito pela execução real.
- A SPEC-004 exige reconciliação global antes do limite; isso permanece válido para auditoria
  global, mas conflita com o objetivo de lote rápido limitado.

## Objetivo

Implementar as Etapas A e B das otimizações da opção 5 com preservação dos gates já aceitos:

1. plano OP5 congelado explícito;
2. separação entre auditoria global e execução de lote;
3. seleção incremental que para ao atingir N elegíveis seguros;
4. pré-seleção local de candidatos;
5. reuso inteligente de PDFs/metadados.

A Etapa B adiciona cache de elegibilidade do Portal, cache da reconciliação global por SHA,
índice local da planilha, extração PDF paralela com limite controlado e comandos explícitos
`op5-plan`, `op5-apply` e `op5-audit-global`.

## Escopo

- Backend terminal da opção 5.
- Persistência privada do plano OP5.
- Contratos de validação do plano no real.
- Configuração de modo de reconciliação para lote rápido.
- Testes sintéticos sem Portal, CDP, planilha real, PDFs reais ou `.env`.

## Fora do escopo

- Desktop.
- Execução real de OP5.
- Mudança do limite máximo de 60.
- Execução real dos novos comandos.
- Tornar `batch_fast` padrão obrigatório.
- Paralelizar Portal/CDP ou escrita Excel.
- Cache distribuído/compartilhável.

## Glossário

- **Plano OP5:** artefato privado JSON gerado por dry-run, com lote, PDFs, hashes, plano de
  ações Excel, SHA da planilha e contrato de autorização.
- **Lote rápido:** execução OP5 limitada por `MAX_COMPLETED_TO_PROCESS` que pode parar a leitura
  do Portal ao atingir N elegíveis seguros.
- **Auditoria global:** reconciliação completa Portal × planilha, somente leitura, executada por
  modo explícito.

## Requisitos funcionais

### RF-001 — Plano OP5 explícito

Todo dry-run válido da opção 5 deve persistir `op5_plan_latest.json` em `LOGS_DIR`, além dos
relatórios existentes. O plano deve conter, no mínimo:

- `schema_version`;
- `created_at`;
- `source_report_path`;
- `dry_run=true`;
- `requested_batch_limit`;
- `authorized_batch_limit`;
- `authorization_scope`;
- `strong_confirmation_contract`;
- `workbook_sha256`;
- `workbook_path`;
- `apply_excel`;
- `apply_archive`;
- `frozen_batch`;
- `frozen_pdf_scope`;
- `planned_excel_actions` por protocolo;
- totais agregados de selecionados, planejados, aplicados e erros.

O plano é `PRIVATE_OPERATIONAL` e não é artefato compartilhável.

### RF-002 — Aplicação real pelo plano

Quando `DRY_RUN=false`, a opção 5 deve preferir `op5_plan_latest.json` como fonte canônica do
lote. O fallback para `pipeline_cdp_completo.json` é permitido apenas por compatibilidade
temporária e deve ficar registrado em `dry_run_plan_source_kind=legacy_pipeline_report`.

A aplicação real deve bloquear antes de qualquer escrita se:

- o plano estiver ausente;
- o plano não for dry-run;
- o status/totais indicarem erro;
- limite, escopo ou frase forte divergirem da configuração atual;
- PDF inexistir ou SHA divergir;
- SHA atual da planilha divergir de `workbook_sha256`;
- o plano não contiver ações planejadas coerentes.

A opcao 5 interativa pode pular a fase de novo dry-run quando encontrar
`LOGS_DIR/op5_plan_latest.json` pendente e validado para o mesmo limite, autorizacao, SHA da
planilha, `APPLY_ARCHIVE`, PDFs e acoes planejadas. Esse reaproveitamento deve:

- nao acessar Portal/CDP;
- nao baixar PDFs;
- registrar que o plano existente validado foi reutilizado;
- continuar exigindo confirmacao forte, backup da planilha e aplicacao pelo plano congelado;
- rejeitar plano `stale_after_failed_plan=true`, com erro, com limite divergente, com
  `APPLY_ARCHIVE` divergente, com SHA de planilha/PDF divergente ou sem acoes planejadas.

### RF-003 — Separação da reconciliação global

A reconciliação global da SPEC-004 permanece válida, mas deve ser controlada por modo explícito:

- `inline_global`: comportamento legado, lê todas as páginas e executa reconciliação antes do
  limite;
- `batch_fast`: não exige reconciliação global para aplicar lote limitado; o relatório deve marcar
  `reconciliation_mode=batch_fast` e `set_reconciliation_authoritative=false`;
- `audit_global`: modo somente leitura para reconciliação global sem aplicação Excel.

A mudança de modo não pode mascarar erro de extração, erro de aplicação ou divergência de lote.

### RF-004 — Seleção incremental

Em `batch_fast`, a leitura do Portal deve parar quando houver N ações reais planejadas
ou N protocolos explicitamente solicitados, respeitando:

- deduplicação por protocolo;
- `SKIP_ALREADY_COMPLETED`;
- `FORCE_REPROCESS_PROTOCOLS`;
- limite autorizado;
- paginação segura.

Para `op5-plan --limit N` sem `--protocols`, N representa o teto de alterações reais
planejadas para Excel (`insert_new_chronological`, `update_existing`,
`move_wrong_sheet_to_correct_sheet` ou equivalente), não o número bruto de protocolos
concluídos encontrados no Portal. Protocolos classificados como
`skipped_excel_already_updated` ou comprovadamente completos no estado local não consomem
o limite operacional do lote. Se os primeiros protocolos lidos já estiverem completos, o
planejamento deve continuar a seleção segura até encontrar até N ações planejadas,
atingir fim/safety cap da listagem ou encontrar erro bloqueante. O relatório deve manter
separados: concluídos lidos no Portal, selecionados para análise, completos/sem
alteração, ações Excel planejadas e ações Excel aplicadas.

Em `batch_fast`, protocolos com entrada valida e nao expirada em
`DATA_DIR/state/op5_completed_index.json` devem ser tratados como concluidos localmente quando
o PDF local existir com SHA-256 igual ao registrado, a entrada tiver aba/linha da planilha e o
protocolo nao estiver em `FORCE_REPROCESS_PROTOCOLS`. Esses protocolos nao devem consumir o
limite de updates planejados; o planejador deve seguir paginando para encontrar novos updates.
Essa regra nao autoriza limpeza de `data/downloads`, nao substitui plano congelado e nao
dispensa a validacao da planilha/PDF no `op5-apply`.

Em `batch_fast`, a parada incremental e a ordem de processamento devem favorecer candidatos
processaveis localmente. A leitura do Portal nao deve parar somente porque encontrou N
protocolos elegiveis brutos quando ainda nao existem N candidatos com PDF local valido e
metadata/conclusao suficiente. Depois da coleta, esses candidatos locais devem ser processados
antes dos protocolos que exigem abertura de detalhe, reduzindo navegacao CDP e risco de
`failed_return_to_listing`.

Se a leitura de detalhe ainda causar `partial_batch_due_to_listing_recovery=true`, o plano
resultante nao e considerado sucesso aplicavel pela opcao 5 interativa. O sistema deve reportar
estado parcial ou bloquear a aplicacao real ate que novo plano valido seja gerado.

Se a paginação terminar antes de N ações planejadas, o lote menor pode prosseguir em dry-run, desde que
o relatório registre o motivo e a quantidade efetiva. Em execução real, o plano congelado define
o escopo; não há nova seleção.

Ao processar um lote congelado, a navegação de retorno para a página/linha de origem deve
reaplicar a página registrada no item selecionado. Se o diagnóstico JavaScript da paginação
retornar `pagination_numeric_target_not_found`, mas listar o número alvo em
`numeric_page_links_found`, o adaptador CDP deve tentar o clique exato via locator Playwright antes
de declarar falha. A falha só pode ser registrada como `pagination_numeric_target_not_found` depois
desse fallback.

Se o clique numérico direto reportar sucesso, mas o Portal permanecer na mesma página ativa e com a
mesma assinatura de tabela, o `op5-plan` deve tentar a navegação sequencial já validada pelo
paginador antes de declarar `pagination_active_page_mismatch`. Essa recuperação só é válida para
planejamento/leitura do Portal; `op5-apply` continua aplicando exclusivamente o plano congelado sem
reler CDP.

Quando o detalhe do orçamento abrir na mesma aba e a aba não conseguir retornar para a listagem por
menu, URL salva, reload ou histórico, o `op5-plan` deve tentar recuperar somente abas já existentes
no mesmo contexto CDP que exibam a tabela `Minhas Solicitações`. O fluxo não pode abrir nova aba nem
navegar para a URL raiz do Portal para tentar autenticação automática. Se nenhuma aba HTTPS já
autenticada/listagem estiver disponível, deve falhar fechado e orientar o operador a reabrir o Edge
com CDP pelo comando PowerShell aprovado e autenticar manualmente. Essa recuperação não altera o
contrato do `op5-apply`, que continua proibido de reler Portal/CDP.

Quando a listagem estiver visível, mas o Portal não expuser indicador confiável da página ativa do
paginador, o reset inicial do `op5-plan` pode aceitar a página 1 apenas se o controle numérico `1`
estiver presente como item desabilitado/ativo e a tabela possuir linhas. O relatório deve marcar o
estado como página 1 não confirmada por indicador. Se essa evidência mínima não existir, o fluxo deve
continuar bloqueando antes de download/aplicação.

### RF-005 — Pré-seleção local

Antes de abrir detalhe ou baixar PDF, o lote rápido deve usar estado local disponível para
reduzir trabalho:

- protocolos já concluídos no state;
- protocolos já presentes na planilha com `Cliente`, `Protocolo`, `Data de ingresso`,
  `Conclusão`, `Parecer`, `Placa` e `Inversor` preenchidos;
- PDFs existentes válidos;
- metadados locais válidos;
- flags de reprocessamento.

A pré-seleção não pode excluir protocolo forçado por `FORCE_REPROCESS_PROTOCOLS`.

Em `batch_fast`, o planejador deve manter caches privados operacionais por SHA/validade curta:

- cache de verificacao da planilha por `workbook_sha256 + protocolo`, para evitar abrir a
  planilha repetidamente durante selecao e reexecucoes proximas;
- cache de elegibilidade do Portal por paginas/candidatos sanitizados, sem nome de cliente,
  endereco, UC, texto bruto, token ou cookie;
- quando uma execucao falhar antes de gerar resultados de PDF, os candidatos ja selecionados
  devem ser persistidos de forma sanitizada em `op5_portal_eligibility_cache.json`.

Na execucao seguinte, `batch_fast` pode iniciar a partir desse cache privado somente quando todos
os candidatos necessarios ao limite solicitado puderem ser processados localmente com PDF valido,
metadata/conclusao suficiente, workbook ainda compativel e sem `FORCE_REPROCESS_PROTOCOLS`. Se a
evidencia local nao for suficiente para completar o limite solicitado, o fluxo deve voltar ao
comportamento seguro via Portal/CDP manual autenticado; nao deve gerar plano enganoso.

### RF-006 — Reuso inteligente de PDFs e metadados

O reuso de PDF/metadados técnicos só é válido quando o cache estiver vinculado a:

- protocolo;
- SHA-256 do PDF;
- versão do extrator ou schema técnico;
- versão das regras técnicas quando disponível.

Para OP5 com preenchimento da coluna `Conclusão`, metadata local só pode evitar a abertura do
detalhe do Portal se contiver `completion_date`, `completion_date_raw`,
`completion_date_normalized` ou status canônico que justifique `EM ABERTO`. Metadata sem
evidência de conclusão deve obrigar nova leitura do detalhe, mesmo que o PDF já exista.

Excecao restrita: em `batch_fast`, quando houver PDF local valido e a linha de listagem/cache tiver
data de conclusao valida, a falta de `metadata.json` ou de conclusao na metadata nao deve, por si
so, forcar abertura de detalhe; a metadata pode ser criada/atualizada a partir da listagem com
fonte explicita. Fora de `batch_fast`, o comportamento legado permanece: PDF sem metadata valida
abre detalhe.

Cache legado sem protocolo e SHA do PDF deve ser reextraído.

### RF-006A — Arquivamento idempotente por SHA-256

O arquivamento real de orçamento de conexão deve ser idempotente por conteúdo.
Antes de criar `Orcamento_de_Conexao_<protocolo>_vN.pdf`, o sistema deve procurar,
na pasta destino resolvida, arquivos `Orcamento_de_Conexao_<protocolo>*.pdf`.

Se qualquer arquivo existente tiver SHA-256 igual ao PDF fonte:

- não copiar o PDF novamente;
- não criar `_v2`, `_v3` ou nova versão;
- retornar sucesso com `reason=archive_already_done`;
- registrar `source_pdf_sha256`, `archived_pdf_sha256` e caminho existente;
- contabilizar o caso como `total_archive_already_done`, não como erro.

Se houver arquivo com mesmo protocolo e SHA diferente, o comportamento seguro permanece:
criar versão `_vN` sem sobrescrever arquivo existente e registrar os dois hashes quando possível.

### RF-007 — Cache privado de elegibilidade do Portal

Em `batch_fast`, o dry-run pode persistir um snapshot privado e sanitizado da elegibilidade do
Portal em `LOGS_DIR/op5_portal_eligibility_cache.json`.

O cache deve conter somente campos mínimos para pré-seleção:

- protocolo;
- página;
- índice da linha;
- status operacional;
- motivo da elegibilidade;
- data de captura;
- hash estrutural do snapshot;
- limite solicitado;
- modo de reconciliação.

O cache não pode conter nome de cliente, endereço, UC, texto bruto do Portal, cookies, tokens ou
caminhos locais. Ele só pode ser reutilizado quando:

- estiver dentro do TTL configurado;
- o modo for `batch_fast`;
- o limite solicitado for compatível;
- o hash estrutural e a versão do schema forem compatíveis.

Cache de elegibilidade não autoriza escrita real por si só. A escrita real continua exigindo
plano OP5 congelado, lock global, confirmação forte, hashes de PDF e SHA da planilha.

### RF-008 — Cache da reconciliação global por SHA

A reconciliação Portal × planilha pode ser cacheada em
`LOGS_DIR/portal_workbook_reconciliation_cache.json` quando executada em modo de auditoria ou
`inline_global`.

O cache só é válido quando todos os itens abaixo coincidirem:

- SHA-256 atual da planilha;
- hash do conjunto de protocolos concluídos do Portal;
- versão do schema de reconciliação;
- modo de reconciliação;
- versão do índice local da planilha.

Em hit válido, o fluxo pode reutilizar o resumo da reconciliação sem recomputar a leitura global
da planilha. Em miss, deve recomputar e gravar novo cache. O cache é privado operacional.

### RF-009 — Índice local da planilha

O sistema deve manter um índice privado em `LOGS_DIR/workbook_index_cache.json`, vinculado ao
SHA-256 da planilha.

O índice deve mapear protocolo para metadados mínimos:

- aba;
- linha;
- status de conclusão;
- presença de equipamentos;
- hash da linha quando disponível.

O índice deve ser invalidado quando o SHA da planilha mudar. O índice não pode ser incluído em
release, fixture permanente ou relatório compartilhável.

### RF-009A — Estado mestre OP5 concluído

O sistema deve manter um índice privado operacional em
`DATA_DIR/state/op5_completed_index.json`.

Cada protocolo concluído com sucesso no OP5 deve registrar:

- `status=completed`;
- SHA-256 do PDF local em `data/downloads`;
- caminho relativo ou absoluto privado do PDF local;
- SHA-256 do PDF arquivado;
- caminho privado do PDF arquivado;
- aba e linha da planilha afetada ou validada;
- SHA-256 da planilha no momento da validação;
- versão do extrator técnico;
- versão das regras técnicas;
- `updated_at`;
- `expires_at`, exatamente 14 dias após a atualização.

O índice mestre é uma evidência privada operacional. Ele não autoriza escrita por si só,
não substitui plano congelado, lock global ou confirmação forte, e não pode ser publicado em
relatórios compartilháveis, fixtures permanentes ou release.

Após `expires_at`, a entrada deve ser considerada expirada e não pode ser usada para pular
validação futura. A criação do índice não deve apagar PDFs locais; limpeza de `data/downloads`
fica fora desta etapa e exigirá plano separado.

### RF-010 — Extração de PDF paralela com limite controlado

Após o lote estar congelado, a extração técnica dos PDFs pode executar em paralelo com limite
controlado por `OP5_PDF_WORKERS`.

Contrato:

- valor permitido: `1` a `4`;
- padrão: `1`, preservando comportamento serial;
- Portal/CDP nunca é paralelizado;
- escrita Excel, arquivamento, state e relatórios continuam seriais;
- a ordem do resultado final deve seguir a ordem do lote congelado;
- exceções de workers devem virar resultado por protocolo, sem abortar outros PDFs já em análise;
- produção real só aplica efeitos depois da fase de extração/planejamento estar consolidada.

### RF-011 — Comandos explícitos de UX operacional

O terminal deve aceitar comandos explícitos sem depender do menu interativo:

```text
py app.py op5-plan --limit N
py app.py op5-plan --limit N --protocols <lista>
py app.py op5-apply --plan <arquivo>
py app.py op5-audit-global
py app.py op5-retention-audit
py app.py op5-retention-apply --plan <arquivo>
py app.py workbook-format-audit
py app.py workbook-format-apply --plan <arquivo>
```

Contratos:

- `op5-plan --limit N` executa dry-run da opção 5 com `MAX_COMPLETED_TO_PROCESS=N`,
  `OP5_RECONCILIATION_MODE=batch_fast`, gera plano OP5 e não aplica Excel;
- `op5-plan --limit N --protocols <lista>` restringe a seleção aos protocolos explícitos,
  continua lendo páginas até encontrar o subconjunto solicitado ou encerrar a paginação segura,
  e não pode substituir a confirmação forte da execução real;
- o subconjunto de `--protocols` deve ser propagado pelo `Settings` efetivo da execução até a
  camada CDP/download; a seleção não pode depender de `get_settings()` global nem de variável de
  ambiente externa para respeitar o lote direcionado;
- `op5-apply --plan <arquivo>` executa produção somente a partir do plano informado, sem nova
  navegação CDP, com confirmação forte vinculada à quantidade do plano;
- `op5-audit-global` executa reconciliação global somente leitura, sem download, sem aplicação
  Excel e sem gerar plano de aplicação;
- todos os comandos devem retornar código não-zero em bloqueio/cancelamento/erro;
- nenhum comando pode ler `.env` em teste nem acessar Portal/CDP sem autorização operacional.

### RF-012 — Retenção segura de `data/downloads`

O sistema deve oferecer limpeza segura dos PDFs locais baixados sem apagar diretamente no audit:

```text
py app.py op5-retention-audit
py app.py op5-retention-apply --plan <arquivo>
```

Contratos:

- `op5-retention-audit` é somente leitura e gera um plano privado operacional em `LOGS_DIR`;
- `op5-retention-apply` só apaga arquivos listados no plano informado;
- nenhum diretório inteiro pode ser removido;
- cada arquivo só pode ser excluído se todos os critérios forem verdadeiros:
  - o PDF local está dentro de `DOWNLOADS_DIR`;
  - o protocolo possui entrada válida e não expirada em `DATA_DIR/state/op5_completed_index.json`;
  - o SHA-256 do PDF local coincide com `download_pdf_sha256` do índice mestre;
  - o PDF arquivado existe e seu SHA-256 coincide com `archived_pdf_sha256`;
  - o SHA-256 local e o SHA-256 arquivado são iguais;
  - a entrada contém aba e linha da planilha;
  - o SHA-256 atual da planilha coincide com `workbook_sha256` do índice mestre.
- arquivos sem todos os critérios devem ser preservados e aparecer como bloqueados no plano.

O plano de retenção é privado operacional e não pode ser relatório compartilhável, release ou
fixture permanente. Falha de validação no apply deve preservar o arquivo.

### RF-013 — Saneamento global da planilha separado do OP5

O saneamento/formatação global da planilha deve permanecer separado da opção 5:

```text
py app.py workbook-format-audit
py app.py workbook-format-apply --plan <arquivo>
```

Contratos:

- `workbook-format-audit` executa somente leitura/dry-run, calcula SHA-256 da planilha e grava
  plano privado operacional;
- `workbook-format-apply` exige `--plan`, revalida o SHA-256 atual da planilha e bloqueia se o
  arquivo mudou desde a auditoria;
- a aplicação reutiliza o reparo/formatação existente, cria backup conforme contrato atual e não
  roda Portal/CDP/download/arquivamento;
- a OP5 não deve chamar automaticamente esses comandos.

## Requisitos não funcionais

- Compatível com Windows e Linux.
- Determinístico em testes.
- Sem leitura de `.env` nos testes.
- Sem Portal/CDP em testes.
- Sem dados reais em fixtures permanentes.
- Nenhuma mudança deve reduzir os controles da SPEC-009.

## Contratos e interfaces

Configuração:

```text
OP5_RECONCILIATION_MODE=inline_global | batch_fast | audit_global
OP5_ELIGIBILITY_CACHE_TTL_MINUTES=30
OP5_PDF_WORKERS=1
OP5_TARGET_PROTOCOLS=
```

Valor padrão inicial: `inline_global`, para preservar compatibilidade. O lote rápido deve ser
ativado explicitamente até concluir homologação operacional.

Artefato:

```text
LOGS_DIR/op5_plan_latest.json
LOGS_DIR/op5_portal_eligibility_cache.json
LOGS_DIR/workbook_index_cache.json
LOGS_DIR/portal_workbook_reconciliation_cache.json
LOGS_DIR/op5_retention_plan_latest.json
LOGS_DIR/workbook_format_plan_latest.json
DATA_DIR/state/op5_completed_index.json
```

## Dados e persistência

`op5_plan_latest.json` é privado operacional e pode conter caminhos locais necessários para
retomada. Ele não pode ser incluído em release, relatório compartilhável, fixture permanente ou
documentação pública.

## Estados e tratamento de erros

Novos códigos:

- `OP5_PLAN_REQUIRED`;
- `OP5_PLAN_INVALID`;
- `OP5_PLAN_WORKBOOK_CHANGED`;
- `OP5_PLAN_PDF_CHANGED`;
- `OP5_RECONCILIATION_MODE_INVALID`.
- `OP5_PLAN_COMMAND_FAILED`;
- `OP5_APPLY_PLAN_REQUIRED`;
- `OP5_AUDIT_GLOBAL_FAILED`;
- `OP5_CACHE_INVALIDATED`.
- `OP5_RETENTION_PLAN_REQUIRED`;
- `OP5_RETENTION_PLAN_INVALID`;
- `OP5_RETENTION_VALIDATION_FAILED`;
- `WORKBOOK_FORMAT_PLAN_REQUIRED`;
- `WORKBOOK_FORMAT_PLAN_INVALID`;
- `WORKBOOK_FORMAT_WORKBOOK_CHANGED`.

## Segurança e privacidade

O plano não deve ser impresso integralmente no terminal. Saída operacional mostra apenas
contadores, modo, digest e frase requerida. O scanner de privacidade deve continuar bloqueando
artefatos públicos com dados operacionais.

## UX e acessibilidade, quando aplicável

O terminal deve deixar claro quando a execução real reutilizou plano OP5 e quando CDP foi
pulado. Em `batch_fast`, deve informar que a reconciliação global não foi executada e pode ser
rodada separadamente.

Na aplicacao interativa da opcao 5, a frase de confirmacao forte deve ser exibida em linha
propria, sem texto explicativo antes ou depois na mesma linha. Textos como "para aplicar este
plano" devem ficar em linha separada para evitar que o operador copie sufixos que invalidam a
confirmacao.

## Observabilidade

Relatórios devem registrar:

- `op5_plan_path`;
- `op5_plan_source_kind`;
- `op5_plan_digest`;
- `reconciliation_mode`;
- `cdp_selection_skipped`;
- `incremental_selection_enabled`;
- `incremental_stop_reason`.
- `eligibility_cache_hit`;
- `eligibility_cache_path`;
- `workbook_index_cache_hit`;
- `workbook_index_path`;
- `reconciliation_cache_hit`;
- `pdf_workers`.

## Compatibilidade

O comportamento `inline_global` preserva a SPEC-004. O fallback legado para
`pipeline_cdp_completo.json` deve existir apenas durante a transição para o plano explícito.

## Migração ou backfill

Nenhum backfill. Planos antigos gerados apenas em `pipeline_cdp_completo.json` continuam aceitos
temporariamente se passarem pelos validadores da SPEC-009.

## Estratégia de testes

- RED para dry-run que não cria `op5_plan_latest.json`.
- RED para real que usa relatório legado mesmo quando plano explícito existe.
- RED para real que não bloqueia workbook alterado após plano.
- RED para `batch_fast` chamando reconciliação global ou exigindo paginação completa.
- RED para cache técnico sem SHA/protocolo sendo reutilizado.
- RED para cache de elegibilidade contendo campos sensíveis ou sendo reutilizado fora do TTL.
- RED para cache de reconciliação sendo reutilizado com SHA de planilha divergente.
- RED para índice local da planilha sendo reutilizado após mudança do SHA.
- RED para arquivamento repetido com PDF idêntico criando `_v2` indevido.
- RED para índice mestre OP5 ausente ou sem TTL de 14 dias após protocolo concluído.
- RED para extração PDF paralela que perde a ordem do lote ou aceita mais de 4 workers.
- RED para CLI sem os comandos `op5-plan`, `op5-apply` e `op5-audit-global`.
- RED para `op5-plan --protocols` selecionando protocolo fora dos primeiros N elegíveis.
- RED para retenção tentando excluir PDF sem índice mestre válido, SHA arquivado validado e SHA da planilha igual.
- RED para `op5-retention-apply` recusando plano ausente, plano alterado ou arquivo fora de `DOWNLOADS_DIR`.
- RED para saneamento de workbook sem plano ou com SHA da planilha alterado.
- RED para OP5 permanecer independente dos comandos de saneamento global.

## Critérios de aceite

- [ ] Dry-run OP5 gera plano explícito com lote, hashes, workbook SHA e ações planejadas.
- [ ] Real OP5 usa plano explícito sem CDP e valida hashes antes de escrever.
- [ ] Plano com workbook alterado bloqueia antes de escrita.
- [ ] `inline_global` preserva comportamento atual da reconciliação.
- [ ] `batch_fast` permite lote limitado sem reconciliação global obrigatória.
- [ ] Seleção incremental para ao atingir N elegíveis seguros.
- [ ] Cache legado sem protocolo/SHA não é reutilizado.
- [ ] Cache de elegibilidade é privado, sanitizado e invalidado por TTL/hash/modo.
- [ ] Reconciliação global pode ser reutilizada por cache somente com SHA/hashes compatíveis.
- [ ] Índice local da planilha é reutilizado por SHA e invalidado em mudança.
- [ ] Arquivamento repetido de PDF idêntico não cria `_v2` e registra `archive_already_done`.
- [ ] Índice mestre OP5 registra protocolo concluído com hashes, aba/linha, versões e TTL de 14 dias.
- [ ] Extração PDF paralela respeita limite 1..4 e preserva ordem.
- [ ] Comandos explícitos `op5-plan`, `op5-apply` e `op5-audit-global` existem e falham fechado.
- [ ] `op5-plan --protocols` restringe o lote aos protocolos explícitos e não para antes de
      procurá-los nas páginas permitidas.
- [ ] `op5-retention-audit` gera plano dry-run sem apagar PDFs.
- [ ] `op5-retention-apply` exclui apenas PDFs locais com índice mestre válido, hashes iguais e planilha validada.
- [ ] `workbook-format-audit` gera plano separado sem alterar workbook.
- [ ] `workbook-format-apply` aplica somente com plano e SHA da planilha inalterado.
- [ ] Testes direcionados, Ruff, MyPy e scanner permanecem verdes.

## Adendo 2026-08-13 - Opcao 5 interativa como orquestrador seguro

A opcao `5 - Executar pipeline CDP completo` do menu interativo de `app.py`
deve orquestrar o mesmo fluxo seguro dos comandos explicitos:

- pedir o limite do lote;
- gerar `op5-plan` em dry-run;
- exibir o resumo do plano;
- aceitar apply somente quando o plano estiver `SUCESSO`, `dry_run=true`, sem
  erros e sem updates aplicados;
- exigir confirmacao forte vinculada ao limite autorizado;
- registrar SHA-256 da planilha antes da escrita;
- criar backup validado antes da escrita;
- executar producao apenas a partir do `op5_plan_latest.json` congelado;
- nao reler Portal/CDP durante o apply.

## Rollout

1. Implementar e validar offline/sintético.
2. Rodar gates locais.
3. Commitar e enviar ao CI.
4. Só após CI verde, autorizar novo dry-run operacional.

## Rollback

Reverter a SPEC-010 e os commits da Etapa A. O modo padrão `inline_global` minimiza impacto de
rollback porque preserva o fluxo anterior.

## Riscos

- Separar reconciliação pode reduzir visibilidade se o operador esquecer a auditoria global.
- Bloqueio por SHA de planilha pode exigir novo dry-run quando alguém alterar a planilha entre
  dry-run e real; isso é intencional para segurança.

## Decisões pendentes

- Quando tornar `batch_fast` padrão.
- Quando tornar os caches padrão para execução operacional longa.

## Evidências de homologação

A preencher após RED/GREEN e gates.
