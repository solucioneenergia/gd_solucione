# Release v2.0.1 — Hotfix do limite global de lote

## Identidade

- Versão: `v2.0.1`
- Data: 2026-07-27
- Commit: `070a4206b017b51f142919e6e98a0b290e0c8953`
- Tag: `v2.0.1`
- Release técnica: APPROVED
- Produção controlada: AUTHORIZED
- Operação ampla: BLOCKED UNTIL EXPLICIT AUTHORIZATION

## Objetivo da release

Liberar o hotfix que garante que `MAX_COMPLETED_TO_PROCESS` seja aplicado como
limite global de protocolos únicos analisados em produção controlada, inclusive
quando `PROCESS_EXISTING_AFTER_SKIP=true` adiciona PDFs locais reutilizáveis ou
protocolos retomados ao fluxo.

## SHA oficial da planilha

- SHA oficial atual: `b1bfedc497db8f207d900234b185c80f974ce2a0b628cb437edb19e13953c441`
- SHA anterior histórico da v2.0.0: `c86c97a1c31f58e[PROTOCOLO REDIGIDO]f91e79941bf8b5ede8fcac715c3fffc0bca973e`
- Estado explicado: SIM
- Observação: a baseline atual foi recuperada por reconciliação operacional da execução que aplicou 11 updates legítimos, pois aquele relatório não continha SHA final. O canário final do hotfix não aplicou updates e preservou o SHA.

## Correção do limite global

Contrato aprovado:

```text
reunir candidatos
→ aplicar regras de retomada e skip
→ deduplicar por protocolo
→ ordenar deterministicamente
→ aplicar limite global
→ processar
```

O limite global considera conjuntamente:

- protocolos novos do Portal;
- PDFs baixados;
- PDFs reutilizados;
- protocolos retomados;
- itens adicionados por `PROCESS_EXISTING_AFTER_SKIP=true`.

Nenhuma fase posterior pode adicionar protocolos acima do limite.

## Cronologia da revalidação

| Tentativa | Resultado | Causa |
| --- | --- | --- |
| 1 | HOTFIX_REJECTED | NETWORK_DRIVE_UNAVAILABLE |
| 2 | HOTFIX_REVALIDATION_BLOCKED | CDP_UNAVAILABLE |
| 3 | HOTFIX_RELEASED | canário real aprovado com limite global |

## Quality gates

- Suíte completa: 682 passed
- Ruff: passed
- Compileall: passed
- MyPy dos arquivos alterados: passed
- Smoke de inicialização do menu sem executar pipeline: passed

## Canário real v2.0.1

Configuração validada:

```env
APP_ENV=production
DRY_RUN=false
APPLY_EXCEL=true
APPLY_ARCHIVE=true
MAX_COMPLETED_TO_PROCESS=5
PROCESS_EXISTING_AFTER_SKIP=true
RESUME_PIPELINE=true
SKIP_ALREADY_COMPLETED=true
RESET_PIPELINE_STATE=false
ENABLE_PORTAL_PAGINATION=true
MAX_PORTAL_PAGES=11
```

Resultado:

| Métrica | Valor |
| --- | ---: |
| Páginas lidas | 11 |
| Solicitações concluídas elegíveis | 462 |
| Protocolos únicos antes do limite | 462 |
| Protocolos selecionados pelo limite global | 5 |
| Protocolos excluídos pelo limite global | 457 |
| Protocolos únicos analisados | 5 |
| Protocolos duplicados | 0 |
| PDFs baixados | 0 |
| PDFs reutilizados | 5 |
| PDFs analisados | 5 |
| PDFs tecnicamente aprovados | 5 |
| Protocolos seguros | 0 |
| Protocolos `NO_CHANGE` | 5 |
| Protocolos pendentes | 0 |
| Protocolos falhos | 0 |
| Updates aplicados | 0 |
| PDFs arquivados | 5 |
| Mudanças fora da allowlist | 0 |
| Erros sistêmicos | 0 |

Fórmula da métrica de exclusão:

```text
protocols_dropped_by_global_limit
= protocols_unique_before_limit - protocols_selected_by_global_limit
= 462 - 5
= 457
```

## Pendências protegidas

Os protocolos abaixo permanecem `PENDING_REVIEW/SOURCE_INCOMPLETE`:

- `[PROTOCOLO REDIGIDO]`
- `[PROTOCOLO REDIGIDO]`
- `[PROTOCOLO REDIGIDO]`
- `[PROTOCOLO REDIGIDO]`
- `[PROTOCOLO REDIGIDO]`
- `[PROTOCOLO REDIGIDO]`

Motivo vinculante:

```text
A quantidade total aparece no documento, mas a quantidade individual por modelo
ou fabricante não está documentalmente separada.
```

Regras:

- não inferir quantidade individual por modelo ou fabricante;
- não modificar automaticamente o Excel;
- não marcar como concluído;
- não arquivar como processado com sucesso;
- manter disponível para conferência;
- não classificar como `PARSER_GAP` sem nova evidência documental.

## Proteções preservadas

- Backfill histórico: CLOSED
- Rules-4: APPLIED/HISTORICAL
- Rules-7: APPLIED/HISTORICAL
- Parser V6: sem alteração nesta release
- Validador V7: sem alteração nesta release
- Isolamento de pendências por protocolo: preservado
- Erro sistêmico bloqueia o lote inteiro
- Pendência técnica bloqueia somente o protocolo
- Aplicação Excel precede arquivamento para protocolos seguros
- Operação ampla: bloqueada até autorização explícita

## Artefatos principais

- `production_batch_limit_hotfix_20260726T230458Z.json`
- `production_batch_limit_hotfix_20260726T230458Z.md`
- `production_batch_limit_hotfix_revalidation_20260727T122842Z.json`
- `production_batch_limit_hotfix_revalidation_20260727T122842Z.md`
- `production_batch_limit_hotfix_revalidation_20260727T123825Z.json`
- `production_batch_limit_hotfix_revalidation_20260727T123825Z.md`

## Decisão

`HOTFIX_RELEASED — PRODUÇÃO CONTROLADA LIBERADA EM v2.0.1`
