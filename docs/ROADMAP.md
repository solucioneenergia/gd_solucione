# Roadmap — evolução gd_neoenergia

Escopo: planejamento por etapas para evolução com Clean Code, Spec Driven Development, testes automatizados, checkpoint granular, progresso 0 a 100%, erros estruturados, limpeza de temporários, regra `equipment_format_v2` e preparação futura para PySide6.

## Etapa 1 — Auditoria e especificação

Objetivo: documentar o estado atual, riscos, roadmap e especificação da nova regra de equipamentos.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: mapear arquitetura, dependências e pontos de implementação futura.
- DevOps sênior: mapear state, cache, logs, temporários, release e riscos operacionais.
- Analista de QA sênior: definir matriz de requisitos e critérios de aceite futuros.
- UX Designer: avaliar necessidades futuras de progresso, erros explicados e desktop.

Checklist:

- [x] Auditar estrutura atual.
- [x] Mapear camadas.
- [x] Identificar acoplamentos e arquivos grandes.
- [x] Especificar `equipment_format_v2`.
- [x] Criar roadmap por etapas.
- [x] Criar matriz QA.
- [x] Documentar recomendação para PySide6 em estrutura separada.

Arquivos prováveis impactados:

- `docs/CURRENT_STATE_AUDIT.md`
- `docs/ROADMAP.md`
- `specs/equipment_format_v2.md`

Critérios de aceite:

- [x] Nenhum código funcional alterado.
- [x] Nenhuma planilha alterada.
- [x] Nenhum state/cache/download alterado.
- [x] ZIP não gerado.
- [x] Documentos criados.

Riscos:

- Auditoria ficar desatualizada se código mudar antes da Etapa 2.

Dependências:

- Nenhuma.

## Etapa 2 — Testes antes da implementação

Objetivo: criar testes automatizados que representem a especificação antes de modificar o código funcional.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: estruturar testes unitários/integração.
- Analista de QA sênior: transformar matriz QA em casos de teste.
- DevOps sênior: garantir execução local e CI sem dados sensíveis.
- UX Designer: validar mensagens esperadas de erro/progresso em nível de contrato.

Checklist:

- [ ] Criar testes para módulo único.
- [ ] Criar testes para inversor único.
- [ ] Criar testes para múltiplos módulos com `|`.
- [ ] Criar testes para múltiplos módulos com `/`.
- [ ] Criar testes para múltiplos inversores com fabricante repetido.
- [ ] Criar testes para microinversor isolado.
- [ ] Criar testes para inversor + microinversor.
- [ ] Criar testes para divergência fabricante/modelo.
- [ ] Criar testes para Excel com wrap/altura.
- [ ] Criar testes para reformatação de registro antigo sem download/arquivamento.

Arquivos prováveis impactados:

- `tests/test_pdf_service.py`
- `tests/test_excel_service.py`
- `tests/test_processing_service.py`
- `tests/test_pipeline_state_service.py`
- Possível novo `tests/test_equipment_format_v2.py`

Critérios de aceite:

- [ ] Testes falham antes da implementação quando a regra ainda não existir.
- [ ] Testes não dependem de planilha real.
- [ ] Testes não dependem do Portal GD.
- [ ] Testes não alteram `data/state`, `data/downloads` ou `data/cache`.

Riscos:

- Criar teste acoplado demais ao layout atual e dificultar refatoração.
- Usar PDF real com dado sensível em fixture.

Dependências:

- Etapa 1 concluída.

## Etapa 3 — Parser e domínio de equipamentos

Objetivo: implementar a regra `equipment_format_v2` no parser/domínio com menor acoplamento possível.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: implementar parser, entidades e formatadores.
- Analista de QA sênior: validar cenários de separadores e microinversores.

Checklist:

- [ ] Introduzir `EQUIPMENT_FORMAT_VERSION = 2`.
- [ ] Separar parsing bruto de formatação de planilha.
- [ ] Aceitar separadores `|`, `/`, `;` e quebra de linha.
- [ ] Parear fabricante/modelo por posição.
- [ ] Preservar fabricante repetido.
- [ ] Gerar alerta técnico para divergência.
- [ ] Garantir que potência total não seja gravada em `Placa`/`Inversor`.
- [ ] Formalizar microinversores.

Arquivos prováveis impactados:

- `automacao_gd/domain/models.py`
- `automacao_gd/infrastructure/pdf/service.py`
- Possível novo `automacao_gd/domain/equipment.py`
- Possível novo `automacao_gd/application/equipment_formatting.py`

