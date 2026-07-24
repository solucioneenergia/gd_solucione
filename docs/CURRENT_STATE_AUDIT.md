# Auditoria do estado atual — gd_neoenergia

Data da auditoria: 2026-07-17

Escopo: Etapa 1 — Auditoria e Especificação. Este documento descreve o estado atual observado no projeto e recomenda correções futuras. Nenhuma regra funcional deve ser considerada implementada por este documento.

## 1. Visão geral

O projeto `gd_neoenergia` é uma automação Python para consultar o Portal GD da Neoenergia, baixar orçamentos de conexão, extrair dados técnicos de PDFs, atualizar uma planilha Excel operacional e arquivar PDFs na pasta do cliente. O projeto já possui separação inicial em camadas e uma suíte de testes automatizados relevante.

O fluxo operacional principal é:

1. CLI em `app.py` chama `automacao_gd.presentation.cli`.
2. A apresentação usa `ApplicationController`.
3. Casos de uso em `automacao_gd.application.use_cases` executam pré-voo, inspeção, processamento offline ou pipeline completo.
4. Infraestrutura acessa Portal GD via CDP, extrai PDF, atualiza Excel, localiza pastas de clientes e persiste state/cache/relatórios.
5. Saída operacional é formatada para terminal e relatórios JSON/Markdown.

Há também uma primeira base de interface desktop web/PySide6 em `desktop_app.py`, `automacao_gd/presentation/desktop` e `frontend/`. Para a evolução futura, a recomendação é isolar essa interface em uma estrutura própria dentro do mesmo projeto, sem duplicar backend.

## 2. Mapa de pastas e responsabilidades

| Caminho | Responsabilidade atual |
|---|---|
| `app.py` | Entrada CLI principal. |
| `desktop_app.py` | Entrada atual da interface desktop PySide6. |
| `automacao_gd/domain` | Modelos de domínio, entidades técnicas, formatação de equipamentos, erros semânticos. |
| `automacao_gd/application` | Orquestração do pipeline, pré-voo, processamento offline, casos de uso. |
| `automacao_gd/infrastructure` | Adaptadores concretos: Portal/CDP, PDF, Excel, arquivos, state, metadata, persistência atômica, configuração. |
| `automacao_gd/presentation` | CLI, controller, saída operacional, Tkinter legado e base desktop PySide6. |
| `scripts` | Ferramentas operacionais e validações manuais: inspeção do portal, processamento, migração, release, validações. |
| `tests` | Testes automatizados de pipeline, Excel, PDF, CDP, state, desktop, release, persistência. |
| `docs` | Documentação técnica, operação CDP, release, migração, arquitetura. |
| `frontend` | Frontend React/Vite da interface desktop visual. |
| `data` | Dados operacionais locais: downloads, logs, cache, state, perfis e artefatos. Não deve entrar em release limpo. |
| `src` | Compatibilidade/aliases para módulos antigos. |

## 3. Camadas existentes

### Domain

Arquivos principais:

- `automacao_gd/domain/models.py`
- `automacao_gd/domain/errors.py`

Responsabilidades atuais:

- Entidades como `PortalSolicitation`, `GenerationData`, `ModuleEquipment`, `InverterEquipment`.
- Funções de split/pareamento/formatação de equipamentos.
- Representações de resultado de arquivamento e busca de pasta.

Avaliação: a camada existe e não depende diretamente de Playwright, openpyxl, Tkinter ou filesystem. Isso está aderente à Clean Architecture. Porém, parte da formatação específica de Excel/planilha ainda está dentro de `GenerationData`, o que tende a misturar domínio com apresentação de persistência.

### Application

Arquivos principais:

- `automacao_gd/application/full_pipeline.py`
- `automacao_gd/application/processing_service.py`
- `automacao_gd/application/preflight.py`
- `automacao_gd/application/use_cases/*.py`
- `automacao_gd/application/contracts.py`

Responsabilidades atuais:

