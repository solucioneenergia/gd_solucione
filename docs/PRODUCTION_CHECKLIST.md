# Checklist de produção

## Segurança

- [ ] Encerrar a sessão que estava no `storage_state.json` do ZIP original.
- [ ] Gerar uma nova sessão local e não copiá-la para o build.
- [ ] Confirmar `CDP_ENDPOINT=http://127.0.0.1:9222`.
- [ ] Manter `ALLOW_REMOTE_CDP=false`.
- [ ] Restringir acesso às pastas de logs, estado, cache e downloads.
- [ ] Definir política de retenção para dados pessoais.

## Configuração

- [ ] `APP_ENV=production`.
- [ ] `PLANILHA_PATH` aponta para o arquivo oficial correto.
- [ ] `CLIENTES_ROOT` aponta para a raiz correta da rede.
- [ ] `BACKUP_EXCEL=true`.
- [ ] Limiares fuzzy aprovados pelos usuários responsáveis.
- [ ] Limites de lote/paginação definidos para o piloto.

## Validação

- [ ] `python -m pytest -q` aprovado.
- [ ] Pré-voo aprovado.
- [ ] Simulação em cópia da planilha aprovada.
- [ ] Cinco fixtures privadas de PDF aprovadas.
- [ ] Teste de restauração de backup aprovado.
- [ ] Teste de retomada após interrupção aprovado.
- [ ] Destinos de arquivamento conferidos manualmente.

## Operação

- [ ] Primeiro lote real limitado a 3–5 protocolos.
- [ ] Conferência manual de 100% do lote piloto.
- [ ] Monitoramento de erros CDP e mudanças no HTML do portal.
- [ ] Procedimento de rollback documentado.
- [ ] Responsável operacional e responsável técnico definidos.
