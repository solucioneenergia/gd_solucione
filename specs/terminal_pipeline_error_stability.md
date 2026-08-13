# terminal_pipeline_error_stability

## Problema

A execução do pipeline pelo terminal apresentou falhas operacionais com ruído técnico excessivo:

- clique em `Orçamento de Conexão` executado, mas Playwright ficou aguardando navegação e o download não foi confirmado;
- linha `Trifásica` foi tratada como candidata de quantidade de inversores;
- planilha bloqueada gerou traceback bruto no terminal;
- planilha gravável pode falhar no `os.replace()` em unidade de rede/sincronização, gerando falso diagnóstico de planilha aberta;
- erros operacionais conhecidos apareceram com stack trace completo, dificultando a leitura do resumo.
- uma aba bloqueada em `http://...` pode ser confundida com aba do portal se a detecção por título/corpo não conseguir ler a página a tempo.

## Comportamento atual

- `download_connection_budget()` usa `target.click(timeout=10_000)` dentro de `expect_download`; se o botão `submit` agenda navegação, o clique pode expirar aguardando navegação mesmo após o clique ter sido feito.
- `_parse_inverter_quantity_lines()` mantém linhas de tipo de conexão, como `Trifásica`, na lista de linhas técnicas para quantidade/potência.
- `update_excel_from_pdf_data()` e `update_excel_equipment_columns()` usam `logger.exception()` para `PermissionError`, exibindo traceback bruto para um erro operacional esperado.
- `_save_workbook_atomically()` não tenta fallback quando o XLSX temporário foi validado mas a substituição atômica é negada pelo sistema operacional.
- O download de orçamento sem arquivo gerado é tratado como `RuntimeError` genérico no loop de protocolos.
- `find_portal_page_from_cdp()` ignora páginas com texto de Access Denied, mas ainda depende de leitura do título/corpo para alguns casos; qualquer URL HTTP do host do Portal GD deve ser rejeitada por heurística de URL.

## Comportamento esperado

- O clique que dispara download não deve aguardar navegação; o sincronizador principal deve continuar sendo `page.expect_download`.
- Linhas de tipo de conexão devem ser ignoradas ao extrair quantidade/potência de inversores.
- Planilha bloqueada deve retornar erro operacional claro sem traceback no console.
- Se `os.replace()` for negado, mas uma cópia validada para o caminho oficial for permitida, o salvamento deve concluir com warning técnico controlado.
- Se a gravação realmente for negada, a mensagem deve indicar arquivo bloqueado/permissão/sincronização, sem afirmar que Excel está aberto como única causa.
- Falha conhecida de download sem arquivo deve ser registrada como erro operacional sem traceback, preservando o erro no resultado do protocolo.
- A seleção de aba CDP deve rejeitar qualquer `http://gdneoenergiapernambuco.neoenergia.com/...` e preferir uma aba HTTPS já autenticada/listagem.
- Quando o Edge conectado tiver somente abas HTTP/bloqueadas do Portal GD, o modo CDP deve falhar
  fechado. A automação não pode abrir nova aba nem tentar autenticar/navegar automaticamente para a
  URL raiz do Portal; o operador deve abrir o Edge via comando PowerShell aprovado e autenticar
  manualmente.

## Critérios de aceite

1. `download_connection_budget()` chama o botão de orçamento com `no_wait_after=True`.
2. Falha conhecida de download sem arquivo usa uma exceção específica ou mapeamento específico, sem `logger.exception`.
3. `Trifásica`, `Bifásica` e `Monofásica` não entram como linhas de quantidade/potência de inversores.
4. `PermissionError` no salvamento atômico da planilha registra `logger.error`, não `logger.exception`.
5. `PermissionError` em `os.replace()` tenta fallback por cópia validada antes de declarar falha.
6. Falha definitiva de salvamento usa mensagem operacional precisa, citando bloqueio/permissão/sincronização.
7. O pipeline continua registrando o erro no resumo do protocolo e segue para os próximos itens.
8. Aba CDP com URL HTTP do Portal GD é ignorada mesmo se título/corpo não forem legíveis.
9. CDP falha fechado quando somente abas HTTP/bloqueadas do Portal GD estão disponíveis, sem abrir
   nova aba.