- Pré-voo.
- Orquestração do pipeline completo.
- Processamento de PDFs baixados.
- Decisão de simulação vs produção.
- Chamada dos serviços concretos de PDF, Excel, arquivos, state e Portal.

Avaliação: a camada centraliza regras de fluxo, mas ainda chama infraestrutura diretamente. Isso é aceitável no estágio atual operacional, porém não é Clean Architecture estrita, porque a aplicação depende de detalhes concretos em vez de portas/interfaces.

### Infrastructure

Arquivos principais:

- `automacao_gd/infrastructure/portal/cdp_service.py`
- `automacao_gd/infrastructure/portal/browser.py`
- `automacao_gd/infrastructure/portal/cdp_browser.py`
- `automacao_gd/infrastructure/portal/factory.py`
- `automacao_gd/infrastructure/pdf/service.py`
- `automacao_gd/infrastructure/excel/service.py`
- `automacao_gd/infrastructure/files/client_folder_service.py`
- `automacao_gd/infrastructure/state/pipeline_state.py`
- `automacao_gd/infrastructure/persistence/atomic.py`
- `automacao_gd/infrastructure/config.py`

Responsabilidades atuais:

- CDP/Playwright.
- Extração e validação de PDF.
- Manipulação de Excel com openpyxl.
- Busca e arquivamento em pastas de clientes.
- Checkpoint/state.
- Escrita atômica de JSON/texto/cópia.

Avaliação: a infraestrutura concentra adaptadores reais e protege parte da persistência com escrita atômica. Há arquivos grandes com múltiplas responsabilidades que devem ser quebrados futuramente.

### Presentation

Arquivos principais:

- `automacao_gd/presentation/cli.py`
- `automacao_gd/presentation/controller.py`
- `automacao_gd/presentation/operational_output.py`
- `automacao_gd/presentation/tkinter_app.py`
- `automacao_gd/presentation/desktop/*`

Responsabilidades atuais:

- Menu CLI.
- Formatação/sanitização da saída operacional.
- Controller para acionar casos de uso.
- Tkinter legado.
- Base desktop PySide6/QWebEngine/QWebChannel.

Avaliação: a apresentação já evita imprimir JSON bruto na saída principal e tem controller. Porém, a bridge desktop atual ainda conhece detalhes de CDP/factory e caminhos, o que deve ser isolado futuramente.

### Scripts

Responsabilidades atuais:

- Inspeção de portal.
- Download CDP.
- Validação de PDF/Excel.
- Migração operacional.
- Empacotamento e validação de release.
- Testes manuais de extração e pasta de cliente.

Avaliação: os scripts são úteis operacionalmente, mas alguns duplicam lógica antiga de navegação/extração que deveria ser consumida por casos de uso ou serviços consolidados.

### Tests

Responsabilidades atuais:

- Cobertura de parser PDF, Excel, pipeline, state, CDP, saída operacional, release, desktop e persistência atômica.

Avaliação: há boa base para TDD incremental. Para a próxima etapa, os testes devem ser escritos antes da implementação da nova regra `equipment_format_v2`.

### Docs

Responsabilidades atuais:

- Arquitetura, CDP, desktop web UI, migração, release, checklist de produção, QA e ADRs.

Avaliação: há documentação operacional relevante. Falta uma especificação formal versionada para a regra futura de equipamentos e um roadmap granular com responsabilidades.

## 4. Aderência à Clean Architecture

### Pontos corretos

- `domain` não importa Playwright, openpyxl, Tkinter ou filesystem.
- Existem casos de uso em `application/use_cases`.
- `presentation` chama `ApplicationController` em vez de chamar diretamente boa parte da infraestrutura.
- `infrastructure/portal/factory.py` centraliza a escolha CDP vs browser persistente.
- Persistência atômica está isolada em `infrastructure/persistence/atomic.py`.
- A compatibilidade legada está documentada em `src/`.
- Testes cobrem comportamento operacional e reduzem risco de regressão.

### Pontos de acoplamento indevido

