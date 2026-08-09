# Classificação e normalização de fabricantes

Status: aceita; complementa `equipment_format_v2.md` na Etapa 1.

Atualização Etapa 2C.6: a versão técnica canônica passa a ser V7 com
`equipment-v2-technical-v7-rules-7`. O plano rules-4 permanece apenas histórico aplicado, e o
plano rules-5 permanece histórico gerado/não aplicado, sem elegibilidade para aplicação.

## Problema e evidência

A regra anterior preservava `SOLPLANET/AISWEI` como marca composta e permitia que aliases do
mesmo equipamento gerassem itens duplicados. `GOKIN` também pode vazar de uma coluna de módulos
para a identidade de inversores em layouts paralelos.

## Decisão SOLPLANET/AISWEI

SOLPLANET e AISWEI representam o mesmo fabricante para esta automação. O nome canônico é:

```text
SOLPLANET
```

Entradas isoladas ou combinações, em qualquer caixa, espaçamento ou ordem, usando `/`, `-` ou
espaços, devem resultar em `SOLPLANET`. A regra é aplicada antes da deduplicação e não apenas
na formatação.

Ela também precede a remoção de prefixo redundante do modelo: fabricante `SOLPLANET` com
modelo iniciado por `AISWEI` produz um único fabricante `SOLPLANET`, sem alterar o restante do
código do modelo.

A remoção é estritamente uma operação de prefixo por tokens completos e consecutivos. Depois do
primeiro token não alias, nenhum fabricante é removido. Assim, `ACME + X ACME PRO` preserva
`X ACME PRO`, `JA + JAM66D45` preserva `JAM66D45`, enquanto repetições iniciais de LEAPTON ou
aliases AISWEI/SOLPLANET podem ser removidas.

Rótulos como MÃ“DULO e INVERSOR não são aliases de fabricante. Só podem ser descartados quando a
origem tipada comprova contaminação por cabeçalho/rótulo/prefixo gerado. Em catálogo ou origem
desconhecida permanecem no modelo e, quando ambíguos, exigem conferência.

Na V7/rules-7, a limpeza segura segue esta ordem:

```text
tokens brutos
â†’ origem tipada
â†’ remoção de rótulo estrutural comprovado
â†’ remoção de fabricante duplicado apenas no prefixo contaminado
â†’ validação canônica novamente
```

Fabricante repetido em posição interna continua preservado quando a origem não comprova
contaminação estrutural. Assim, `ACME + X ACME PRO` não pode ser reduzido por regra genérica.

Quando uma linha histórica possui `Placa` vazia e o campo `Inversor` mistura tokens de módulo e
inversor, a validação do backfill deve comparar a linha combinada atual (`Placa + Inversor`) com a
linha combinada proposta e com a coleção documental aprovada. Tokens transferidos do `Inversor`
para `Placa` não são perda semântica se fabricante, modelo, quantidade, potência, unidade e
atributos permanecerem representados no conjunto final. A validação continua bloqueando qualquer
equipamento real adicional presente apenas na linha atual.

Aliases comerciais documentados no próprio modelo, como `SOLIS` em modelos de GINLONG (SOLIS) ou
`SAJ` em modelos da Guangzhou Sanjing, não são classificados como fabricante duplicado. A remoção
de fabricante duplicado só é aceita quando o token adicional não pertence ao modelo documental e a
limpeza não altera modelo, potência, quantidade ou atributos.

Exemplo:

```text
1x SOLPLANET ASW6000-S-G2
```

Nunca gerar `SOLPLANET/AISWEI` nem duas linhas para o mesmo modelo.

## Decisão GOKIN

- `GOKIN` é fabricante exclusivo de módulo fotovoltaico.
- Ã‰ válido em `Placa`.
- Ã‰ inválido como inversor ou microinversor.
- Se for a única identidade de inversor, não se inventa fabricante nem se move modelo ambíguo;
  o protocolo recebe `pending_technical_review` e não atualiza Excel.
- GOKIN nunca é descartado silenciosamente da origem. Em seção de inversor ou
  microinversor ele produz violação estruturada `MODULE_BRAND_IN_INVERTER_SECTION`, severidade
  `error`, bloqueante e com `source_section` correspondente.
- A violação permanece bloqueante quando HUAWEI ou qualquer outra identidade válida coexistir.
- Menção de GOKIN fora das seções de equipamento não gera falso positivo.

