# Validação de equipamentos antes do Excel e erros operacionais

Status: aceita; complementa `equipment_format_v2.md` na Etapa 1.

## Problema

O fluxo atual pode gravar Placa/Inversor mesmo quando o parser produziu campo vazio, par inseguro,
quantidade ausente ou contaminação entre tipos. O cache técnico também marca `success` antes de
uma validação cruzada central.

## Contrato de validação

Antes de `update_excel_from_pdf_data()` ou de qualquer arquivamento conclusivo, deve existir:

- módulo com fabricante, modelo e quantidade válidos;
- inversor convencional ou microinversor com fabricante, modelo e quantidade válidos;
- textos finais de Placa e Inversor não vazios;
- pares seguros, sem fabricante exclusivo de módulo em inversor;
- ausência de sinais de modelo de módulo contaminando o inversor, incluindo `BIFACIAL`,
  `N-TYPE`, `MONOCRISTALINO`, `POLICRISTALINO`, `Wp`, potência unitária de módulo e fabricante
  classificado exclusivamente como módulo.

Sinais são usados como validação de contaminação, não como parser ou lista exaustiva.
Quando inversor convencional e microinversor coexistirem, a quantidade positiva de cada categoria
é obrigatória; a quantidade de uma não valida a outra.

## Resultado inválido

```text
action: pending_technical_review
technical_processing.status: pending_review
technical_review_required: true
```

Mensagem:

```text
Extração técnica inconclusiva: os dados de Placa e Inversor não puderam ser separados com segurança.
```

Ação recomendada:

```text
Confira a seção 3. GERAÇÃO do orçamento de conexão e reprocese o protocolo após corrigir o parser ou os dados de origem.
```

O resultado não chama Excel, não arquiva, não fica completed, preserva o PDF e não interrompe
o lote.

## Relatórios e estado

Cada protocolo inclui:

```text
technical_validation_status
technical_review_required
technical_validation_errors
technical_validation_warnings
module_source
inverter_source
```

`total_pending_review` inclui a pendência e `total_technical_pending_review` a diferencia. Nenhum
campo inclui texto integral do PDF ou dados pessoais.

O relatório público é construído por allowlist. Campos permitidos são status, modo, tempos e
totais; por protocolo: identificador, tipo/status do documento, ação, status resumido de Excel e
arquivo, validação técnica, erros/avisos técnicos, origens e ação recomendada. Caminhos,
nomes, dados pessoais, nomes de arquivo e dicionários internos não são copiados.

## Cache técnico V5

- Versão 4 ou inferior é obsoleta e exige reextração.
- V5 pendente, inválida, sem coleção canônica completa ou sem
  `technical_validation_status=approved` não é
  reutilizada.
- V5 persiste `CanonicalEquipmentCollection` completa e
  `equipment-v2-technical-v5-rules-4`; flags e textos isolados não são
  suficientes para reutilização.
- Cache V4/rules-2 é incompatível. V5 exige roundtrip lossless, origem tipada, quantidade por item,
  potência/unidade, atributos, violações e avisos.
- Reutilização reconstrói a estrutura, reexecuta as invariantes e compara os textos formatados.
- Validação aprovada persiste `status=validated`; `status=success` somente é confirmado depois
  de uma atualização do Excel bem-sucedida ou comprovadamente idempotente.
- Falha do Excel muda o estado para `operational_pending`, preserva a estrutura validada para
  retomada e impede arquivamento conclusivo.
- `EQUIPMENT_FORMAT_VERSION` permanece 2 porque o layout V2 das células não muda.
- Nenhuma linha histórica da planilha é atualizada nesta etapa.

## Critérios de aceite

- [ ] Placa vazia impede Excel.
- [ ] Inversor vazio sem microinversor impede Excel.
- [ ] Quantidade obrigatória ausente impede Excel.
- [ ] Contaminação e GOKIN em inversor impedem Excel e arquivamento.
- [ ] Resultado aprovado chama Excel normalmente.
- [ ] Pendência registra estado e contadores, permanece reprocessável e não interrompe o lote.
- [ ] Cache V4 é ignorado; V5 somente é reutilizado quando aprovado e completo.
- [ ] V5 com GOKIN, quantidade ausente, texto divergente ou fingerprint incompatível é
  reextraído.
- [ ] Sucesso técnico somente é persistido depois do Excel; falha de Excel permanece pendência
  operacional retomável.
- [ ] Relatório público contém somente a allowlist e nenhum caminho em qualquer profundidade.
- [ ] Status e exit codes da Etapa 0 permanecem inalterados.

## Segurança de logs

Console e arquivo persistente usam a mesma sanitização defensiva de mensagem, estruturas
aninhadas, exceções e traceback. Caminhos Windows/UNC/POSIX, e-mail, documentos, tokens, cookies,
conteúdo bruto e objetos completos de configuração são substituídos por marcadores. Protocolos
rotulados permanecem observáveis. Logs históricos são apenas auditados nesta etapa; sanitização
é comando separado, exige confirmação forte, backup e verificação, e não será executada sem nova
autorização.

## Testes, rollback e riscos

Testes usam mocks para provar ausência/presença de chamada ao Excel, arquivamento e conclusão.
Rollback reverte validação e versão do cache. O risco de falso positivo é mitigado usando
estrutura extraída e poucos sinais de contaminação; casos ambíguos ficam pendentes em vez de
serem gravados.

## Adendo 2026-08-09 - totais agregados em celula V2

Em celulas V2 com multiplos modelos, a quantidade valida pode ser o total agregado da categoria
quando o texto final preserva os modelos separados e inclui `Qtd. total`. Esse contrato e aceito
para a planilha porque a coluna continua recebendo um campo textual unico com os modelos e o total
da categoria. A validacao continua bloqueando modelo sem identidade, origem insegura,
contaminacao entre categorias, ausencia de total agregado positivo ou conflitos entre inversor
convencional e microinversor.
