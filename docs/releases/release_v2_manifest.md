# Release v2.0.0 — Automação GD Neoenergia

## Identidade

- Versão: v2.0.0
- Data: 2026-07-24
- Commit: commit de fechamento identificado pela tag `v2.0.0`
- Tag: `v2.0.0`
- Release técnica: APPROVED
- Produção controlada: AUTHORIZED

## Objetivo da release

Liberar a Automação GD Neoenergia para produção controlada após fechamento do backfill histórico, validação ponta a ponta do Portal GD via CDP, atualização controlada da planilha, isolamento de pendências técnicas por protocolo e aprovação dos quality gates finais.

## Componentes aprovados

- Portal GD via Edge CDP.
- Paginação de solicitações concluídas.
- Download e reutilização segura de orçamentos de conexão.
- Validação de PDF.
- Parser PDF V6.
- Validador semântico V7.
- Normalização de fabricantes e equipamentos.
- Microinversores na coluna Inversor.
- Atualização controlada da planilha.
- Arquivamento condicionado ao sucesso do Excel.
- Isolamento individual de pendências técnicas.
- Backfill histórico rules-4 e rules-7 aplicado e fechado.
- Relatórios operacionais sanitizados.

## Identidade da planilha

- SHA oficial atual: `c86c97a1c31f58e0150222872f91e79941bf8b5ede8fcac715c3fffc0bca973e`
- SHA anterior histórico: `22d2872b30bccbd4be2f04ce89871632e46691f9d5ce387376b2ff7d44afb5ee`
- Causa da mudança de SHA: aplicação dos três protocolos seguros no canário aprovado da Etapa 3.2.

## Quality gates herdados

- Testes direcionados: 86 passed
- Testes isolamento/Etapa 0: 8 passed
- Suíte completa: 677 passed
- Ruff: passed
- Compileall: passed
- MyPy dos arquivos alterados: passed

## Resultado E2E

- Portal acessado: SIM
- Páginas lidas: 5
- Protocolos selecionados: 5
- PDFs analisados: 5
- Protocolos seguros: 3
- Protocolos NO_CHANGE: 2
- Protocolos pendentes no canário: 0
- Updates aplicados: 3
- Erros sistêmicos: 0
- Mudanças fora da allowlist: 0
- P0: 0
- P1: 0

## Proteções de produção

- `APP_ENV=production` obrigatório.
- Confirmação explícita no menu para execução real.
- Pré-voo da planilha antes de escrita.
- Segunda validação antes da primeira gravação.
- Backup habilitado.
- Aplicação atômica do subconjunto seguro.
- Rollback em falha sistêmica após início de aplicação.
- Pendência técnica individual não bloqueia protocolo seguro.
- Erro sistêmico bloqueia o lote inteiro.
- Arquivamento ocorre depois do Excel para protocolos seguros.
- Relatórios não devem conter credenciais, dados pessoais ou caminhos absolutos.

## Limitações conhecidas

- A operação ampla permanece bloqueada até autorização operacional explícita.
- Execução não interativa do menu pode gerar EOF após o pipeline quando entradas extras são enviadas por pipe. Classificação: P3, sem impacto no uso interativo normal.
- Protocolos com quantidade individual não comprovada permanecem pendentes para conferência.

## Protocolos pendentes protegidos

Os protocolos abaixo permanecem `PENDING_REVIEW/SOURCE_INCOMPLETE`:

- `2605250167`
- `2605148473`
- `2605056663`
- `2604275348`

Motivo vinculante: a quantidade total aparece no documento, mas a quantidade individual por modelo ou fabricante não está documentalmente separada.

Regras:

- não inferir quantidade;
- não modificar automaticamente o Excel;
- não marcar como concluído;
- não arquivar como processado com sucesso;
- manter disponível para conferência;
- não classificar como `PARSER_GAP` sem nova evidência documental.

## Procedimento de rollback

1. Interromper novas execuções.
2. Identificar o backup validado correspondente à execução.
3. Confirmar SHA do backup.
4. Fechar Excel e qualquer processo que possa bloquear a planilha.
5. Restaurar somente conforme procedimento operacional aprovado.
6. Reabrir a planilha restaurada em modo de leitura.
7. Recalcular SHA.
8. Registrar ocorrência no ledger.
9. Não executar nova aplicação até diagnóstico.

## Artefatos históricos principais

- `historical_equipment_backfill_apply_rules4_20260723T172020Z.json`
- `historical_equipment_backfill_apply_rules4_20260723T172020Z.md`
- `historical_equipment_backfill_apply_rules7_20260724T151254Z.json`
- `historical_equipment_backfill_apply_rules7_20260724T151254Z.md`
- `pipeline_partial_batch_validation_20260724T164641Z.json`
- `pipeline_partial_batch_validation_20260724T164641Z.md`

## Estado final

- Backfill histórico: CLOSED
- Rules-4: APPLIED/HISTORICAL
- Rules-7: APPLIED/HISTORICAL
- Automação ponta a ponta: CLOSED
- Release técnica: APPROVED
- Produção controlada: AUTHORIZED

## Hotfix v2.0.1

Em 2026-07-26 foi corrigido o controle de amplitude do lote para que
`MAX_COMPLETED_TO_PROCESS` limite globalmente protocolos únicos analisados,
incluindo PDFs baixados, PDFs reutilizados, retomadas e
`PROCESS_EXISTING_AFTER_SKIP=true`.

Estado final do hotfix:

- Código corrigido: SIM
- Testes direcionados: passed
- Ruff: passed
- Compileall: passed
- MyPy restrito aos arquivos alterados: passed
- Suíte completa: 682 passed
- Unidade `Z:` e planilha oficial: disponíveis
- SHA oficial recuperado: `b1bfedc497db8f207d900234b185c80f974ce2a0b628cb437edb19e13953c441`
- Canário real com limite 5: aprovado
- Protocolos únicos selecionados/analisados: 5/5
- PDFs baixados: 0
- PDFs reutilizados: 5
- Protocolos `NO_CHANGE`: 5
- Pendências: 0
- Updates aplicados: 0
- PDFs arquivados: 5
- Protocolos duplicados: 0
- Mudanças fora da allowlist: 0
- Erros sistêmicos: 0
- SHA oficial final: `b1bfedc497db8f207d900234b185c80f974ce2a0b628cb437edb19e13953c441`
- Tag v2.0.1: criada localmente

A produção controlada permanece autorizada em `v2.0.1`. A operação ampla
continua bloqueada até autorização operacional explícita.