10. Nenhum teste acessa Portal GD, Edge real, planilha real, `Z:\Clientes` ou dados reais.

## Casos de teste

- Download: fake `page.expect_download` e fake target validam que `click()` recebe `no_wait_after=True`.
- Download: erro conhecido sem download é registrado por `logger.error` e não por `logger.exception`.
- PDF: linhas de tipo de conexão são filtradas antes da extração de quantidade de inversores.
- Excel: `PermissionError` em `_save_workbook_atomically()` retorna mensagem operacional e não chama `logger.exception`.
- Excel: `PermissionError` em `os.replace()` com cópia permitida salva a planilha e valida o arquivo final.
- Paginação: divergência de página ativa após clique numérico é warning operacional.
- CDP: aba `http://...` bloqueada é ignorada na seleção de aba ativa.
- CDP: contexto fake com apenas aba HTTP/bloqueada não recebe `new_page()` nem `goto(PORTAL_GD_URL)`.

## Arquivos permitidos

- `automacao_gd/infrastructure/portal/cdp_service.py`
- `automacao_gd/infrastructure/pdf/service.py`
- `automacao_gd/infrastructure/excel/service.py`
- `tests/test_full_cdp_pipeline.py`
- `tests/test_pdf_service.py`
- `tests/test_excel_service.py`
- `specs/terminal_pipeline_error_stability.md`

## Riscos

- Alterar o clique CDP pode afetar botões que realmente dependam de navegação. Mitigação: aplicar `no_wait_after=True` apenas no botão de orçamento que é esperado gerar download.
- Filtrar linhas de tipo de conexão pode esconder uma variação textual inesperada. Mitigação: filtrar apenas termos exatos conhecidos de tipo de conexão.
- Reduzir traceback de erro operacional pode esconder diagnóstico. Mitigação: manter traceback para exceções desconhecidas.

## Rollback

Reverter as alterações nos três módulos de infraestrutura e remover os testes adicionados nesta especificação.

## Adendo 2026-08-09 — substituição atômica sem fallback de cópia

Este adendo, aprovado pela SPEC-009 e pela ADR 0006, substitui o requisito anterior de fallback
por cópia quando `os.replace()` for negado.

- O arquivo oficial deve permanecer intacto.
- O temporário validado deve ser removido.
- A operação deve falhar com código `WORKBOOK_ATOMIC_REPLACE_DENIED` e mensagem operacional.
- Não é permitido copiar diretamente sobre o arquivo oficial, pois isso viola a atomicidade da
  ADR 0002 e cria uma janela de corrupção.
- O operador deve fechar bloqueios, verificar permissão/sincronização e retomar a operação.

## Adendo 2026-07-24 — isolamento de pendencias tecnicas por protocolo

### Problema

O pipeline CDP completo bloqueou a gravacao de todo o lote quando a simulacao de seguranca encontrou protocolos com pendencia tecnica individual, como `MODULE_QUANTITY_MISSING` e `CANONICAL_STRUCTURE_INCOMPLETE`. Essa pendencia nao representa falha sistemica da planilha, do ambiente, do Portal ou da transacao de Excel.

### Comportamento esperado

- Pendencia tecnica individual bloqueia apenas o protocolo correspondente.
- Protocolos seguros do mesmo lote continuam para aplicacao.
- Protocolos `NO_CHANGE` continuam sem gravacao e podem seguir a politica existente de arquivamento seguro.
- Erros sistemicos continuam bloqueando todo o lote antes da escrita ou acionando rollback quando ocorrerem depois do inicio da aplicacao.
- Se nao houver nenhum protocolo seguro para aplicar, a execucao deve ficar `BLOQUEADO` com `NO_SAFE_PROTOCOLS_TO_APPLY`, sem criar backup.
- O subconjunto seguro continua atomico: falha sistemica durante a aplicacao restaura o backup e zera os updates aplicados no relatorio.
- Os relatorios devem distinguir PDFs analisados, protocolos seguros, protocolos pendentes, protocolos sem alteracao, updates planejados, updates aplicados e PDFs mantidos para retomada.

### Criterios de aceite

