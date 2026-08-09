# Especificação — Equipment Format V2 e extração técnica V5

Status: aceita para implementação na Etapa 1.1  
Data: 2026-07-21  
ADRs relacionadas: nenhuma; a mudança é comportamental e reversível  
Versão de formato Excel: `EQUIPMENT_FORMAT_VERSION = 2`  
Versão de processamento técnico: `TECHNICAL_PROCESSING_FORMAT_VERSION = 5`

## Contexto

Os orçamentos podem apresentar módulos, inversores e microinversores em campos lineares ou em
uma tabela com colunas paralelas. A ordem linear devolvida pelo PyMuPDF pode intercalar cabeçalhos
e valores de colunas diferentes.

## Problema

O parser atual delimita a seção de módulos pelo primeiro cabeçalho de inversores. Quando os
cabeçalhos paralelos aparecem intercalados, a seção de módulos fica vazia e fabricante/modelo de
módulo podem ser concatenados ao inversor. O cache técnico anterior pode perpetuar o resultado.

## Evidências do comportamento atual

Fixture sintética equivalente ao protocolo de referência:

```text
Fabricante inversor
Fabricante módulo
GROWATT
LEAPTON
Modelo inversor
Modelo módulo
MIC 3000TL-X
BIFACIAL 585W N-TYPE
Qtd inversores
Potência inversor
Qtd módulos
Potência módulos
1
3
5
2,92
```

Antes da Etapa 1, `Placa` fica vazia e `Inversor` recebe conteúdo dos dois tipos.

## Objetivo

Separar deterministicamente a origem dos equipamentos, normalizar fabricantes, validar o
resultado antes do Excel e invalidar caches inseguros, sem alterar a representação V2 da célula.

## Escopo

- extração linear e de tabela paralela;
- módulos, inversores e microinversores;
- normalização, classificação, pareamento e deduplicação;
- validação técnica anterior ao Excel e ao arquivamento;
- estado `pending_review` e contadores técnicos;
- cache técnico V5 com coleção canônica lossless.

## Fora do escopo

- backfill ou correção histórica em lote;
- mudança de `EQUIPMENT_FORMAT_VERSION`;
- Portal, paginação, ACOMPANHAR, UI, planilha real ou arquivos operacionais;
- OCR.

## Glossário

- `module`: equipamento exclusivo da coluna `Placa`.
- `inverter`: inversor convencional da coluna `Inversor`.
- `microinverter`: seção própria na extração e coluna `Inversor` na formatação.
- `pending_review`: resultado inseguro que não pode atualizar Excel nem concluir protocolo.

## Requisitos funcionais

1. O layout linear tradicional deve manter o comportamento aprovado.
2. A tabela paralela deve associar valores pela ordem e pelo tipo dos cabeçalhos, nunca por
   protocolo, fabricante, modelo ou coordenada absoluta.
3. Valores sob cabeçalhos de módulo pertencem somente a módulo; valores sob cabeçalhos de
   inversor pertencem somente a inversor.
4. Microinversores permanecem separados na extração e são formatados na coluna `Inversor`.
5. Fabricantes são normalizados antes do pareamento e da deduplicação.
6. Deduplicar por `tipo + fabricante canônico + modelo normalizado`; modelos diferentes não
   podem ser descartados e quantidades incertas não podem ser inventadas.
7. `SOLPLANET` e `AISWEI`, isolados ou combinados com `/`, `-` ou espaços, resultam em
   `SOLPLANET`.
8. `GOKIN` é válido somente como módulo e invalida identidade de inversor/microinversor.
9. O texto compacto permanece `quantidadex FABRICANTE MODELO`; múltiplos itens permanecem
   multilinha, sem potência total.
10. Antes do Excel, a validação exige Placa, quantidade de módulos, Inversor ou
    microinversor, quantidade correspondente, pares seguros e ausência de contaminação.
11. Resultado inválido recebe `action=pending_technical_review`,
    `technical_processing.status=pending_review` e `technical_review_required=true`.
12. Resultado inválido não chama Excel, não arquiva, não conclui o protocolo e não interrompe
    os demais PDFs.
13. Cache anterior a V5, V5 pendente ou V5 sem validação aprovada deve reextrair o PDF.
14. Cache V5 `success` somente pode ser persistido após validação aprovada.
15. Células paralelas com continuação de linha devem ser reconstruídas integralmente; nenhum
    valor excedente pode ser truncado ou descartado por associação ordinal.
16. Quando apenas o texto linear estiver disponível, a máquina de estados deve aceitar uma
    continuação somente quando a cardinalidade for determinística. Cardinalidade ambígua produz
    violação bloqueante e `pending_review`.
17. Quando palavras, spans ou blocos posicionais estiverem disponíveis, as colunas devem ser
    calculadas relativamente aos cabeçalhos e suas continuações agrupadas na mesma célula, sem
    coordenadas absolutas específicas de documento.
