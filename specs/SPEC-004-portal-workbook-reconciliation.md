# SPEC-004 — Reconciliação global Portal GD × planilha

Status: Implementação
Data: 2026-07-28
Responsável: Codex
Revisores: revisão sênior ao final da Etapa 2
ADRs relacionadas: nenhuma
Issues relacionadas: Etapa 2 / reconciliação global Portal GD × planilha oficial

## Contexto

A opção 5 do pipeline CDP completo já lê o filtro `Concluídos` do Portal GD, aplica limite operacional e processa apenas o lote autorizado. A Etapa 1 da v2.1.0 integrou a coluna `Conclusão` ao mesmo fluxo, preservando o limite global da v2.0.1.

## Problema

O operador precisa de um diagnóstico global entre todos os protocolos concluídos no Portal e todos os protocolos da planilha oficial, mas sem transformar essa auditoria em processamento em massa.

## Evidências do comportamento atual

- A opção 5 lê todas as páginas de `Concluídos`.
- O limite `MAX_COMPLETED_TO_PROCESS` restringe o lote operacional.
- Não existe relatório global com teoria de conjuntos Portal × planilha antes do limite.

## Objetivo

Executar automaticamente uma reconciliação global, somente leitura, antes da seleção do lote operacional da opção 5.

## Escopo

- Ler todos os protocolos concluídos do Portal já coletados pela opção 5.
- Indexar todas as abas da planilha oficial em modo read-only.
- Comparar conjuntos `P` e `W`.
- Gerar relatórios JSON e Markdown.
- Exibir resumo compacto no terminal da opção 5.
- Manter o processamento operacional limitado por `MAX_COMPLETED_TO_PROCESS`.

## Fora do escopo

- Baixar PDFs para todos os protocolos auditados.
- Abrir detalhes de todos os protocolos auditados.
- Alterar a planilha durante a reconciliação.
- Criar linhas ausentes em massa.
- Mover linhas entre abas.
- Preencher conclusões, módulos ou inversores fora do lote operacional.
- Reabrir Parser V6, Validador V7 ou backfill histórico.
- Criar manifesto ou tag v2.1.0.

## Glossário

- `P`: protocolos únicos concluídos no Portal.
- `W`: protocolos únicos válidos encontrados na planilha.
- Reconciliação: comparação somente leitura entre `P` e `W`.
- Lote operacional: protocolos selecionados para detalhe, PDF, Excel e arquivamento.

## Requisitos funcionais

1. A opção 5 deve executar a reconciliação após ler todas as páginas de `Concluídos` e antes de aplicar `MAX_COMPLETED_TO_PROCESS`.
2. A reconciliação deve gerar `portal_workbook_reconciliation_<timestamp>.json` e `.md`.
3. O serviço de reconciliação deve ser isolado em `automacao_gd/application/reconciliation_service.py`.
4. Protocolos do Portal devem ser normalizados em memória e deduplicados.
5. Protocolos da planilha devem ser normalizados em memória sem modificar células.
6. A reconciliação deve calcular `P ∩ W`, `P - W` e `W - P`.
7. Deve detectar ausentes, duplicados, aba anual incorreta, conclusões vazias/inválidas, equipamentos vazios, registros incompletos e linhas vazias residuais.
8. Achados de dados não devem bloquear o lote operacional seguro.
9. Erros sistêmicos devem bloquear antes da seleção operacional.

## Requisitos não funcionais

- A reconciliação deve ser read-only.
- A planilha oficial deve manter SHA idêntico antes/depois da reconciliação.
- Relatórios não devem conter nomes de clientes quando o protocolo for suficiente.
- A saída de terminal não deve listar centenas de protocolos.

## Contratos e interfaces

Entrada:

```text
PortalProtocolIndex
WorkbookProtocolIndex
MAX_COMPLETED_TO_PROCESS
```

Saída:

```text
ReconciliationResult
portal_workbook_reconciliation_<timestamp>.json
portal_workbook_reconciliation_<timestamp>.md
```

## Dados e persistência

Somente relatórios em `data/logs/`. Nenhuma escrita na planilha, no estado de retomada ou em PDFs.

## Estados e tratamento de erros

Bloqueantes:

- Planilha ilegível.
- Colunas essenciais ausentes.
- Paginação incompleta.
- SHA alterado durante a reconciliação.
- Tentativa de escrita.

Não bloqueantes:

- Protocolo ausente na planilha.
- Duplicado na planilha.
- Aba anual incorreta.
- Conclusão vazia.
- Equipamento vazio.
- Registro incompleto.

## Segurança e privacidade

Relatórios detalhados devem usar protocolo, aba e linha. Nomes de clientes não devem ser emitidos quando o protocolo for suficiente.

## UX e acessibilidade

O terminal deve exibir um resumo compacto:

```text
Reconciliação Portal × planilha:
- Concluídos únicos no Portal:
- Protocolos únicos na planilha:
- Encontrados nas duas fontes:
- Ausentes na planilha:
- Somente na planilha:
- Duplicados na planilha:
- Em aba anual incorreta:
- Conclusões vazias:
- Equipamentos vazios para revisão:
- Registros incompletos:
- Relatório:
```

## Observabilidade

O JSON deve conter seções: `metadata`, `portal_summary`, `workbook_summary`, `set_reconciliation`, `missing_in_workbook`, `duplicate_workbook_protocols`, `wrong_year_sheet`, `completion_audit`, `equipment_field_audit`, `incomplete_records`, `workbook_only_protocols`, `structural_findings`, `safety`, `operational_batch`, `review`, `decision`.

## Compatibilidade

Preservar opção 5, limite global v2.0.1, conclusão integrada da Etapa 1, Parser V6 e Validador V7.

## Migração ou backfill

Nenhum backfill ou migração de dados nesta etapa.

## Estratégia de testes

- Testes unitários do serviço de reconciliação.
- Testes de integração do pipeline comprovando reconciliação antes do limite.
- Testes de resumo operacional.
- Dry-run real com Portal/CDP e planilha oficial em modo somente leitura.

## Critérios de aceite

- [ ] Dado 100 concluídos no Portal e limite 5, quando a opção 5 roda, então 100 protocolos são auditados e no máximo 5 são processados.
- [ ] Dado protocolo concluído ausente da planilha, então o relatório registra `PORTAL_COMPLETED_MISSING_IN_WORKBOOK`.
- [ ] Dado protocolo duplicado na planilha, então todas as ocorrências são registradas como `DUPLICATE_PROTOCOL_IN_WORKBOOK`.
- [ ] Dado protocolo numérico inteiro no Excel, então a comparação usa texto canônico e a célula não é modificada.
- [ ] Dado data de ingresso incompatível com a aba anual, então o relatório registra `PROTOCOL_IN_WRONG_YEAR_SHEET`.
- [ ] Dado linha vazia residual, então ela é classificada como `TRAILING_MATERIALIZED_EMPTY_ROW` e não como projeto.
- [ ] Dado reconciliação global, então downloads, detalhes, arquivamento, backup, `workbook.save` e `os.replace` não são executados.
- [ ] Dado a reconciliação finalizada, então o SHA antes/depois da planilha é idêntico.

## Rollout

Ativar como parte read-only da opção 5 em v2.1.0.

## Rollback

Remover a chamada da reconciliação da opção 5. Como a etapa é read-only, não há rollback de dados.

## Riscos

- Crescimento do relatório se houver muitos achados.
- Classificação de registros incompletos pode exigir refinamento posterior.
- Mudanças futuras no Portal podem afetar a leitura de todas as páginas.

## Decisões pendentes

Nenhuma decisão arquitetural difícil de reverter foi identificada.

## Evidências de homologação

A preencher após testes, dry-run real e revisão sênior.

## Evidencia de encerramento da reconciliacao global

Etapa 2.1 foi revalidada com paginacao completa do Portal:

- paginas lidas: 15;
- ultima pagina confirmada: sim;
- protocolos concluidos unicos no Portal: 629;
- protocolos unicos na planilha antes do saneamento direcionado: 631;
- `P - W`: 4;
- `W - P`: 6;
- `metrics_scope=global`;
- `set_reconciliation_authoritative=true`.

Apos a aplicacao direcionada do protocolo `2607077271`, a teoria de conjuntos foi recalculada em modo read-only a partir da evidencia global completa congelada e da planilha atual:

- protocolos concluidos unicos no Portal: 629;
- protocolos unicos na planilha: 632;
- `P ∩ W`: 626;
- `P - W`: 3;
- `W - P`: 6.

A SPEC-004 permanece como contrato de reconciliacao read-only. Aplicacoes de saneamento direcionado passam a seguir a SPEC-005.
