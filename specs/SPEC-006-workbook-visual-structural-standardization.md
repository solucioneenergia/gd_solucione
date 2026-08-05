# SPEC-006 - Padronizacao visual e estrutural da planilha

Status: Etapa 4.1 concluida - referencia canonica estabilizada e plano read-only aprovado.

## Objetivo

Auditar a planilha oficial sem escrita, usando a aba `2025` como referencia canonica visual e estrutural, e gerar um plano versionado de padronizacao futura.

## Regras vinculantes

- A execucao da Etapa 4.1 e exclusivamente read-only.
- Nenhum valor logico, formula, comentario, hyperlink, ordem de linhas, ordem de abas ou texto tecnico pode ser alterado.
- Nenhum backup de aplicacao, temporario de workbook, `workbook.save` ou `os.replace` pode ser executado.
- A aplicacao futura exige confirmacao forte: `APLICAR PADRONIZAÇÃO VISUAL E ESTRUTURAL DA PLANILHA`.

## Aba canonica

A aba `2025` e a referencia inicial para:

- titulo;
- cabecalho;
- larguras de coluna;
- estilos de celulas de dados;
- formatos de datas;
- formato da coluna `Protocolo`;
- politicas empiricas de altura por arquetipo de linha;
- estilo de linhas multifabricante.

Se a propria aba canonica tiver diferencas visuais internas, o servico deve decompor as diferencas por propriedade e separar altura de estilo. Variacoes reais com destino deterministico entram como acao futura segura `STYLE_STANDARDIZATION`; variacoes explicadas por altura, line count ou estado informativo nao entram como bloqueio.

## Fingerprint semantico

O fingerprint visual nao deve depender somente de `style_id`. Ele deve normalizar propriedades observaveis:

- fonte;
- preenchimento;
- bordas;
- alinhamento;
- formato numerico;
- protecao;
- `quote_prefix`.

O conteudo possui fingerprint separado, incluindo representacao logica do valor, formula, tipo, hyperlink, comentario e mesclagem.

## Ultima linha real

`worksheet.max_row` nao e fonte unica de verdade. A ultima linha real deve ser calculada pelas sete colunas da tabela e por presenca de formula, comentario, hyperlink ou conteudo logico. Linhas apenas estilizadas apos os dados sao candidatas a `TRAILING_MATERIALIZED_EMPTY_ROW`.

## Filtros

O filtro esperado e derivado da linha de cabecalho e da ultima linha real:

```text
A<header_row>:G<last_real_data_row>
```

Categorias:

- `FILTER_RANGE_CORRECT`
- `FILTER_HEADER_ONLY`
- `FILTER_RANGE_TOO_SHORT`
- `FILTER_RANGE_TOO_LONG`
- `FILTER_RANGE_INVALID`
- `FILTER_NOT_PRESENT`

## Protocolos numericos

Protocolos armazenados fisicamente como numero podem ser classificados como conversao segura somente quando o inteiro exato preserva todos os digitos e o texto canonico e igual ao protocolo logico.

Categorias:

- `PROTOCOL_NUMERIC_SAFE_TO_CONVERT`
- `PROTOCOL_NUMERIC_REQUIRES_REVIEW`
- `PROTOCOL_ALREADY_TEXT`
- `PROTOCOL_INVALID`

## Linhas multifabricante

Linhas com multiplos fabricantes, quebras de linha, `Fabricante | Modelo` ou `Qtd. total` devem ter conteudo protegido. A Etapa 4.1 avalia somente:

- `wrap_text`;
- alinhamento;
- altura;
- bordas;
- fonte;
- preenchimento;
- preservacao de quebras.

Nenhuma quantidade deve ser inferida ou redistribuida.

## Plano read-only

O plano deve conter:

- `metadata`;
- `workbook_sha`;
- `workbook_content_hash`;
- `canonical_fingerprint`;
- comparacoes por aba;
- achados tipados;
- acoes propostas;
- celulas e protocolos protegidos;
- allowlist futura;
- bloqueios;
- revisao;
- decisao.

Todas as acoes devem ter:

```text
apply_now = false
changes_content = false
```

## Criterios de aceite

- SHA antes e depois iguais.
- `workbook_content_hash_before = workbook_content_hash_after`.
- `content_changes_proposed = 0`.
- `safe_proposed_actions + blocked_proposed_actions = proposed_actions`.
- P0 = 0.
- P1 = 0.

## Resultado do diagnostico read-only de 2026-07-28

Artefatos oficiais:

- `data/logs/workbook_visual_standardization_plan_20260728T192839Z.json`
- `data/logs/workbook_visual_standardization_plan_20260728T192839Z.md`
- `data/logs/workbook_visual_fingerprint_2025_20260728T192839Z.json`
- `data/logs/workbook_visual_fingerprint_2025_20260728T192839Z.md`
- `data/logs/workbook_visual_structural_audit_20260728T192839Z.json`
- `data/logs/workbook_visual_structural_audit_20260728T192839Z.md`

Resultado:

- planilha oficial preservada: sim;
- SHA antes/depois: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- abas auditadas: 4;
- aba canonica: `2025`;
- acoes propostas: 75;
- acoes seguras para plano futuro: 27;
- acoes bloqueadas para revisao: 48;
- alteracoes de conteudo propostas: 0;
- `apply_now=true`: 0;
- inconsistencias na referencia canonica: 3;
- decisao: `STAGE4_1_PARTIAL - CANONICAL_REFERENCE_REQUIRES_REVIEW`.