## Fonte central e deduplicação

Aliases e fabricantes exclusivos por tipo ficam centralizados no domínio. A chave de
deduplicação é:

```text
tipo + fabricante canônico + modelo normalizado
```

Aliases do mesmo modelo produzem um item. Modelos distintos do mesmo fabricante são preservados.
Quantidades não são somadas ou distribuídas sem evidência; a quantidade total e um aviso são
mantidos quando necessário.

Fabricantes corporativos multi-token podem ser reconhecidos como uma identidade canônica somente
por lista explícita e determinística. A comparação canônica deve aceitar diferenças de pontuação,
espaços e forma corporativa comprovadamente equivalente, mas continua bloqueando perda de
fabricante, modelo, quantidade, potência, unidade, MPPT, tensão, AFCI, fase ou equipamento
adicional.

Potências unitárias de módulo preservam a unidade documental (`W`, `Wp`, `kW`, `kWp`) quando ela
existe. Quando a extração textual perde `W`, a unidade só pode ser restaurada se houver evidência
aritmética direta entre quantidade e potência total do arranjo; números incorporados a códigos
alfanuméricos, como `JKM625N`, não recebem `W` por inferência.


## Classificacao de acao no backfill

Equivalencia canonica nao basta para classificar uma linha como `NO_CHANGE` quando o texto gravado na planilha ainda contem contaminacao que deve ser saneada. Se `Placa` ou `Inversor` atuais diferem dos textos propostos e a validacao tecnica/semantica estiver aprovada sem violacoes bloqueantes, a acao deve ser `UPDATE_EQUIPMENT` quando houver ao menos uma destas evidencias aprovadas:

```text
DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED
SOLPLANET_ALIAS_NORMALIZED
PROVEN_STRUCTURAL_CONTAMINATION_REMOVED
CROSS_FIELD_CONTAMINATION_RESOLVED
GENERIC_LABEL_REMOVED
```

Continuam como `NO_CHANGE` as diferencas puramente cosmeticas: caixa, capitalizacao, espacamento, pontuacao, normalizacao de forma juridica ou alias documentado que pertence ao modelo, como `SOLIS` ou `SAJ` no inicio do modelo registrado na origem.## Critérios de aceite

- [ ] `Solplanet`, `AISWEI` e todas as combinações previstas resultam `SOLPLANET`.
- [ ] Dois aliases com `ASW6000-S-G2` geram `1x SOLPLANET ASW6000-S-G2`.
- [ ] `ASW5000-S` e `ASW6000-S` permanecem dois modelos SOLPLANET.
- [ ] GOKIN permanece formatado como módulo.
- [ ] GOKIN nunca aparece no texto de inversor ou microinversor.
- [ ] GOKIN como única identidade de inversor bloqueia Excel e exige conferência.
- [ ] HUAWEI e GOKIN na mesma seção preservam a violação bloqueante.
- [ ] GOKIN em microinversor preserva a violação bloqueante.
- [ ] Testes usam apenas dados sintéticos e anonimizados.
- [ ] Fabricante interno ou correspondência parcial nunca é removido.
- [ ] Rótulo genérico só é removido com `EquipmentSourceType` comprobatório.
- [ ] V7/rules-7 corrige os falsos bloqueios do validador sem alterar o parser V6.
- [ ] A comparação combinada entre `Placa` e `Inversor` aprova separação segura de
      contaminação cruzada.
- [ ] A comparação combinada continua bloqueando equipamento real perdido.
- [ ] Aliases documentados como parte do modelo não são removidos como duplicidade.
- [ ] Fabricante duplicado comprovadamente textual pode ser limpo sem perda semântica.
- [ ] Unidade `W` perdida só é restaurada com evidência documental/aritmética segura.
- [ ] Fabricantes corporativos multi-token equivalentes não geram regressão semântica falsa.
- [ ] Os 19 casos de múltiplos equipamentos legítimos permanecem preservados.
- [ ] O caso `SOURCE_INCOMPLETE` não é preenchido por inferência.

## Rollback e riscos

Rollback: restaurar o mapa anterior e os testes. Risco principal: novo alias não conhecido;
nessa situação o valor é preservado ou fica pendente, nunca inferido por protocolo/modelo.