1. Dado um lote com 1 protocolo seguro e 1 protocolo com `MODULE_QUANTITY_MISSING`, entao o seguro e aplicado, o pendente nao e aplicado e o status final e `PARCIAL`.
2. Dado um lote com apenas protocolos pendentes, entao `total_excel_updated = 0`, status `BLOQUEADO` e codigo `NO_SAFE_PROTOCOLS_TO_APPLY`.
3. Dado erro sistemico de pre-voo como `WORKBOOK_LOCKED`, entao nenhum PDF e processado para escrita.
4. Dada falha sistemica depois de aplicacao parcial do subconjunto seguro, entao o backup e restaurado e o relatorio nao declara update aplicado.
5. Dado `NO_SAFE_PROTOCOLS_TO_APPLY` no processamento, entao o status agregado do pipeline permanece `BLOQUEADO`, mesmo se PDFs foram baixados ou reutilizados.

### Casos de teste

- `test_critical_simulation_issues_ignores_individual_technical_pending`
- `test_real_run_technical_pending_does_not_block_safe_protocol`
- `test_real_run_only_pending_protocols_blocks_without_backup`
- `test_real_run_systemic_preflight_error_blocks_entire_batch`
- `test_real_run_systemic_failure_rolls_back_safe_subset`
- `test_processing_metrics_distinguish_analyzed_safe_pending_and_applied`
- `test_pipeline_no_safe_protocols_to_apply_remains_blocked`

## Adendo 2026-07-26 - hotfix v2.0.1 / limite global do lote

### Adendo 2026-08-09 - metricas consolidadas do canario

Os totais consolidados devem separar: `total_pdfs_analyzed`,
`total_technically_approved`, `total_pending_review`, `total_excel_already_updated`,
`total_updates_planned`, `total_updates_applied`, `total_blocked_by_batch_policy`,
`total_real_extraction_errors`, `total_real_application_errors` e `total_errors`.
Protocolos `skipped_excel_already_updated` nao sao erro real. Protocolos tecnicamente aprovados,
mas nao aplicados por politica de lote, contam em `total_blocked_by_batch_policy`, nao em erro de
extracao. `total_errors` agrega somente pendencias tecnicas e erros reais de extracao/aplicacao
para fins de status operacional; bloqueio de politica de lote permanece contado separadamente.

### Problema

Uma execucao de producao controlada com reutilizacao de PDFs existentes analisou
mais protocolos do que o limite operacional pretendido para a rodada. O pipeline
mantinha o isolamento de pendencias individuais, mas nao havia uma guarda final
que limitasse o conjunto unico efetivamente enviado ao processamento apos juntar
PDFs baixados, PDFs reutilizados e retomadas.

### Comportamento esperado

- `MAX_COMPLETED_TO_PROCESS` deve limitar globalmente a quantidade de protocolos
  unicos analisados em uma execucao de producao controlada.
- O limite global deve considerar conjuntamente protocolos novos do Portal,
  retomados e PDFs locais reutilizados por `PROCESS_EXISTING_AFTER_SKIP=true`.
- Nenhuma fase posterior pode adicionar `process_pdf_path` alem do limite global.
- O mesmo protocolo presente em mais de uma origem deve ser analisado uma unica vez.
- Protocolos pendentes consomem o limite, mas continuam bloqueando apenas a si
  proprios.
- Os relatorios devem separar protocolos encontrados, candidatos, selecionados
  pelo limite global, PDFs baixados, PDFs reutilizados, PDFs analisados,
  protocolos seguros, `NO_CHANGE`, pendentes, falhos, updates planejados,
  updates aplicados e PDFs arquivados.
- Pendencias tecnicas classificadas na simulacao nao devem gerar warnings
  indistinguiveis duplicados na fase real; o relatorio estruturado deve manter
  rastreabilidade suficiente por protocolo.

### Criterios de aceite

1. Dado 10 protocolos do Portal e 10 PDFs locais reutilizaveis com limite global
   5, entao no maximo 5 protocolos unicos sao analisados.
2. Dado o mesmo protocolo no Portal e nos downloads locais, entao ele e enviado
   ao processamento uma unica vez.
3. Dado protocolo retomado com PDF local valido, entao ele tambem consome o limite
   global.
4. Dado protocolo pendente dentro do limite, entao ele nao e aplicado, mas tambem
   nao bloqueia protocolo seguro do mesmo subconjunto.
5. Dado warning de pendencia tecnica, entao o console/resultado operacional nao
   deve emitir duplicidade indistinguivel de simulacao e aplicacao.