Nenhuma acao deste diagnostico pode ser aplicada automaticamente. Antes de qualquer etapa de escrita, a equipe deve revisar as inconsistencias da aba canonica `2025` e gerar um pre-voo proprio.

## Politica canonica final

A politica canonica final da Etapa 4.1 usa a aba `2025`, mas nao propaga `style_id` interno do Excel nem altura de linha como parte do hash visual.

Hashes separados:

- `cell_visual_style_hash`;
- `row_visual_style_hash`;
- `row_height_policy_hash`, representado pela politica de altura;
- `row_archetype_hash`, representado pelo arquetipo e line count.

Politicas:

- `freeze_panes` futuro esperado: `A3`;
- configuracao de pagina: `OUT_OF_SCOPE_PAGE_SETUP`, sem acao sem requisito empresarial;
- protocolos numericos seguros: acao futura somente de tipo fisico, sem mudanca logica;
- conteudo multifabricante: protegido em `protected_targets`, nunca em `proposed_actions`;
- findings informativos: separados de acoes e fora da equacao `safe + blocked = proposed`.

## Politica de altura final

Metricas obrigatorias por linha:

- `explicit_line_count`;
- `equipment_entry_count`;
- `estimated_wrapped_line_count`;
- `required_visual_line_count`;
- `current_height`;
- `minimum_required_height`;
- `height_tolerance`.

Regra:

```text
required_visual_line_count = max(explicit_line_count, estimated_wrapped_line_count)
```

`equipment_entry_count` nao e usado sozinho para concluir insuficiencia de altura.

## Serializacao canonica de cores

Cores de fonte, preenchimento e borda sao serializadas como estrutura tipada:

```text
type
rgb
indexed
theme
tint
auto
```

Mensagens de validacao do openpyxl, excecoes, descritores ou `repr` de tipos nao podem entrar nos artefatos.

## Artefatos finais de 2026-07-29

- `data/logs/workbook_canonical_style_variant_analysis_20260729T115215Z.json`
- `data/logs/workbook_canonical_style_variant_analysis_20260729T115215Z.md`
- `data/logs/workbook_visual_fingerprint_2025_final_20260729T115215Z.json`
- `data/logs/workbook_visual_fingerprint_2025_final_20260729T115215Z.md`
- `data/logs/workbook_visual_structural_audit_final_20260729T115215Z.json`
- `data/logs/workbook_visual_structural_audit_final_20260729T115215Z.md`
- `data/logs/workbook_visual_standardization_plan_final_20260729T115215Z.json`
- `data/logs/workbook_visual_standardization_plan_final_20260729T115215Z.md`

Identidade:

- SHA da planilha: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- content hash final antes/depois: `85a0eed71927b8123dfabda0f77f84ad9efd568a2fdeac2aa192643a7ccc9c61`;
- canonical policy hash: `e09a710cea0f29d47feb76324bcc4303173a52a48f21f97d438d900feedf0662`;
- final plan hash: `3589c169354395bbe4964699a10acfbc69a00ce2ec1039fca575a87bae6fd670`.

Metricas finais:

- `canonical_reference_inconsistencies = 0`;
- `unresolved_canonical_decisions = 0`;
- `blocked_proposed_actions = 0`;
- `content_changes_proposed = 0`;
- `apply_now_true = 0`;
- `artifact_error_strings = 0`;
- `proposed_actions = 40`;
- `safe_proposed_actions = 40`;
- `protected_targets_total = 29`;
- `informational_findings_total = 674`.

Decisao da estabilizacao read-only:

```text
STAGE4_1_COMPLETE - CANONICAL_REFERENCE_STABILIZED_AND_READ_ONLY_PLAN_APPROVED
```

## Aplicacao certificada - Etapa 4.3

A aplicacao visual/estrutural foi certificada em modo read-only apos execucao controlada.

Identidade da cadeia:

- SHA anterior autorizada: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- SHA do backup inicial: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- SHA final certificada: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`;
- final plan hash: `3589c169354395bbe4964699a10acfbc69a00ce2ec1039fca575a87bae6fd670`;
- canonical policy hash: `e09a710cea0f29d47feb76324bcc4303173a52a48f21f97d438d900feedf0662`;
- preflight hash: `a7e32e2e86b366874089b17d17ca77bcbab2b7749d3b44ba69c4834d8b4b3cd9`.

Resultado da certificacao:

- topologia de escrita: `MULTI_CYCLE_FULLY_AUDITED`;
- acoes planejadas/reconciliadas: 40/40;
- action IDs ausentes: 0;
- action IDs inesperados: 0;
- action IDs duplicados: 0;
- alvos de estilo fora do plano: 0;
- mudancas de valor logico: 0;
- mudancas fisicas inesperadas: 0;
- acoes pos-aplicacao: 0;
- idempotencia aprovada.

Decisao certificada:

```text
STAGE4_3_COMPLETE - VISUAL_STANDARDIZATION_APPLIED_AND_IDEMPOTENT
```

A SHA `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b` passa a ser a baseline oficial da planilha para proximas etapas. A operacao ampla permanece bloqueada ate autorizacao explicita.