| Problema | Impacto | Severidade | Arquivo provável | Recomendação futura | Etapa do roadmap |
|---|---|---:|---|---|---|
| `application` importa serviços concretos de infraestrutura diretamente. | Dificulta testes por contrato, troca de adaptadores e isolamento da futura UI. | Média | `application/full_pipeline.py`, `application/processing_service.py`, `application/use_cases/*.py` | Introduzir portas/interfaces para Portal, PDF, Excel, arquivos e state. | Etapa 6 / Etapa 8 |
| `PipelineStateStore` valida Excel chamando `validate_excel_protocol_updated`. | State fica acoplado ao Excel e a openpyxl indiretamente. | Média | `infrastructure/state/pipeline_state.py` | Separar validação de artefatos em serviço de aplicação ou porta. | Etapa 5 |
| `domain.models.GenerationData` contém métodos específicos de planilha. | Mistura entidade de domínio com formato de persistência Excel. | Média | `domain/models.py` | Extrair formatadores para serviço de domínio puro ou `application/equipment_formatting.py`. | Etapa 3 |
| `full_pipeline.py` importa saída de apresentação dentro de `main`. | Camada de aplicação fica parcialmente consciente da apresentação. | Baixa | `application/full_pipeline.py` | Mover `main` operacional para presentation/script e manter aplicação sem print. | Etapa 6 |
| Bridge desktop chama factory CDP e manipula abertura do Edge. | Risco de UI depender de infraestrutura concreta e dificultar testes. | Média | `presentation/desktop/web_bridge.py` | Criar app desktop isolado que chama apenas casos de uso e serviços de aplicação. | Etapa 8 |
| Scripts duplicam lógica operacional. | Risco de divergência entre CLI, scripts e pipeline. | Média | `scripts/process_first_solicitation_cdp.py`, `scripts/inspect_portal_table.py` | Transformar scripts em wrappers finos de casos de uso. | Etapa 6 |

## 5. Arquivos grandes e responsabilidades excessivas

| Arquivo | Tamanho observado | Responsabilidades acumuladas | Risco | Recomendação |
|---|---:|---|---|---|
| `automacao_gd/infrastructure/portal/cdp_service.py` | ~2777 linhas | Conexão, leitura de tabela, paginação, abertura de detalhe, download, recuperação de listagem, extração técnica após download. | Alto | Extrair `listing_reader`, `pagination_navigator`, `detail_downloader`, `listing_recovery`, `download_reporter`. |
| `automacao_gd/infrastructure/excel/service.py` | ~1721 linhas | Validação, backup, busca de linha, inserção, atualização, reparo visual, layout, comparação de atualização. | Alto | Extrair `workbook_validator`, `row_locator`, `row_writer`, `equipment_cell_formatter`, `backup_service`. |
| `automacao_gd/application/processing_service.py` | ~893 linhas | Processamento PDF, metadata, pasta cliente, Excel, arquivamento, relatório, state. | Alto | Separar fluxo em etapas explícitas com objetos de resultado estruturados. |
| `automacao_gd/application/full_pipeline.py` | ~735 linhas | Pré-voo, download, processamento, payload, relatórios, resumo e erro. | Médio | Transformar em orquestrador por etapas com progresso e checkpoint. |
| `automacao_gd/infrastructure/pdf/service.py` | ~686 linhas | Extração, parsing, validação, parsing de equipamentos e limpeza textual. | Médio | Separar parser de equipamentos em módulo próprio e cobrir com spec. |
| `automacao_gd/infrastructure/files/client_folder_service.py` | ~763 linhas | Busca, cache, normalização, arquivamento e fallback de pasta. | Médio | Separar busca/cache/arquivamento. |
| `automacao_gd/presentation/desktop/web_bridge.py` | ~440 linhas | API QWebChannel, execução, settings temporário, CDP, abertura de arquivos/pastas. | Médio | Dividir bridges por responsabilidade em `apps/desktop/bridge`. |

## 6. Funções/classes candidatas a refatoração futura

