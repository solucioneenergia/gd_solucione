# Relatório final de homologação técnica/offline

Data: 2026-07-19  
Projeto: `gd_neoenergia`

## Status final

Homologação técnica/offline concluída.

Produção real pendente de validação assistida pelo usuário com 1 protocolo, conforme `docs/PRODUCTION_HOMOLOGATION_CHECKLIST.md`.

## Resumo das etapas

- Etapa 1 — Auditoria e especificação: documentação do estado atual, roadmap e regra `equipment_format_v2`.
- Etapa 2 — Testes antes da implementação: testes sintéticos e contratos futuros.
- Etapa 3 — Parser e domínio de equipamentos: `EQUIPMENT_FORMAT_VERSION = 2`, parser e formatadores V2.
- Etapa 4 — Atualização da planilha: integração dos formatadores V2 ao fluxo de escrita Excel com layout preservado.
- Etapa 5 — Registros antigos e checkpoint: reformatação de registros `completed` antigos sem download e sem arquivamento.
- Etapa 6 — Progresso e erros estruturados: `ProgressEvent`, `ProgressTracker`, `OperationError`, `StepError`, `ProcessError`.
- Etapa 7 — Limpeza de temporários: cleanup seguro com dry-run/apply, allowlist e proteção contra path traversal.
- Etapa 8 — Preparação PySide6: estrutura `apps/desktop`, bridges, worker, fallback de PySide6 e compatibilidade com `desktop_app.py`.
- Etapa 9 — Validação final: regressão automatizada, compileall, validação de imports, documentação final e checklist de produção assistida.

## Arquivos principais criados/alterados ao longo das etapas

- `automacao_gd/domain/equipment.py`
- `automacao_gd/application/equipment_reformatting.py`
- `automacao_gd/application/contracts.py`
- `automacao_gd/application/use_cases/cleanup_temp.py`
- `automacao_gd/application/processing_service.py`
- `automacao_gd/application/full_pipeline.py`
- `automacao_gd/infrastructure/excel/service.py`
- `automacao_gd/infrastructure/files/cleanup.py`
- `automacao_gd/infrastructure/state/pipeline_state.py`
- `automacao_gd/presentation/operational_output.py`
- `apps/desktop/`
- `desktop_app.py`
- `tests/test_equipment_format_v2.py`
- `tests/test_equipment_reformatting.py`
- `tests/test_progress_and_errors.py`
- `tests/test_cleanup_service.py`
- `tests/test_desktop_app_structure.py`
- `tests/test_desktop_bridge.py`
- `tests/test_desktop_architecture.py`
- `tests/test_offline_homologation.py`

## Validações executadas

### Testes automatizados

```text
python -m pytest -q
293 passed, 1 skipped in 10.52s
```

Skipped:

- `tests/test_cleanup_service.py::test_cleanup_ignores_symlink_to_outside_allowed_root`
- Motivo: symlink não disponível no ambiente Windows de teste.

Xfailed:

- `0 xfailed`

### Compileall

```text
python -m compileall automacao_gd apps
OK
```

```text
python -m compileall scripts
OK
```

## Imports principais validados

Validados por `tests/test_offline_homologation.py`:

- `app`
- `desktop_app`
- `apps.desktop.main`
- `automacao_gd.presentation.cli`
- `automacao_gd.application.full_pipeline`
- `automacao_gd.application.processing_service`
- `automacao_gd.application.equipment_reformatting`
- `automacao_gd.infrastructure.excel.service`
- `automacao_gd.infrastructure.pdf.service`
- `automacao_gd.infrastructure.files.cleanup`
- `automacao_gd.infrastructure.state.pipeline_state`

## Validação Clean Architecture

Cobertura automatizada atual:

- camada `domain` não importa Playwright, openpyxl, PySide6, tkinter, infrastructure ou presentation;
- `apps/desktop` não importa diretamente Playwright, openpyxl, `cdp_service` ou `excel.service`;
- desktop chama application/use cases e bridges;
- CLI permanece importável.

## Validação Equipment Format V2

Cenários cobertos por testes sintéticos:

- módulo único compacto;
- inversor único compacto;
- múltiplos módulos com `|`, `/`, `;` e quebra de linha;
- múltiplos inversores com fabricante repetido;
- microinversor isolado;
- inversor convencional + microinversor;
- divergência fabricante/modelo com alerta;
- potência total de sistema não gravada em `Placa`/`Inversor`;
- potência comercial do modelo preservada, como `585W`.

## Validação Excel sintético

Coberto por testes com workbooks temporários:

- escrita correta de `Placa`;
- escrita correta de `Inversor`;
- `wrap_text=True`;
- `vertical="top"`;
- altura proporcional;
- cabeçalhos preservados;
- filtros preservados;
- cores preservadas;
- larguras preservadas;
- demais colunas preservadas;
- dry-run não salva;
- apply salva workbook sintético;
- workbook sintético abre após salvar.

## Validação de reformatação

Coberto por testes sintéticos:

- `completed` antigo sem `equipment_format_version` executa `reformat_existing_excel_row`;
- `equipment_format_version=1` executa reformatação;
- `equipment_format_version=2` pula como `skipped_already_v2`;
- usa metadata quando disponível;
- usa PDF local quando metadata não existe;
- pendência de conferência quando não há fonte;
- não chama download;
- não chama arquivamento;
- não cria `_v2`/`_v3`;
- não usa célula antiga como fonte;
- atualiza somente `Placa` e `Inversor`;
- atualiza state temporário para versão 2.

## Validação de progresso e erros

Coberto por testes:

- `ProgressEvent` normaliza percentuais;
- `ProgressTracker` impede regressão;
- sucesso final chega a 100;
- falha não força 100;
- `OperationError` serializável;
- `EXCEL_LOCKED`;
- `CLIENT_FOLDER_AMBIGUOUS`;
- `PDF_INVALID_SIGNATURE`;
- `DOWNLOAD_TIMEOUT`;
- `CDP_CONNECTION_FAILED`;
- saída operacional sem traceback bruto;
- mensagens sensíveis sanitizadas.

## Validação cleanup

Coberto por testes com `tmp_path`:

- dry-run lista sem apagar;
- apply remove apenas temporários permitidos;
- `.pdf`, `.xlsx`, `.xlsm`, state/cache, relatórios e backups são preservados;
- path traversal é bloqueado;
- symlink externo é ignorado com warning quando testável;
- erro de remoção é registrado e não interrompe a limpeza;
- relatório contém contadores, warnings e errors.

## Validação desktop

Coberto por testes:

- `desktop_app.py` importável;
- `desktop_app.py` chama `apps.desktop.main`;
- `apps/desktop` existe;
- bridges importáveis;
- worker importável;
- `ProgressBridge` consome `ProgressEvent`;
- produção sem confirmação textual exata não executa;
- produção com confirmação correta chama apenas runner mockado em teste;
- mensagens para UI são sanitizadas;
- Tkinter legado permanece no projeto.

## Riscos residuais

- Portal GD pode mudar layout.
- Edge/CDP depende de login manual e navegador aberto corretamente.
- Planilha real pode estar aberta ou bloqueada.
- Pastas de clientes podem ter nomes divergentes ou ambíguos.
- PDFs reais podem ter variações de layout não cobertas pelas fixtures sintéticas.
- Symlink no Windows pode não ser testável sem permissão.
- PySide6 pode não estar instalado em todas as máquinas.
- Não há Git inicializado, então rastreabilidade por `git diff`/`git status` não está disponível.
- Produção real ainda não foi executada nesta etapa.

## Limitações conhecidas

- Homologação foi offline/técnica.
- Portal GD não foi acessado.
- Planilha real não foi usada.
- `Z:\Clientes` não foi usado.
- Nenhum ZIP/release foi gerado.
- Interface PySide6 foi preparada, mas homologação visual real depende de PySide6 instalado.

## Checklist antes de produção

Usar `docs/PRODUCTION_HOMOLOGATION_CHECKLIST.md`.

## Recomendação final

Executar produção assistida com 1 protocolo somente após:

1. revisar `.env`;
2. abrir Edge com CDP;
3. autenticar manualmente no Portal GD;
4. rodar simulação limitada;
5. conferir relatórios;
6. confirmar backup da planilha;
7. confirmar produção explicitamente.
