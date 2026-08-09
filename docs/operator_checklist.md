# Checklist do operador — Produção controlada

## Versão liberada

- [ ] Versão operacional confirmada: `v2.0.1`
- [ ] SHA oficial de referência conferido: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`
- [ ] Operação ampla não autorizada nesta execução

## Antes

- [ ] Unidade Z: disponível
- [ ] Planilha fechada
- [ ] Edge CDP aberto
- [ ] Endpoint 127.0.0.1:9222/json/version acessível
- [ ] Portal autenticado
- [ ] APP_ENV=production
- [ ] DRY_RUN=false
- [ ] MAX_COMPLETED_TO_PROCESS conferido
- [ ] PROCESS_EXISTING_AFTER_SKIP conferido
- [ ] Limite global entendido: protocolos analisados <= MAX_COMPLETED_TO_PROCESS
- [ ] Reconciliação global entendida: audita todos os `Concluídos`, mas não amplia o lote operacional
- [ ] Backup habilitado

## Depois

- [ ] Status final conferido
- [ ] Páginas lidas maior que zero
- [ ] Protocolos selecionados conferidos
- [ ] Protocolos analisados dentro do limite global
- [ ] Relatório `portal_workbook_reconciliation_<timestamp>` conferido
- [ ] Escopo da reconciliação conferido como GLOBAL antes de tratar métricas como definitivas
- [ ] Última página confirmada e próxima página indisponível após parada
- [ ] Ausentes, duplicados, aba anual incorreta e registros incompletos avaliados como achados de saneamento
- [ ] PDFs baixados/reutilizados conferidos
- [ ] Updates aplicados conferidos
- [ ] Pendências conferidas
- [ ] Protocolos protegidos conferidos: [PROTOCOLO REDIGIDO], [PROTOCOLO REDIGIDO], [PROTOCOLO REDIGIDO], [PROTOCOLO REDIGIDO], [PROTOCOLO REDIGIDO], [PROTOCOLO REDIGIDO]
- [ ] Nenhuma quantidade individual inferida para protocolo `SOURCE_INCOMPLETE`
- [ ] Arquivamentos conferidos
- [ ] Relatórios gerados
- [ ] Erros sistêmicos igual a zero
- [ ] Temporários residuais igual a zero

## Sincronizacao de conclusao integrada - v2.1.0

- [ ] Opcao 5 usada para o pipeline CDP completo
- [ ] Opcao 7 ausente no menu operacional
- [ ] MAX_COMPLETED_TO_PROCESS conferido para limitar o lote integrado
- [ ] Detalhes abertos para cada protocolo selecionado
- [ ] Data lida somente do bloco `Ponto de Conexao Aprovado`
- [ ] PDF reutilizado tambem abriu detalhes antes de processar
- [ ] `EM ABERTO` entendido como data de conclusao nao disponivel
- [ ] Conflitos e regressoes conferidos como `PENDING_REVIEW`
- [ ] Relatorio da opcao 5 contem metricas de conclusao
- [ ] Mudancas de `Conclusao`, `Placa` e `Inversor` aplicadas em transacao unica
- [ ] Nenhuma mudanca fora da allowlist

## Saneamento direcionado

[ ] Plano aprovado localizado
[ ] Pre-voo aprovado localizado
[ ] Hashes revalidados
[ ] SHA atual da planilha confere com a base autorizada
[ ] Protocolo alvo ainda esta ausente ou no estado esperado
[ ] Linha/celulas alvo conferidas
[ ] Confirmacao forte exata recebida
[ ] Backup criado e validado
[ ] Temporario criado no mesmo volume
[ ] Comparacao integral sem mudancas fora da allowlist
[ ] Substituicao atomica concluida
[ ] Arquivo final reaberto e validado
[ ] Idempotencia concluida
[ ] Relatorios e ledger atualizados

## Diagnostico visual e estrutural

[ ] SHA atual da planilha oficial recalculada
[ ] SHA esperada conferida antes da auditoria
[ ] Planilha aberta somente em modo read-only
[ ] Portal nao acessado
[ ] PDFs nao baixados
[ ] Backup nao criado
[ ] Temporario de aplicacao nao criado
[ ] `workbook.save` nao executado
[ ] `os.replace` nao executado
[ ] Abas auditadas: `2022 - 2023`, `2024`, `2025`, `2026`
[ ] Aba canonica registrada: `2025`
[ ] Inconsistencias da referencia canonica revisadas
[ ] Todas as acoes com `apply_now=false`
[ ] Todas as acoes com `changes_content=false`
[ ] Alteracoes de conteudo propostas igual a zero
[ ] SHA antes/depois preservada
[ ] Plano read-only conferido antes de qualquer pre-voo futuro
[ ] Final plan hash conferido: `3589c169354395bbe4964699a10acfbc69a00ce2ec1039fca575a87bae6fd670`
[ ] Canonical policy hash conferido: `e09a710cea0f29d47feb76324bcc4303173a52a48f21f97d438d900feedf0662`
[ ] `blocked_actions = 0`
[ ] `content_changes_proposed = 0`
[ ] Aplicacao visual certificada: `STAGE4_3_COMPLETE - VISUAL_STANDARDIZATION_APPLIED_AND_IDEMPOTENT`
[ ] Acoes pos-aplicacao igual a zero
[ ] Mudancas de valor logico igual a zero
[ ] Mudancas fisicas inesperadas igual a zero
[ ] SHA final certificada conferida: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`

