# terminal_pipeline_error_stability

## Problema

A execução do pipeline pelo terminal apresentou falhas operacionais com ruído técnico excessivo:

- clique em `Orçamento de Conexão` executado, mas Playwright ficou aguardando navegação e o download não foi confirmado;
- linha `Trifásica` foi tratada como candidata de quantidade de inversores;
- planilha bloqueada gerou traceback bruto no terminal;
- planilha gravável pode falhar no `os.replace()` em unidade de rede/sincronização, gerando falso diagnóstico de planilha aberta;
- erros operacionais conhecidos apareceram com stack trace completo, dificultando a leitura do resumo.
- uma aba bloqueada em `http://.../index.jsf` pode ser confundida com aba do portal se a detecção por título/corpo não conseguir ler a página a tempo.

## Comportamento atual

- `download_connection_budget()` usa `target.click(timeout=10_000)` dentro de `expect_download`; se o botão `submit` agenda navegação, o clique pode expirar aguardando navegação mesmo após o clique ter sido feito.
- `_parse_inverter_quantity_lines()` mantém linhas de tipo de conexão, como `Trifásica`, na lista de linhas técnicas para quantidade/potência.
- `update_excel_from_pdf_data()` e `update_excel_equipment_columns()` usam `logger.exception()` para `PermissionError`, exibindo traceback bruto para um erro operacional esperado.
- `_save_workbook_atomically()` não tenta fallback quando o XLSX temporário foi validado mas a substituição atômica é negada pelo sistema operacional.
- O download de orçamento sem arquivo gerado é tratado como `RuntimeError` genérico no loop de protocolos.
- `find_portal_page_from_cdp()` ignora páginas com texto de Access Denied, mas ainda depende de leitura do título/corpo para alguns casos; a URL HTTP `/index.jsf` bloqueada deve ser rejeitada por heurística de URL.

## Comportamento esperado

- O clique que dispara download não deve aguardar navegação; o sincronizador principal deve continuar sendo `page.expect_download`.
- Linhas de tipo de conexão devem ser ignoradas ao extrair quantidade/potência de inversores.
- Planilha bloqueada deve retornar erro operacional claro sem traceback no console.
- Se `os.replace()` for negado, mas uma cópia validada para o caminho oficial for permitida, o salvamento deve concluir com warning técnico controlado.
- Se a gravação realmente for negada, a mensagem deve indicar arquivo bloqueado/permissão/sincronização, sem afirmar que Excel está aberto como única causa.
- Falha conhecida de download sem arquivo deve ser registrada como erro operacional sem traceback, preservando o erro no resultado do protocolo.
- A seleção de aba CDP deve rejeitar `http://gdneoenergiapernambuco.neoenergia.com/index.jsf` e preferir uma aba HTTPS já autenticada/listagem.

## Critérios de aceite

1. `download_connection_budget()` chama o botão de orçamento com `no_wait_after=True`.
2. Falha conhecida de download sem arquivo usa uma exceção específica ou mapeamento específico, sem `logger.exception`.
3. `Trifásica`, `Bifásica` e `Monofásica` não entram como linhas de quantidade/potência de inversores.
4. `PermissionError` no salvamento atômico da planilha registra `logger.error`, não `logger.exception`.
5. `PermissionError` em `os.replace()` tenta fallback por cópia validada antes de declarar falha.
6. Falha definitiva de salvamento usa mensagem operacional precisa, citando bloqueio/permissão/sincronização.
7. O pipeline continua registrando o erro no resumo do protocolo e segue para os próximos itens.
8. Aba CDP com URL HTTP `/index.jsf` do Portal GD é ignorada mesmo se título/corpo não forem legíveis.
9. Nenhum teste acessa Portal GD, Edge real, planilha real, `Z:\Clientes` ou dados reais.

## Casos de teste

- Download: fake `page.expect_download` e fake target validam que `click()` recebe `no_wait_after=True`.
- Download: erro conhecido sem download é registrado por `logger.error` e não por `logger.exception`.
- PDF: linhas de tipo de conexão são filtradas antes da extração de quantidade de inversores.
- Excel: `PermissionError` em `_save_workbook_atomically()` retorna mensagem operacional e não chama `logger.exception`.
- Excel: `PermissionError` em `os.replace()` com cópia permitida salva a planilha e valida o arquivo final.
- Paginação: divergência de página ativa após clique numérico é warning operacional.
- CDP: aba `http://.../index.jsf` bloqueada é ignorada na seleção de aba ativa.

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
