# Relatorio - validacao canonica com totais agregados

Data: 2026-08-09

## Contexto

Durante o canario controlado, um protocolo real informado pelo operador ficou pendente por
`CANONICAL_STRUCTURE_INCOMPLETE`, `MODULE_QUANTITY_MISSING` e
`INVERTER_QUANTITY_MISSING`, embora a extracao tecnica tivesse produzido multiplos modelos de
modulos e inversores com totais agregados confiaveis.

Este relatorio nao registra protocolos reais, nomes de clientes, caminhos operacionais, PDFs,
planilhas ou dados extraidos do Portal.

## Decisao de contrato

Politica escolhida: Opção A - aprovar com totais agregados confiaveis.

A planilha V2 aceita um campo textual unico por coluna. Quando os modelos aparecem separados e a
categoria possui `Qtd. total` positivo, a quantidade agregada da categoria e suficiente para
validacao tecnica, desde que:

- exista ao menos um modelo canonico de modulo;
- exista ao menos um modelo canonico de inversor ou microinversor;
- os totais agregados positivos existam na categoria correspondente;
- a origem seja tipada;
- nao exista contaminacao ou conflito entre categorias.

Multiplos modelos sem total agregado positivo continuam pendentes e nao chamam Excel.

## Correcoes

- A validacao canonica passou a considerar total agregado tambem para multiplos itens.
- O cache tecnico V7 aceita estrutura canonica com multiplos itens sem quantidade individual
  quando a categoria tem total agregado positivo.
- O fluxo de reformatacao de linhas existentes segue o mesmo contrato: aceita total agregado
  confiavel e bloqueia ausencia real de quantidade.
- As metricas consolidadas do canario separam erro real de extracao/aplicacao, pendencia tecnica,
  Excel ja atualizado, updates planejados/aplicados e bloqueio por politica de lote.
- Relatorios compartilháveis incluem os novos totais somente por allowlist.

## Evidencias de teste

- `python -m pytest -q tests/test_equipment_reformatting.py`:
  `21 passed`
- `python -m pytest -q tests/test_pdf_service.py tests/test_processing_service.py tests/test_processing_resilience.py`:
  `64 passed`
- `python -m pytest -q tests/test_stage1_terminal_persistence.py tests/test_terminal_operation_safety.py`:
  `32 passed`
- `python -m pytest -q tests/test_release_privacy.py tests/test_release_validator_hardening.py`:
  `48 passed`
- `python -m pytest -q tests/test_full_cdp_pipeline.py tests/test_operational_output.py`:
  `70 passed`
- `python -m pytest -q tests/test_pending_equipment_triage.py tests/test_stage1_review_regressions.py tests/test_equipment_canonical_comparison.py`:
  `82 passed`
- `python -m pytest -q` executado pelo operador no PowerShell:
  `959 passed, 1 skipped`
- `python -m ruff check automacao_gd apps src scripts tests`:
  aprovado
- `python -m mypy automacao_gd`:
  aprovado
- `python scripts/privacy_scan.py ...`:
  `valid=true`, `359 scanned`, `0 findings`

## Seguranca operacional

Nesta correcao nao houve acesso ao Portal, Edge/CDP, `.env`, PDF real, workbook real, pasta real de
clientes, download novo, escrita real de planilha, arquivamento real ou avanço para a Etapa 2.

## Status

Pronto para commit local. O canario real de escrita permanece separado e exige autorizacao
operacional propria antes de qualquer execucao.