## Inventario e manutencao segura do projeto

[ ] SHA da planilha oficial recalculada antes do inventario
[ ] Portal nao acessado
[ ] PDFs nao acessados nem baixados
[ ] `.env`, cookies, tokens e storage state nao lidos
[ ] Raizes externas registradas como protegidas
[ ] Varredura recursiva limitada ao projeto
[ ] Arquivos protegidos classificados antes de qualquer candidato
[ ] Duplicados tratados como revisao, nao exclusao automatica
[ ] Caches regeneraveis classificados como revisao futura
[ ] Todos os candidatos com `delete_now=false`
[ ] Todos os candidatos com `move_now=false`
[ ] Todos os candidatos com `archive_now=false`
[ ] Todos os candidatos com `compress_now=false`
[ ] Backup nao criado
[ ] Temporario de aplicacao nao criado
[ ] Zip/pacote compartilhavel nao criado
[ ] `workbook.save` nao executado
[ ] `os.replace` nao executado
[ ] Relatorios finais gerados em `data/logs`
[ ] Qualquer violacao read-only registrada sem apagar evidencias
[ ] Raiz canonica validada por identidade fisica
[ ] Git root usado somente como verificacao
[ ] Unicode/NFC validado
[ ] Caminho com mojibake bloqueado
[ ] `data/logs` pertence a raiz canonica e nao e symlink
[ ] Snapshot mantido somente em memoria
[ ] Nenhum snapshot fisico criado
[ ] Oito destinos finais ausentes antes da escrita
[ ] Bundle unico validado
[ ] `execution_id` unico compartilhado pelos oito relatorios
[ ] `timestamp` unico compartilhado pelos oito relatorios
[ ] `cleanup_plan_hash` reproduzido
[ ] JSON e Markdown consistentes
[ ] Escritor sem temporarios e sem `os.replace`
[ ] Referencias autoritativas separadas de fixtures
[ ] Exemplos, diretorios, placeholders e caminhos malformados nao protegem arquivos
[ ] Caches citados apenas por testes/SPEC permanecem regeneraveis

## Encerramento da Etapa 5.1C

[ ] Artefatos `stage5_1_terminal_disposition_<timestamp>.json/.md` localizados
[ ] `cleanup_plan_hash` conferido: `bccb5e1b58c8ee892685b7b39040e58bb91fafb44cb6a872b331a88a3290c9f2`
[ ] `disposition_hash` reproduzido: `2cd2e2e0b88cb5db2d2724d03c10fea610e75274e5f150a818e3428dbdae5390`
[ ] Referencias quebradas tratadas: 6/6
[ ] Arquivos unknown tratados: 52/52
[ ] Grupos duplicados tratados: 28/28
[ ] Total de achados reconciliados: 86/86
[ ] Achados sem disposicao: 0
[ ] `delete_now=true`: 0
[ ] `move_now=true`: 0
[ ] `archive_now=true`: 0
[ ] `compress_now=true`: 0
[ ] `requires_new_stage=true`: 0
[ ] Limpeza autorizada: NAO
[ ] Etapa 5.2 iniciada: NAO
[ ] Decisao 5.1C registrada: `STAGE5_1C_COMPLETE — ALL_FINDINGS_DISPOSITIONED_READ_ONLY`
[ ] Decisao 5.1 registrada: `STAGE5_1_COMPLETE — INVENTORY_AND_MANUAL_REVIEW_CLOSED_NO_CLEANUP_AUTHORIZED`

## OP-2 — Hardening operacional

[ ] Producao permanece com `MAX_COMPLETED_TO_PROCESS=5`
[ ] Requested limit menor ou igual ao authorized limit
[ ] Authorization scope confirmado
[ ] Frase forte exibida corresponde ao limite autorizado
[ ] Confirmacao generica `SIM` rejeitada para opcao 5 real
[ ] Lock global adquirido antes de recursos externos
[ ] Nenhuma execucao concorrente em andamento
[ ] Arquivo `data/locks/option5_execution.lock` interpretado como mutex ativo somente quando bloqueado pelo SO
[ ] Lote congelado criado
[ ] Protocolos adicionados apos freeze igual a zero
[ ] Duplicados no lote congelado igual a zero
[ ] PDF reutilizado consome limite
[ ] Protocolo retomado consome limite
[ ] Lote 10 nao autorizado em producao
[ ] Operacao ampla bloqueada