Critérios de aceite:

- [ ] Todos os testes da Etapa 2 relacionados ao parser passam.
- [ ] Sem alteração em Excel nesta etapa além de contratos se necessário.
- [ ] Sem download novo.
- [ ] Sem arquivamento novo.

Riscos:

- Quebrar PDFs já suportados.
- Interpretar `/` como separador quando faz parte do modelo.

Dependências:

- Testes da Etapa 2 definidos.

## Etapa 4 — Atualização da planilha

Objetivo: aplicar a formatação v2 nas células `Placa` e `Inversor` preservando estrutura visual e dados existentes.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: implementar escrita mínima de células.
- Analista de QA sênior: validar workbook em memória e casos reais controlados.
- DevOps sênior: validar backup e proteção contra arquivo aberto.

Checklist:

- [ ] Gravar multilinha em `Placa`.
- [ ] Gravar multilinha em `Inversor`.
- [ ] Aplicar `wrap_text=True`.
- [ ] Aplicar `vertical="top"`.
- [ ] Ajustar altura pelo maior número de linhas.
- [ ] Preservar cabeçalho, filtros, cores, larguras e demais colunas.
- [ ] Criar backup antes de produção.
- [ ] Validar pré e pós-gravação.

Arquivos prováveis impactados:

- `automacao_gd/infrastructure/excel/service.py`
- `automacao_gd/application/processing_service.py`
- `tests/test_excel_service.py`

Critérios de aceite:

- [ ] Planilha de teste mantém estrutura.
- [ ] Apenas células previstas são alteradas.
- [ ] Backup criado em produção.
- [ ] Simulação não grava.

Riscos:

- Alterar linha errada.
- Perder filtro/cabeçalho/estilo.
- Regravar linha já correta sem necessidade.

Dependências:

- Etapa 3 concluída.

## Etapa 5 — Registros antigos e checkpoint

Objetivo: permitir que protocolos já `completed` em formato antigo sejam reformados sem repetir download ou arquivamento.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: implementar ação `reformat_existing_excel_row`.
- DevOps sênior: revisar state, rastreabilidade e retomada.
- Analista de QA sênior: validar não repetição de tarefas.

Checklist:

- [ ] Registrar `equipment_format_version` por protocolo.
- [ ] Detectar completed antigo com versão menor que 2.
- [ ] Implementar ação futura `reformat_existing_excel_row`.
- [ ] Reutilizar PDF ou metadata existente.
- [ ] Não baixar PDF novamente.
- [ ] Não arquivar PDF novamente.
- [ ] Não criar `_v2`, `_v3` sem necessidade.
- [ ] Atualizar apenas `Placa` e `Inversor`.
- [ ] Atualizar checkpoint para versão 2.

Arquivos prováveis impactados:

- `automacao_gd/infrastructure/state/pipeline_state.py`
- `automacao_gd/application/processing_service.py`
- `automacao_gd/application/full_pipeline.py`
- `automacao_gd/infrastructure/excel/service.py`

Critérios de aceite:

- [ ] Protocolo completed antigo é selecionado apenas para reformatação.
- [ ] Nenhuma chamada de download ocorre nesse fluxo.
- [ ] Nenhum arquivamento ocorre nesse fluxo.
- [ ] State registra versão nova.

Riscos:

- State antigo incompleto.
- PDF local inexistente.
- Metadata divergente da planilha.

Dependências:

- Etapa 4 concluída.

## Etapa 6 — Progresso e erros estruturados

Objetivo: expor progresso monotônico de 0 a 100% e erros explicados por etapa.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: criar contratos de progresso/erro.
- UX Designer: definir linguagem de status e mensagens operacionais.
- Analista de QA sênior: testar monotonicidade e erros esperados.
- DevOps sênior: garantir logs auditáveis.

Checklist:

- [ ] Criar contrato `ProgressEvent`.
- [ ] Criar contrato `StepError`/`OperationError`.
- [ ] Mapear etapas do pipeline.
- [ ] Emitir progresso por etapa.
- [ ] Garantir progresso monotônico.
- [ ] Padronizar códigos de erro.
- [ ] Sanitizar dados pessoais nas mensagens.
- [ ] Manter compatibilidade com CLI.

Arquivos prováveis impactados:

- `automacao_gd/application/contracts.py`
- `automacao_gd/application/full_pipeline.py`
- `automacao_gd/application/processing_service.py`
- `automacao_gd/presentation/operational_output.py`
- `automacao_gd/presentation/desktop/worker.py`
- `automacao_gd/presentation/desktop/web_bridge.py`

Critérios de aceite:

- [ ] Progresso começa em 0 e termina em 100 quando operação conclui.
- [ ] Progresso nunca retrocede.
- [ ] Erro contém etapa, código, mensagem e ação recomendada.
- [ ] Terminal permanece limpo.

Riscos:

- Percentual enganoso em etapas com quantidade dinâmica.
- UI depender de detalhes internos do pipeline.

Dependências:

- Etapa 5 concluída ou contratos definidos com compatibilidade.

## Etapa 7 — Limpeza de temporários

Objetivo: remover temporários seguros sem apagar dados operacionais.

Profissionais envolvidos:

- DevOps sênior: definir política de limpeza.
- Desenvolvedor fullstack sênior: implementar serviço seguro.
- Analista de QA sênior: testar allowlist/denylist.

Checklist:

- [ ] Mapear `.tmp`, `.temp`, `.crdownload`, `.part`, `~$*.xlsx`.
- [ ] Nunca apagar PDFs válidos.
- [ ] Nunca apagar planilha real.
- [ ] Nunca apagar state/cache sem confirmação.
- [ ] Criar dry-run de cleanup.
- [ ] Criar relatório de cleanup.
- [ ] Testar paths dentro da raiz permitida.

Arquivos prováveis impactados:

- Possível novo `automacao_gd/infrastructure/files/cleanup.py`
- `scripts/validate_*`
- `tests/test_*cleanup*.py`

Critérios de aceite:

- [ ] Dry-run lista sem apagar.
- [ ] Apply remove apenas temporários permitidos.
- [ ] Relatório gerado.
- [ ] Testes cobrem paths perigosos.

Riscos:

- Apagar arquivo operacional por padrão amplo.
- Limpar arquivo ainda em uso pelo navegador/Excel.

Dependências:

- Etapa 6 para relatório/erro estruturado, ou implementação isolada com contrato simples.

## Etapa 8 — Preparação da aplicação PySide6

Objetivo: preparar interface desktop moderna sem duplicar backend e sem quebrar CLI.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: estruturar app e bridges.
- UX Designer: definir fluxo visual, progresso, erros e confirmação de produção.
- DevOps sênior: preparar build futuro e recursos.
- Analista de QA sênior: validar que UI chama casos de uso.

Checklist:

- [ ] Criar estrutura futura `apps/desktop/`.
- [ ] Separar bridges: automação, progresso, arquivos.
- [ ] UI chama apenas camada `application`.
- [ ] UI não chama Playwright diretamente.
- [ ] UI não atualiza Excel diretamente.
- [ ] UI usa `Settings`.
- [ ] CLI permanece contingência.
- [ ] Não duplicar backend.
- [ ] Preparar testes de bridge.

Arquivos prováveis impactados:

- Futuro `apps/desktop/main.py`
- Futuro `apps/desktop/bridge/automation_bridge.py`
- Futuro `apps/desktop/bridge/progress_bridge.py`
- Futuro `apps/desktop/workers`
- Futuro `apps/desktop/frontend`
- `desktop_app.py` como compatibilidade ou wrapper futuro

Critérios de aceite:

- [ ] Interface abre sem bloquear.
- [ ] Pipeline roda por caso de uso.
- [ ] Produção exige confirmação detalhada.
- [ ] Dados sensíveis não chegam ao frontend.
- [ ] CLI continua funcionando.

Riscos:

- UI chamar infraestrutura diretamente.
- Divergência entre CLI e desktop.
- Acoplamento a caminhos locais.

Dependências:

- Etapa 6 recomendada para progresso/erros.

## Etapa 9 — Validação final e homologação

Status: homologação técnica/offline concluída em 2026-07-19. Produção real permanece pendente de validação assistida com 1 protocolo.

Objetivo: validar funcionalmente, operacionalmente e tecnicamente a evolução sem executar produção real.

Profissionais envolvidos:

- Desenvolvedor fullstack sênior: correções finais.
- DevOps sênior: ambiente, release limpo, checklist.
- Analista de QA sênior: regressão e homologação.
- UX Designer: validação de clareza operacional.

Checklist:

- [x] `python -m pytest -q`.
- [x] `python -m compileall automacao_gd apps`.
- [x] `python -m compileall scripts`.
- [x] Validação offline com fixtures e workbooks sintéticos.
- [x] Validação de imports principais.
- [x] Validação de fronteiras de Clean Architecture.
- [x] Validação automatizada de Equipment Format V2.
- [x] Validação automatizada de Excel sintético.
- [x] Validação automatizada de reformatação de registros antigos.
- [x] Validação automatizada de progresso e erros estruturados.
- [x] Validação automatizada de cleanup seguro.
- [x] Validação automatizada da base desktop.
- [x] Relatório final de homologação técnica/offline criado.
- [x] Checklist de produção assistida criado.
- [ ] Produção assistida com 1 protocolo.
- [ ] Conferência visual da planilha real após produção assistida.
- [ ] Conferência de PDF arquivado real após produção assistida.
- [ ] Conferência de state/checkpoint real após produção assistida.
- [ ] Conferência de logs reais após produção assistida.
- [ ] Conferência de release limpo em etapa própria posterior.

Arquivos prováveis impactados:

- Testes.
- Docs.
- Scripts de validação.
- Checklist de produção.

Critérios de aceite:

- [x] Sem regressão automatizada detectada.
- [x] `xfailed = 0`.
- [x] `skipped` justificado.
- [x] `app.py` importável.
- [x] `desktop_app.py` importável.
- [x] `apps/desktop` importável.
- [x] Documentação final criada.
- [x] Checklist de produção assistida criado.
- [x] Nenhuma produção real executada nesta etapa.
- [ ] Produção real assistida aprovada pelo usuário.
- [ ] Planilha real conferida visualmente após produção assistida.
- [ ] State real conferido após produção assistida.

Riscos:

- Diferença entre ambiente de teste e produção.
- Portal GD alterar layout.
- Planilha real estar aberta/bloqueada.

Dependências:

- Etapas 1 a 8 concluídas.

## Matriz QA

| Requisito | Critério de aceite | Teste automatizado futuro | Prioridade |
|---|---|---|---|
| Módulo único mantém formato compacto | `14x RONMA RM182/144TB 585W` | Unitário de formatador | Alta |
| Inversor único mantém formato compacto | `1x HUAWEI SUN2000-6KTL` | Unitário de formatador | Alta |
| Múltiplos módulos com separador pipe | Gera multilinha e total | Unitário parser/formatador | Alta |
| Múltiplos módulos com separador barra | Gera multilinha quando barra for separador válido | Unitário parser/formatador | Alta |
| Múltiplos inversores com fabricante repetido | Fabricante repetido é preservado | Unitário parser/formatador | Alta |
| Potência total não é gravada | Célula não contém kWp/kW total | Unitário + Excel | Alta |
| Quantidade total é preservada | Linha `Qtd. total` correta | Unitário parser/formatador | Alta |
| Microinversor isolado | Coluna Inversor preenchida com microinversor | Unitário parser/formatador | Alta |
| Inversor + microinversor | Ambos aparecem identificados | Unitário parser/formatador | Alta |
| Divergência fabricante/modelo gera alerta | Alerta técnico estruturado existe | Unitário parser | Média |
| Excel aplica wrap_text | Célula multilinha com `wrap_text=True` | Integração openpyxl | Alta |
| Excel ajusta altura | Altura aumenta conforme linhas | Integração openpyxl | Média |
| Registro completed antigo executa `reformat_existing_excel_row` | Action específica no resultado | Integração state/processamento | Alta |
| Não baixa PDF novamente para reformatação | Download service não é chamado | Teste com mock/spy | Alta |
| Não arquiva PDF novamente para reformatação | Archive service não é chamado | Teste com mock/spy | Alta |
| Checkpoint por etapa | State registra etapas e versão | Unitário state | Alta |
| Retomada após interrupção | Pipeline continua do ponto seguro | Integração state | Alta |
| Progresso monotônico | Percentual nunca retrocede | Unitário contrato progresso | Média |
| Erro estruturado | Erro tem código, etapa e recomendação | Unitário contratos | Média |
| Limpeza de temporários | Remove só arquivos permitidos | Unitário cleanup | Média |
| Regras de Clean Architecture | UI não importa Playwright/Excel diretamente | Teste estático/import lint futuro | Média |

## Recomendação imediata

Próxima recomendação: executar produção assistida com 1 protocolo somente após conferência manual do checklist em `docs/PRODUCTION_HOMOLOGATION_CHECKLIST.md`.