| Item | Motivo | Etapa recomendada |
|---|---|---|
| `download_completed_budgets_from_current_page` | Fluxo CDP amplo, estado e paginação no mesmo ponto. | Etapa 6 |
| `download_connection_budget` | Mistura clique, espera de download, tratamento de erro e path final. | Etapa 6 |
| `process_downloaded_pdfs` | Orquestra simulação, backup, Excel, arquivo e relatório. | Etapa 6 |
| `_process_single_pdf` | Concentra extração, pasta cliente, Excel, arquivamento e state por protocolo. | Etapa 6 |
| `_load_or_extract_technical_data` | Cache técnico, extração e versionamento de formato no mesmo ponto. | Etapa 5 |
| `parse_generation_data_from_text` | Parser geral com muitas regras de layout. | Etapa 3 |
| `GenerationData.format_module_for_planilha` e `format_inverter_for_planilha` | Formatação de Excel dentro do modelo de domínio. | Etapa 3 |
| `update_excel_from_pdf_data` | Decide escrita, backup, skip, movimentação e layout. | Etapa 4 |
| `PipelineStateStore.validate_completed_artifacts` | Validação de state acoplada a Excel/arquivos. | Etapa 5 |
| `AutomationBridge` | Bridge com responsabilidades de UI, execução, CDP e abertura de recursos. | Etapa 8 |

## 7. Pontos responsáveis por operação

| Responsabilidade | Local atual |
|---|---|
| Leitura do portal | `automacao_gd/infrastructure/portal/cdp_service.py`, `browser.py`, `cdp_browser.py` |
| Conexão CDP | `automacao_gd/infrastructure/portal/cdp_service.py`, `cdp_browser.py`, `factory.py` |
| Download do orçamento de conexão | `automacao_gd/infrastructure/portal/cdp_service.py`, função `download_connection_budget` e fluxo de seleção/download |
| Extração de PDF | `automacao_gd/infrastructure/pdf/service.py` |
| Parser de equipamentos | `automacao_gd/infrastructure/pdf/service.py` e `automacao_gd/domain/models.py` |
| Atualização da planilha | `automacao_gd/infrastructure/excel/service.py` |
| Arquivamento do PDF | `automacao_gd/infrastructure/files/client_folder_service.py`, chamado por `application/processing_service.py` |
| Checkpoint/state | `automacao_gd/infrastructure/state/pipeline_state.py` |
| Cache | `data/cache/client_folder_cache.json`, `automacao_gd/infrastructure/files/client_folder_service.py`, `automacao_gd/data/cache` |
| Logs e relatórios | `application/full_pipeline.py`, `application/processing_service.py`, `infrastructure/logging.py`, `persistence/atomic.py` |
| Saída operacional no terminal | `automacao_gd/presentation/operational_output.py`, `cli.py` |
| Interface desktop existente | `desktop_app.py`, `automacao_gd/presentation/desktop`, `frontend/`, `tkinter_app.py` legado |

## 8. Onde implementar futuramente a regra de Placa/Inversor

Recomendação técnica:

1. Especificar comportamento em `specs/equipment_format_v2.md`.
2. Criar testes antes da implementação em `tests/test_pdf_service.py`, `tests/test_excel_service.py`, `tests/test_processing_service.py` e possivelmente novo `tests/test_equipment_format_v2.py`.
3. Concentrar parsing de equipamentos em módulo dedicado, preferencialmente `automacao_gd/domain/equipment.py` ou `automacao_gd/application/equipment_formatting.py`, evitando espalhar regra por PDF e Excel.
4. Manter `automacao_gd/infrastructure/pdf/service.py` responsável por extrair dados brutos/estruturados do PDF.
5. Manter `automacao_gd/infrastructure/excel/service.py` responsável apenas por gravar o texto já formatado e aplicar layout.

Locais atuais impactáveis:

- `automacao_gd/domain/models.py`
- `automacao_gd/infrastructure/pdf/service.py`
- `automacao_gd/infrastructure/excel/service.py`
- `automacao_gd/application/processing_service.py`
- `automacao_gd/infrastructure/state/pipeline_state.py`

## 9. Onde tratar microinversores futuramente

