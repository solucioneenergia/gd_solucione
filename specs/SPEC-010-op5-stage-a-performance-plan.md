# SPEC-010 — OP5 Etapa A: plano congelado e lote rápido auditável

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

Implementar a Etapa A das otimizações da opção 5 com preservação dos gates já aceitos:

1. plano OP5 congelado explícito;
2. separação entre auditoria global e execução de lote;
3. seleção incremental que para ao atingir N elegíveis seguros;
4. pré-seleção local de candidatos;
5. reuso inteligente de PDFs/metadados.

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
- Paralelização de extração PDF.
- Cache persistido de elegibilidade do Portal entre execuções.
- Índice persistido da planilha.
- Nova UX completa de subcomandos `op5-plan`, `op5-apply` e `op5-audit-global`.

Esses itens ficam para Etapa B ou SPEC posterior.

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

### RF-003 — Separação da reconciliação global

A reconciliação global da SPEC-004 permanece válida, mas deve ser controlada por modo explícito:

- `inline_global`: comportamento legado, lê todas as páginas e executa reconciliação antes do
  limite;
- `batch_fast`: não exige reconciliação global para aplicar lote limitado; o relatório deve marcar
  `reconciliation_mode=batch_fast` e `set_reconciliation_authoritative=false`;
- `audit_global`: modo somente leitura para reconciliação global sem aplicação Excel.

A mudança de modo não pode mascarar erro de extração, erro de aplicação ou divergência de lote.

### RF-004 — Seleção incremental

Em `batch_fast`, a leitura do Portal deve parar quando houver N protocolos elegíveis e
processáveis, respeitando:

- deduplicação por protocolo;
- `SKIP_ALREADY_COMPLETED`;
- `FORCE_REPROCESS_PROTOCOLS`;
- limite autorizado;
- paginação segura.

Se a paginação terminar antes de N elegíveis, o lote menor pode prosseguir em dry-run, desde que
o relatório registre o motivo e a quantidade efetiva. Em execução real, o plano congelado define
o escopo; não há nova seleção.

### RF-005 — Pré-seleção local

Antes de abrir detalhe ou baixar PDF, o lote rápido deve usar estado local disponível para
reduzir trabalho:

- protocolos já concluídos no state;
- PDFs existentes válidos;
- metadados locais válidos;
- flags de reprocessamento.

A pré-seleção não pode excluir protocolo forçado por `FORCE_REPROCESS_PROTOCOLS`.

### RF-006 — Reuso inteligente de PDFs e metadados

O reuso de PDF/metadados técnicos só é válido quando o cache estiver vinculado a:

- protocolo;
- SHA-256 do PDF;
- versão do extrator ou schema técnico;
- versão das regras técnicas quando disponível.

Cache legado sem protocolo e SHA do PDF deve ser reextraído.

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
```

Valor padrão inicial: `inline_global`, para preservar compatibilidade. O lote rápido deve ser
ativado explicitamente até concluir homologação operacional.

Artefato:

```text
LOGS_DIR/op5_plan_latest.json
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

## Segurança e privacidade

O plano não deve ser impresso integralmente no terminal. Saída operacional mostra apenas
contadores, modo, digest e frase requerida. O scanner de privacidade deve continuar bloqueando
artefatos públicos com dados operacionais.

## UX e acessibilidade, quando aplicável

O terminal deve deixar claro quando a execução real reutilizou plano OP5 e quando CDP foi
pulado. Em `batch_fast`, deve informar que a reconciliação global não foi executada e pode ser
rodada separadamente.

## Observabilidade

Relatórios devem registrar:

- `op5_plan_path`;
- `op5_plan_source_kind`;
- `op5_plan_digest`;
- `reconciliation_mode`;
- `cdp_selection_skipped`;
- `incremental_selection_enabled`;
- `incremental_stop_reason`.

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

## Critérios de aceite

- [ ] Dry-run OP5 gera plano explícito com lote, hashes, workbook SHA e ações planejadas.
- [ ] Real OP5 usa plano explícito sem CDP e valida hashes antes de escrever.
- [ ] Plano com workbook alterado bloqueia antes de escrita.
- [ ] `inline_global` preserva comportamento atual da reconciliação.
- [ ] `batch_fast` permite lote limitado sem reconciliação global obrigatória.
- [ ] Seleção incremental para ao atingir N elegíveis seguros.
- [ ] Cache legado sem protocolo/SHA não é reutilizado.
- [ ] Testes direcionados, Ruff, MyPy e scanner permanecem verdes.

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
- Formato final dos subcomandos explícitos da Etapa B.

## Evidências de homologação

A preencher após RED/GREEN e gates.