6. Dadas as metricas finais, entao
   `protocolos_selecionados_pelo_limite_global = seguros + no_change + pendentes + falhos`.

### Casos de teste

- `test_global_protocol_limit_caps_existing_pdfs_after_skip`
- `test_global_protocol_limit_deduplicates_same_protocol`
- `test_global_protocol_limit_counts_resumed_protocols`
- `test_global_limit_metrics_close_with_processing_categories`
- `test_real_run_reuses_pending_simulation_result_without_second_warning`

## Adendo 2026-08-11 - CDP attach-only sem navegacao HTTP

### Problema

O retorno a listagem durante `op5-plan` ainda podia tentar navegar, recarregar ou voltar no
historico quando a pagina atual ou a `listing_url` capturada apontavam para
`http://gdneoenergiapernambuco.neoenergia.com/...`. Esse caminho pode recriar a tela de
`Access Denied` observada no Edge.

### Contrato

- O modo CDP permanece estritamente attach-only.
- A selecao inicial da aba CDP deve ser passiva por URL; nao deve ler titulo, body ou DOM da aba
  do Portal antes de escolher a pagina.
- Se a pagina atual ou a `listing_url` salva forem HTTP do Portal GD, a automacao deve falhar
  fechada antes de chamar `goto()`, `reload()` ou `go_back()`.
- Se a aba HTTPS inicialmente valida virar `Access Denied` depois da conexao CDP, a automacao
  deve abortar antes de resetar paginacao, ler tabela, baixar PDF ou manter espera longa.
- A mensagem operacional deve orientar o operador a reabrir o Edge pelo comando PowerShell
  aprovado, fazer login manual e deixar a listagem aberta.
- A automacao nao pode abrir nova aba, navegar para a raiz do Portal nem tentar autenticar.

### Testes

- `test_recover_listing_does_not_navigate_when_current_page_is_http_access_denied`
- `test_recover_listing_does_not_goto_http_listing_url`
- `test_download_step_aborts_before_pagination_when_portal_turns_access_denied`
- `test_find_portal_page_selects_https_portal_tab_without_dom_probe`

## Adendo 2026-08-13 - logging terminal sem rotacao automatica compartilhada

### Problema

Em Windows, mais de um processo `app.py` pode manter `data/logs/app.log` aberto ao mesmo
tempo. Quando o Loguru tenta rotacionar esse arquivo compartilhado, a chamada de rename pode
falhar com `PermissionError WinError 32`. Esse erro e exclusivamente de logging e nao pode
invalidar o fechamento de um `op5-plan` cujo processamento principal ja formou lote aprovado.

### Contrato

- O sink persistente `app.log` deve continuar sanitizado e gravado em modo append.
- O sink persistente `app.log` nao deve configurar `rotation` automatica.
- Falhas de rotacao de log nao podem transformar uma operacao principal bem-sucedida em
  `PARCIAL`, nem deixar `op5_plan_latest.json` stale.
- Limpeza, retencao e auditoria historica de logs permanecem fluxo separado e explicito.
- A correcao nao altera CDP, Portal, Excel, download, arquivamento ou `op5-apply`.

### Teste

- `test_persistent_app_log_does_not_configure_windows_unsafe_rotation`

## Adendo 2026-08-13 - total_pending_review nao mistura pasta de cliente

### Problema

Um `op5-plan` pode formar um lote com todos os resultados individuais aprovados, conclusao
extraida e pasta/arquivamento resolvidos, mas o relatorio agregado ainda marcar
`total_pending_review=1` e `total_errors=1`. Isso invalida o plano apesar de nao existir
protocolo pendente no resultado individual.

### Contrato

- `total_pending_review` deve contar apenas protocolos tecnicamente pendentes ou que exigem
  revisao operacional individual antes de Excel.
- Pendencias de pasta/arquivamento devem usar contador separado
  `total_client_folder_pending_review`.
- `total_errors` nao deve incluir pendencia de pasta quando o item esta tecnicamente aprovado,
  sem erro real de extracao/aplicacao e com arquivamento simulado/aplicavel.
- O resumo consolidado deve ser derivavel dos resultados individuais sanitizados.

### Teste

- `test_processing_metrics_do_not_count_resolved_client_folder_as_pending_review`