18. Cada categoria presente exige quantidade própria positiva: módulo, inversor convencional e
    microinversor não validam a quantidade uns dos outros.
19. GOKIN encontrado na seção de inversor ou microinversor deve permanecer como violação
    estruturada bloqueante, mesmo quando outra identidade válida coexistir.
20. Cache V5 deve conter estrutura canônica, fingerprint das regras e textos derivados; antes da
    reutilização, as invariantes atuais e a igualdade entre estrutura e textos devem ser
    reexecutadas.
21. Extração aprovada persiste estado intermediário `validated`; `success` somente pode existir
    depois de confirmação da atualização do Excel.

## Requisitos não funcionais

- Parser determinístico, sem OCR e sem regras por protocolo, fabricante ou modelo.
- Funções separadas para extração, normalização, classificação, pareamento, validação e
  formatação.
- Logs e relatórios não incluem texto integral do PDF nem dados pessoais.
- Compatibilidade integral com a proteção de produção da Etapa 0.

## Contratos e interfaces

O resultado por protocolo expõe:

```text
technical_validation_status
technical_review_required
technical_validation_errors
technical_validation_warnings
module_source
inverter_source
```

A representação canônica contém `equipment_type`, fabricante canônico e bruto, modelo
canônico e bruto, quantidade, valor/unidade de potência, atributos técnicos, origem tipada,
violações e avisos. Uma `CanonicalEquipmentCollection` mantém módulos, inversores convencionais,
microinversores, violações/avisos da coleção e versões. Parser, cache V5, validação, auditoria,
comparação, plano V3 e formatação usam essa mesma estrutura; prefixos redundantes
de fabricante/alias e rótulos genéricos isolados são removidos por tokens inteiros, nunca por
substring, e uma unidade explicitamente identificada nunca é descartada;
APIs legadas atuam somente como adaptadores.

Na rules-4, `MODULO`, `MÓDULO`, `INVERSOR` e `MICROINVERSOR` somente podem ser
removidos do modelo quando a origem tipada comprovar contaminação estrutural. Depois dessa
remoção comprovada, o fabricante repetido introduzido pelo cabeçalho também é removido e o
modelo é validado novamente. Origem que não comprove a limpeza gera as violações bloqueantes
`GENERIC_LABEL_IN_MODEL`, `GENERIC_LABEL_ORIGIN_UNPROVEN` e, quando aplicável,
`DUPLICATED_MANUFACTURER_IN_MODEL`. Fabricante em posição interna sem rótulo contaminante,
como em `ACME X ACME PRO`, permanece intacto.

Mensagem de pendência:

```text
Extração técnica inconclusiva: os dados de Placa e Inversor não puderam ser separados com segurança.
```

## Dados e persistência

- `TECHNICAL_PROCESSING_FORMAT_VERSION = 5` invalida V4 e versões anteriores.
- V5 aprovada armazena a coleção canônica completa e textos derivados, nunca `raw_text`.
- Pendência pode registrar erros, avisos e origens, mas não recebe cache V4 reutilizável.
- `EQUIPMENT_FORMAT_VERSION` permanece 2: a forma das células continua V2; muda a confiabilidade
  da extração e validação, não o contrato visual.
- `equipment_rules_version` é estável, não inclui caminho, timestamp ou dado pessoal, e deve
  coincidir com as regras atuais para permitir retomada sem reextração.
- `equipment_rules_version = equipment-v2-technical-v5-rules-4` é obrigatório.
- O cache persiste coleções canônicas lossless; flags antigas ou textos não
  constituem prova semântica de validade.

## Estados e tratamento de erros

- `extracted`: estrutura obtida, ainda não validada.
- `validated`: invariantes aprovadas e dados prontos para Excel; não significa conclusão.
- `excel_updated`: Excel confirmou a aplicação ou a idempotência da atualização.
- `success`: estado técnico final, persistido somente depois de `excel_updated`.
- `pending_review`: preserva PDF, não grava Excel, não arquiva e permanece reprocessável.
- `operational_pending`: técnica validada, mas Excel ou arquivamento não foi concluído.
- `failed`: falha inesperada; não se confunde com pendência esperada.
- Exceção inesperada continua `FALHOU`; mistura de aprovados e pendentes resulta `PARCIAL`.

Transições permitidas: `extracted → validated → excel_updated → success` e qualquer
estado anterior a `success` pode seguir para a pendência correspondente. É proibido
`validated → success` sem confirmação do Excel, e falha de arquivamento impede conclusão
operacional ainda que o estado técnico final tenha sido alcançado.

## Segurança e privacidade

Testes usam dados sintéticos. PDF real, quando existir, é somente leitura e nunca vira fixture.
Planilha, state, logs e downloads operacionais não são modificados pela homologação.
Relatórios públicos são construídos por allowlist e nunca recebem caminhos, nomes, dados
pessoais, texto integral, objetos de configuração ou campos aninhados não explicitamente
permitidos. A suíte unitária padrão não acessa `data/downloads`; homologação operacional exige
opt-in explícito fora da suíte padrão.

