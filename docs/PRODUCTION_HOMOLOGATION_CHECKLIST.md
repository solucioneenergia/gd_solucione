# Checklist de produção assistida

Este checklist deve ser executado manualmente pelo usuário responsável pela operação. A homologação técnica/offline foi validada por testes automatizados, mas a produção real ainda exige conferência assistida.

## Antes de qualquer produção

- [ ] Fazer backup completo da pasta do projeto.
- [ ] Confirmar que a planilha real está fechada no Excel.
- [ ] Confirmar `BACKUP_EXCEL=true`.
- [ ] Confirmar `DRY_RUN=true` para a primeira validação.
- [ ] Confirmar `CDP_MODE=true`.
- [ ] Confirmar `CDP_ENDPOINT=http://127.0.0.1:9222`.
- [ ] Confirmar `MAX_COMPLETED_TO_PROCESS=1` para o primeiro teste real controlado.
- [ ] Confirmar `MAX_PORTAL_PAGES=1` para o primeiro teste real controlado.
- [ ] Confirmar que `PLANILHA_PATH` aponta para a planilha correta.
- [ ] Confirmar que `CLIENTES_ROOT` aponta para a pasta correta de clientes.

## Preparação do Edge/CDP

- [ ] Abrir o Edge manualmente com porta CDP.
- [ ] Fazer login manual no Portal GD.
- [ ] Abrir a aba autenticada do Portal GD.
- [ ] Não fechar o Edge durante a automação.
- [ ] Rodar a opção de teste/inspeção CDP antes do pipeline.

Comando-base esperado:

```powershell
Start-Process "msedge.exe" -ArgumentList '--remote-debugging-port=9222', '--user-data-dir=<projeto>\data\edge_cdp_profile', '--no-first-run', '--no-default-browser-check'
```

## Primeira validação em simulação

- [ ] Rodar simulação com limite pequeno.
- [ ] Conferir `pipeline_cdp_completo.md`.
- [ ] Conferir `processamento_pdfs_planilha_clientes.md`.
- [ ] Confirmar que nenhuma linha real foi alterada em dry-run.
- [ ] Confirmar que nenhum PDF foi arquivado em dry-run.
- [ ] Conferir campos `placa_planilha` e `inversor_planilha`.
- [ ] Validar casos com múltiplos módulos/inversores, se aparecerem.
- [ ] Validar que dados sensíveis não aparecem no terminal.

## Produção controlada com 1 protocolo

- [ ] Alterar para produção somente após simulação aprovada.
- [ ] Manter `BACKUP_EXCEL=true`.
- [ ] Manter `MAX_COMPLETED_TO_PROCESS=1`.
- [ ] Confirmar explicitamente a produção quando solicitado.
- [ ] Conferir se o backup da planilha foi criado.
- [ ] Conferir a linha atualizada na planilha.
- [ ] Conferir `wrap_text`, alinhamento superior e altura da linha.
- [ ] Conferir se o PDF foi arquivado no destino correto.
- [ ] Conferir state/checkpoint.
- [ ] Conferir logs e relatórios.

## Liberação gradual

- [ ] Se o protocolo único estiver correto, aumentar limite com cautela.
- [ ] Validar novamente relatórios após cada lote.
- [ ] Parar a execução se houver bloqueio do Portal GD, planilha bloqueada ou pasta ambígua.
- [ ] Registrar qualquer caso divergente para ajuste posterior.

## Critério de liberação operacional

- [ ] Simulação aprovada.
- [ ] Produção controlada com 1 protocolo aprovada.
- [ ] Planilha correta visualmente.
- [ ] PDF arquivado corretamente.
- [ ] State/checkpoint coerente.
- [ ] Logs sem dados sensíveis indevidos.
