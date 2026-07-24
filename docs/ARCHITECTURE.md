# Arquitetura

## Direção das dependências

```text
Presentation ──> Application ──> Domain
      │               │
      └──────────────> Infrastructure adapters
```

O domínio não importa Playwright, openpyxl, Tkinter ou filesystem. A apresentação chama o `ApplicationController`, que cria os casos de uso. Os casos de uso executam pré-voo e delegam aos adaptadores de infraestrutura.

## Camadas

### Domain

- `models.py`: entidades e formatação técnica já validadas pela versão anterior;
- `errors.py`: exceções semânticas para evolução futura.

### Application

- `preflight.py`: prontidão do ambiente;
- `use_cases/inspect_portal.py`: inspeção com confirmação injetável;
- `use_cases/process_downloads.py`: fluxo offline;
- `use_cases/run_pipeline.py`: pipeline completo;
- `processing_service.py` e `full_pipeline.py`: orquestração estabilizada.

### Infrastructure

- `portal/browser.py`: navegador iniciado pela aplicação para inspeção;
- `portal/cdp_service.py`: automação CDP, paginação e download;
- `pdf/service.py`: validação e parsing;
- `excel/service.py`: mapeamento, atualização e integridade do workbook;
- `files/client_folder_service.py`: busca e arquivamento;
- `state/pipeline_state.py`: checkpoints;
- `persistence/atomic.py`: primitivas de persistência segura.

### Presentation

- `controller.py`: fachada independente de interface;
- `cli.py`: menu de terminal;
- `tkinter_app.py`: aplicativo desktop inicial.

## Compatibilidade

Os módulos em `src/` apontam para as novas implementações usando alias de módulo. Isso é importante porque os testes e integrações existentes fazem monkeypatch em `src.*`. A camada pode ser removida apenas em uma futura versão principal, depois que consumidores externos migrarem.

## Estratégia Strangler

Os módulos CDP e Excel foram movidos para limites claros, mas mantiveram internamente a lógica validada. A próxima refatoração deve extrair componentes por comportamento:

1. CDP: `listing_reader`, `pagination_navigator`, `detail_downloader`, `listing_recovery`;
2. Excel: `workbook_validator`, `row_locator`, `row_writer`, `format_repair`, `atomic_repository`;
3. Processamento: `technical_extraction`, `client_resolution`, `excel_update`, `archive`.

Cada extração deve manter uma fachada compatível e testes de contrato antes de remover o código antigo.

## Tkinter

A interface executa tarefas longas em threads daemon e entrega resultados pela `queue.Queue`; widgets só são atualizados na thread principal. O fluxo de login usa uma confirmação injetada, sem `input()` dentro da UI.