## UX e acessibilidade, quando aplicável

Não há mudança visual. Relatório informa motivo e ação recomendada sem traceback.

## Observabilidade

Contadores incluem `total_pending_review` e `total_technical_pending_review`. Resultados registram
status, erros, avisos e origem de cada grupo, sem texto integral do documento.

## Compatibilidade

Separadores `|`, `/`, `;` e nova linha continuam aceitos; barras internas de modelos como
`RM182/144TB` permanecem intactas. A Etapa 0 e a rotina atômica do Excel não mudam.

## Migração ou backfill

Não há backfill nesta etapa. Registros históricos serão tratados somente na Etapa 2.

## Estratégia de testes

- fixture intercalada e layout linear;
- aliases SOLPLANET/AISWEI, GOKIN e microinversores;
- deduplicação e modelos distintos;
- validação bloqueando Excel/arquivo/state completed;
- cache V4 ignorado e V5 condicionado à aprovação e roundtrip lossless;
- regressões de separadores, quantidades, modelos com barra, Etapa 0 e Excel temporário.
- continuações de fabricante/modelo, cardinalidade ambígua e fronteiras da seção seguinte;
- quantidades independentes de inversor e microinversor, inclusive zero e ausência;
- revalidação semântica e fingerprint de cache V5;
- ordem observável `validação → Excel → sucesso → arquivamento`;
- fixtures anonimizadas dos protocolos `2600001064`, `2600001070` e `2600001073`;
- prova hermética de que a suíte padrão não consulta PDFs operacionais.

## Critérios de aceite

- [ ] Dada a tabela intercalada, quando extraída, então Placa é
  `5x LEAPTON BIFACIAL 585W N-TYPE` e Inversor é `1x GROWATT MIC 3000TL-X`.
- [ ] Dado layout linear, quando extraído, então o resultado aprovado não regride.
- [ ] Dado qualquer alias AISWEI/SOLPLANET, quando normalizado, então resulta `SOLPLANET`.
- [ ] Dado GOKIN em inversor ou microinversor, quando validado, então o protocolo fica pendente.
- [ ] Dado microinversor válido sem inversor convencional, quando validado, então o resultado é
  aprovado e formatado na coluna Inversor.
- [ ] Dado resultado técnico inválido, quando processado, então Excel e arquivamento não são
  chamados e o protocolo não fica completed.
- [ ] Dado cache V4 ou V5 não aprovado, quando processado, então o PDF é reextraído.
- [ ] Dada continuação de linha determinística, quando extraída, então todo o conteúdo da
  célula é preservado; se a cardinalidade for ambígua, o resultado fica pendente.
- [ ] Dadas identidades HUAWEI e GOKIN na seção de inversor, quando validadas, então GOKIN
  permanece violação bloqueante e o Excel não é chamado.
- [ ] Dados inversor e microinversor coexistentes, quando validados, então cada tipo exige sua
  própria quantidade positiva.
- [ ] Dado cache V5 com flags aprovadas mas estrutura inválida, texto divergente ou fingerprint
  incompatível, quando carregado, então é reextraído.
- [ ] Dada falha do Excel após validação, quando persistida, então o estado não é `success`, o
  PDF não é arquivado e a estrutura validada permanece retomável.
- [ ] Dado qualquer caminho em qualquer profundidade do payload interno, quando produzido o
  relatório público, então somente a allowlist aparece e nenhum caminho é serializado.
- [ ] Dada a suíte padrão sem opt-in operacional, quando executada, então `data/downloads` não é
  acessado.
- [ ] Dado cache V5, quando serializado e lido, então tipo, bruto/canônico, quantidade,
  potência/unidade, atributos, origem, violações e avisos permanecem idênticos.
- [ ] Dado modelo com fabricante interno ou rótulo sem origem comprobatória, então nenhum token é
  descartado e eventual ambiguidade fica pendente.

## Rollout

Aplicar somente ao caminho canônico `automacao_gd/`; validar primeiro com fixtures, depois com
homologação local somente leitura, sem produção.

## Rollback

Reverter parser/normalização/validação e restaurar a versão técnica anterior. Nenhum dado
histórico precisa ser desfeito porque esta etapa não executa backfill.

## Riscos

- layouts paralelos diferentes podem permanecer pendentes em vez de serem inferidos;
- deduplicação pode ocultar distribuição individual incerta, mitigada por aviso e quantidade total;
- caches V4 incompletos devem ser rejeitados para evitar falso sucesso.

## Decisões pendentes

Nenhuma para a Etapa 1.1.

## Evidências de homologação

Serão preenchidas após RED, GREEN, quality gates e auditoria opcional somente leitura.