Microinversores já aparecem em:

- `InverterEquipment.equipment_type`
- `GenerationData.microinverters`
- `GenerationData.microinverter_total_quantity`
- `GenerationData.microinverter_total_kw`
- `parse_generation_data_from_text`
- `format_inverter_for_planilha`

Recomendação futura:

- Formalizar microinversor na regra `EQUIPMENT_FORMAT_VERSION = 2`.
- Não gravar potência total em Placa/Inversor.
- Quando houver inversor convencional e microinversor, identificar ambos na célula Inversor.
- Quando houver apenas microinversor, usar a coluna Inversor com identificação clara.
- Gerar alerta técnico quando fabricante/modelo/quantidade não puderem ser pareados sem inventar dados.

## 10. Onde emitir progresso 0 a 100% futuramente

Locais recomendados:

- `application/full_pipeline.py`: progresso macro do pipeline CDP completo.
- `application/processing_service.py`: progresso por PDF/protocolo.
- `infrastructure/portal/cdp_service.py`: progresso de leitura de páginas/downloads, mas emitido via callback/porta, não via UI direta.
- `presentation/desktop/worker.py` e `web_bridge.py`: consumo de eventos para UI, sem calcular regras.
- `presentation/operational_output.py`: renderização textual do progresso, se aplicável.

Modelo recomendado:

- Definir contrato de progresso na aplicação, por exemplo `ProgressEvent(stage, percent, protocol, message)`.
- Percentual monotônico.
- Etapas nomeadas: `preflight`, `portal_read`, `download`, `pdf_parse`, `excel_update`, `archive`, `report`, `cleanup`.
- UI e CLI apenas consomem eventos.

## 11. Onde criar erros estruturados futuramente

Locais recomendados:

- `domain/errors.py`: hierarquia semântica.
- `application/contracts.py`: contrato `OperationError` ou `StepError`.
- `application/full_pipeline.py` e `processing_service.py`: mapear exceções para erro estruturado.
- `infrastructure/portal/cdp_service.py`: transformar erros de Playwright em códigos como `CDP_PAGE_CLOSED`, `DOWNLOAD_TIMEOUT`, `PORTAL_ACCESS_DENIED`.
- `infrastructure/pdf/service.py`: erros como `PDF_INVALID_SIGNATURE`, `EQUIPMENT_PARSE_WARNING`.
- `infrastructure/excel/service.py`: erros como `EXCEL_LOCKED`, `PROTOCOL_DUPLICATED`, `WRONG_SHEET`, `ROW_VALIDATION_FAILED`.

Campos mínimos recomendados:

- `code`
- `stage`
- `severity`
- `message`
- `technical_detail`
- `protocol`
- `recoverable`
- `recommended_action`

## 12. Arquivos temporários gerados

| Origem | Temporários observados/esperados | Risco |
|---|---|---|
| `persistence/atomic.py` | `.<nome>.*.tmp` no mesmo diretório do destino | Sobra de `.tmp` se processo for encerrado abruptamente antes do cleanup. |
| `scripts/create_clean_release_zip.py` | ZIP temporário antes do `os.replace` | Sobra se interrupção externa ocorrer. |
| Downloads do navegador | `.crdownload`, `.part` | Download incompleto processado se validação falhar em algum fluxo futuro. |
| Excel/Office | `~$*.xlsx` | Pode causar lock e erro de gravação se planilha aberta. |
| Python/testes | `__pycache__`, `.pytest_cache` | Poluição de release e auditoria visual. |
| Migração | Backups e relatórios em `data/logs` | Crescimento de logs e duplicidade de artefatos se não houver política de retenção. |

Recomendação futura: criar etapa explícita `cleanup_temp_files` com allowlist segura, sem apagar dados operacionais.

## 13. Riscos atuais

