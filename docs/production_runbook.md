# Runbook de produção controlada — Automação GD Neoenergia

## Preparação

1. Verificar unidade Z: disponível.
2. Confirmar que a planilha não está aberta.
3. Abrir Edge com CDP.
4. Fazer login manual no Portal GD.
5. Confirmar endpoint `127.0.0.1:9222/json/version`.
6. Ativar ambiente virtual.
7. Confirmar `APP_ENV=production`.

## Configuração inicial

```env
APP_ENV=production
DRY_RUN=false
APPLY_EXCEL=true
APPLY_ARCHIVE=true
RESUME_PIPELINE=true
SKIP_ALREADY_COMPLETED=true
RESET_PIPELINE_STATE=false
MAX_COMPLETED_TO_PROCESS=5
```

## Execução

```text
python app.py
selecionar opção 5
confirmar com SIM
```

## Resultado esperado

```text
Status: SUCESSO ou PARCIAL
Páginas lidas > 0
Erros sistêmicos = 0
Mudanças fora da allowlist = 0
```

## Condições de interrupção

Interromper e diagnosticar se ocorrer:

```text
ENVIRONMENT_NOT_PRODUCTION
WORKBOOK_LOCKED
NETWORK_DRIVE_UNAVAILABLE
BACKUP_VALIDATION_FAILED
UNEXPECTED_WORKBOOK_CHANGE
ATOMIC_REPLACE_FAILED
ROLLBACK_FAILED
```

## Pendências individuais

As pendências abaixo bloqueiam somente o protocolo correspondente e não devem bloquear protocolos seguros do mesmo lote:

```text
MODULE_QUANTITY_MISSING
INVERTER_QUANTITY_MISSING
SOURCE_INCOMPLETE
SOURCE_AMBIGUOUS
```

Regras:

- não inferir quantidade;
- não atualizar Excel para protocolo pendente;
- não arquivar como concluído;
- manter o PDF disponível para retomada ou conferência;
- registrar a pendência no relatório.

## Retomada

Usar:

```env
RESUME_PIPELINE=true
SKIP_ALREADY_COMPLETED=true
RESET_PIPELINE_STATE=false
```

Não usar `RESET_PIPELINE_STATE=true` em produção controlada sem autorização operacional explícita.

## Rollback

1. Identificar backup validado.
2. Confirmar SHA do backup.
3. Fechar Excel.
4. Restaurar somente conforme procedimento operacional aprovado.
5. Registrar ocorrência no ledger.
6. Não executar nova aplicação até diagnóstico.

## Implantação gradual

### Fase 1

```env
MAX_COMPLETED_TO_PROCESS=5
```

### Fase 2

```env
MAX_COMPLETED_TO_PROCESS=10
```

### Fase 3

Limite superior somente com autorização operacional explícita.

### Critério para progressão

- nenhum P0;
- nenhum P1;
- nenhuma mudança fora da allowlist;
- nenhum rollback;
- relatórios coerentes;
- pendências individuais isoladas.

Não alterar automaticamente o limite durante uma execução.

## P3 conhecido

Execução não interativa do menu pode gerar EOF após o pipeline quando entradas extras são enviadas por pipe.

- Classificação: P3.
- Impacto: sem impacto no uso interativo normal.
- Risco de dados: nenhum identificado.
- Ação: backlog; não bloqueia produção controlada.