| Risco | Impacto | Severidade | Arquivo provável | Recomendação futura | Etapa |
|---|---|---:|---|---|---|
| Duplicidade de processamento por state incompleto ou versão antiga. | Rebaixar produtividade, baixar/processar protocolo já tratado. | Média | `state/pipeline_state.py`, `full_pipeline.py` | Versionar checkpoints por etapa e formato. | Etapa 5 |
| Reprocessamento desnecessário para reformatação de Placa/Inversor. | Baixa PDF e arquiva novamente quando bastaria reformatar linha. | Alta | `processing_service.py`, `pipeline_state.py` | Criar ação futura `reformat_existing_excel_row`. | Etapa 5 |
| Criação de arquivos `_v2`, `_v3` sem necessidade. | Polui pasta do cliente e reduz rastreabilidade. | Alta | `client_folder_service.py`, `processing_service.py` | Reformatação não deve arquivar novamente nem gerar novo PDF. | Etapa 5 |
| Perda de rastreabilidade entre PDF, Excel e state. | Dificulta auditoria e correção manual. | Média | `pipeline_state.py`, relatórios JSON | Adicionar versão de formato e IDs de etapa no state. | Etapa 5 |
| Alteração indevida da planilha. | Risco operacional alto em produção. | Alta | `excel/service.py` | Backup obrigatório, validação pré/pós e atualização mínima de células. | Etapa 4 |
| Acoplamento da futura interface desktop ao backend. | UI pode chamar Playwright/Excel diretamente e duplicar regra. | Alta | `presentation/desktop/web_bridge.py` | Criar `apps/desktop` chamando apenas casos de uso. | Etapa 8 |
| Erros sem estrutura padronizada. | Mensagens difíceis de explicar por etapa. | Média | `full_pipeline.py`, `processing_service.py`, `cdp_service.py` | Introduzir `StepError`/`OperationError`. | Etapa 6 |
| Progresso sem contrato granular. | UI não consegue exibir 0 a 100% confiável. | Média | `full_pipeline.py`, `processing_service.py` | Criar callback/event bus de progresso. | Etapa 6 |
| Arquivos temporários acumulados. | Poluição e risco de release com dados locais. | Média | `atomic.py`, scripts, `data/` | Etapa de cleanup com testes. | Etapa 7 |
| Parser de equipamentos dependente de layout específico. | Falhas em PDFs com tabela quebrada ou separadores diferentes. | Alta | `pdf/service.py`, `domain/models.py` | TDD com cenários da spec v2. | Etapa 2/3 |

## 14. Análise da futura estrutura PySide6

É melhor criar outra pasta para a interface desktop PySide6 dentro do projeto?

Sim. A recomendação é manter a interface no mesmo repositório, mas em estrutura separada:

```text
apps/desktop/
  main.py
  bridge/
    automation_bridge.py
    progress_bridge.py
  workers/
  frontend/
  resources/
```

Justificativa:

- O backend da automação não deve ser duplicado.
- A interface desktop deve chamar apenas casos de uso da camada `application`.
- A interface não deve chamar Playwright diretamente.
- A interface não deve atualizar Excel diretamente.
- A interface não deve conter caminhos hardcoded.
- A interface deve usar `Settings` centralizado.
- `app.py`/CLI deve ser mantido como contingência operacional.
- A estrutura separada reduz o risco de misturar regras de UI com regras de pipeline.

Situação atual:

- Já existe uma base em `automacao_gd/presentation/desktop` e `frontend/`.
- Ela é útil como protótipo/base inicial, mas para homologação mais limpa deve ser reorganizada em etapa futura.
- A migração para `apps/desktop` deve ser planejada, testada e feita sem quebrar o CLI.

## 15. Conclusão da auditoria

O projeto está operacional e tem boa base de testes. A arquitetura já tem camadas nomeadas e algumas fronteiras corretas. A maior dívida técnica está em arquivos grandes que concentram responsabilidades e em acoplamentos da aplicação com infraestrutura concreta.

Para a próxima etapa, a prioridade deve ser escrever testes antes de alterar a regra de equipamentos. A implementação da regra `equipment_format_v2` deve evitar novo processamento desnecessário e deve permitir reformatação de registros já concluídos sem baixar ou arquivar PDF novamente.
