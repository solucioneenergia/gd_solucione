# SPEC-003 — Sincronização da data de conclusão dos projetos

Status: Em implementação
Data: 2026-07-28
Responsável: Codex
Revisores: Operação GD Neoenergia
ADRs relacionadas: nenhuma
Issues relacionadas: Etapa 4.3

## Contexto

A versão `v2.0.1` da Automação GD Neoenergia está liberada para produção controlada, com backfill histórico encerrado, parser V6, validador V7, aplicação rules-7 e limite global por protocolo aprovados.

A funcionalidade de sincronização da coluna `Conclusão` foi criada como serviço isolado na Etapa 4.0 e validada em dry-run restrito aos protocolos da opção 5 na Etapa 4.1. A decisão atual é integrar definitivamente essa sincronização ao pipeline CDP completo da opção 5.

## Problema

A opção 5 processa solicitações concluídas, baixa ou reutiliza o Orçamento de Conexão, atualiza equipamentos e arquiva PDFs. Porém, a coluna `Conclusão` pode permanecer vazia ou desatualizada porque a data correta não está no PDF: ela aparece na tela de detalhes do Portal GD, no bloco da linha do tempo `Ponto de Conexão Aprovado`.

Manter a sincronização como opção separada cria risco operacional: o operador pode concluir equipamentos e arquivamento sem registrar a conclusão correspondente.

## Objetivo

Integrar à opção 5 a leitura e aplicação da data de conclusão para os mesmos protocolos selecionados pelo pipeline CDP completo:

1. abrir detalhes da solicitação concluída;
2. extrair estruturalmente a data do bloco `Ponto de Conexão Aprovado`;
3. baixar ou reutilizar o Orçamento de Conexão;
4. processar módulos e inversores sem alterar a semântica validada;
5. aplicar, em transação única, as alterações seguras em `Conclusão`, `Placa` e `Inversor`;
6. arquivar PDFs somente após aplicação segura;
7. registrar métricas e motivos por protocolo.

## Escopo

- Reutilizar internamente `completion_sync_service.py` e seus modelos.
- Integrar a decisão de conclusão ao processamento da opção 5.
- Extrair a data somente do bloco `Ponto de Conexão Aprovado`.
- Abrir detalhes mesmo quando o PDF local for reutilizado.
- Incluir `completion_no_change` na decisão de linha já atualizada.
- Aplicar data como data real do Excel com `number_format = dd/mm/yyyy`.
- Aplicar `EM ABERTO` como texto quando a data do bloco autorizado não estiver disponível.
- Remover a opção 7 como operação separada do menu CLI.
- Atualizar relatórios operacionais da opção 5.

## Fora do escopo

- Parser PDF V6.
- Validador V7 de equipamentos.
- Backfill histórico rules-4/rules-7.
- Criação de rules-8.
- Arquivamento independente de PDFs.
- Catálogo paralelo de protocolos.
- Consulta de projetos fora do lote selecionado pela opção 5.
- Integração automática com etapas futuras do roadmap.

## Fonte dos protocolos

A opção 5 continua sendo a fonte exclusiva:

```text
Minhas Solicitações
→ filtro Concluídos
→ paginação
→ deduplicação por protocolo
→ limite global MAX_COMPLETED_TO_PROCESS
→ processamento do lote selecionado
```

A sincronização de conclusão só pode ocorrer para protocolos desse lote. Não deve consultar a planilha inteira, outros filtros do Portal ou PDFs locais sem vínculo rastreável com a opção 5.

Origem registrada:

```text
option_5_completed_pipeline
```

## Extração estrutural da data

A tela de detalhes pode conter várias etapas com textos `Concluído em dd/mm/aaaa`. A única etapa autorizada é:

```text
Ponto de Conexão Aprovado
```

Regra obrigatória:

```text
localizar o bloco cujo rótulo normalizado seja Ponto de Conexão Aprovado
→ limitar a busca ao contêiner dessa etapa
→ extrair o texto Concluído em dd/mm/aaaa desse mesmo bloco
```

Não é permitido usar como regra principal:

- primeira ocorrência global de `Concluído em`;
- última ocorrência global;
- maior data encontrada;
- regex global sobre toda a página;
- data da etapa `Solicitação Concluída`;
- data de ingresso da tabela.

O resultado da extração deve registrar:

```text
completion_date_raw
completion_date_normalized
completion_source_stage = PONTO_DE_CONEXAO_APROVADO
completion_source_selector
completion_extraction_status
```

Ambiguidade no bloco autorizado gera pendência e não escolhe data arbitrária.

## PDF novo e PDF reutilizado

A leitura da data é independente do download:

```text
PDF novo:
abrir detalhes → extrair data → baixar PDF

PDF reutilizado:
abrir detalhes → extrair data → reutilizar PDF local válido
```

PDF existente não autoriza pular a tela de detalhes.

## Regras da coluna Conclusão

### Data encontrada

Portal:

```text
Ponto de Conexão Aprovado
Concluído em dd/mm/aaaa
```

Planilha:

```text
Conclusão = data real do Excel
number_format = dd/mm/yyyy
```

### Data ausente no bloco autorizado

Quando o protocolo pertence ao filtro `Concluídos`, mas o bloco `Ponto de Conexão Aprovado` não apresenta data:

```text
Conclusão = EM ABERTO
motivo = POINT_OF_CONNECTION_COMPLETION_DATE_NOT_AVAILABLE
```

`EM ABERTO` significa `data de conclusão não disponível`; não significa que a solicitação deixou de pertencer ao filtro `Concluídos`.

### Valor já igual

Mesma data:

```text
NO_CHANGE
```

`EM ABERTO` atual e data ainda ausente:

```text
NO_CHANGE
```

### EM ABERTO passa a ter data

Planilha:

```text
EM ABERTO
```

Portal:

```text
data válida
```

Resultado:

```text
substituir EM ABERTO pela data
```

### Data divergente

Planilha possui uma data e o Portal apresenta outra:

```text
PENDING_REVIEW
COMPLETION_DATE_CONFLICT
```

Não sobrescrever automaticamente.

### Data existente e Portal sem data

Planilha possui data válida e o Portal não apresenta data:

```text
PENDING_REVIEW
PORTAL_COMPLETION_DATE_REGRESSION
```

Nunca substituir automaticamente uma data por `EM ABERTO`.

### Data inválida

```text
PENDING_REVIEW
INVALID_COMPLETION_DATE
```

Não inferir nem corrigir datas.

## Decisão de NO_CHANGE

A opção 5 deve avaliar separadamente:

```text
ingress_no_change
equipment_no_change
completion_no_change
```

Exemplos:

- Equipamentos iguais e `Conclusão` vazia: atualizar somente `Conclusão`.
- `Conclusão` igual e equipamento divergente: atualizar somente `Placa`/`Inversor`.
- Tudo igual: classificar como já atualizado.

Para solicitações concluídas da opção 5, `Conclusão` não pode ser considerada
`completion_no_change=True` quando a célula da planilha estiver vazia e o Portal/plano não
trouxer data ou valor canônico de conclusão. Nesse caso, o protocolo deve ser bloqueado para
revisão/retomada operacional com erro específico de conclusão indisponível, sem aplicar
atualização parcial de equipamentos na mesma linha. Se houver data ou valor `EM ABERTO`
canônico no plano/metadata, a automação deve preencher a coluna `Conclusão`.

## Transação do Excel

Quando um protocolo possuir alterações seguras em `Conclusão`, `Placa` ou `Inversor`, elas devem ser aplicadas na mesma transação controlada:

```text
backup único
→ temporário único no mesmo volume
→ allowlist por célula
→ comparação integral
→ substituição atômica única
```

Allowlist da opção 5:

```text
Conclusão
Placa
Inversor
```

Somente as colunas efetivamente propostas podem mudar.

## Menu e UX

A opção separada foi removida:

```text
7 - Sincronizar datas de conclusão
```

A sincronização passa a ser comportamento interno da opção 5 em produção e em simulação:

```text
5 - Executar pipeline CDP completo
```

## Relatórios e console

O relatório da opção 5 deve incluir, por protocolo:

```text
protocol
completion_current
completion_raw
completion_normalized
completion_source_stage
completion_action
completion_reason
completion_excel_updated
```

O resumo deve separar:

```text
Datas de conclusão encontradas
Conclusões atualizadas
EM ABERTO aplicados
Conclusões sem alteração
Conclusões pendentes
```

## Segurança

Preservar:

- `APP_ENV=production`;
- confirmação de execução real da opção 5;
- pré-voo;
- unidade de rede;
- planilha fechada;
- backup validado;
- fingerprints;
- temporário no mesmo volume;
- comparação integral;
- allowlist;
- substituição atômica;
- rollback;
- SHA antes/depois;
- limite global;
- isolamento de pendências.

Pendências individuais de conclusão bloqueiam apenas a coluna `Conclusão` daquele protocolo. Equipamentos seguros do mesmo protocolo podem continuar sendo aplicados.

## Testes obrigatórios

- Página com múltiplas datas extrai somente a data do bloco `Ponto de Conexão Aprovado`.
- Etapa ausente ou sem data propõe `EM ABERTO` para solicitação concluída.
- Etapa ambígua gera pendência.
- PDF novo abre detalhes, extrai data e baixa PDF.
- PDF reutilizado abre detalhes, extrai data e não baixa novamente.
- Equipamento `NO_CHANGE` e conclusão vazia atualiza somente `Conclusão`.
- Conclusão `NO_CHANGE` e equipamento divergente atualiza somente equipamentos.
- Tudo `NO_CHANGE` não escreve.
- Data real é gravada como data Excel com formato `dd/mm/yyyy`.
- `EM ABERTO` é gravado como texto.
- Data existente é preservada quando o Portal não disponibiliza data.
- Menu não exibe opção 7.
- Regressão do pipeline v2.0.1 permanece verde.

## Rollout

1. Implementar integração com TDD direcionado.
2. Executar dry-run integrado da opção 5 com `DRY_RUN=true` e `MAX_COMPLETED_TO_PROCESS=5`.
3. Somente com autorização forte, executar canário real da opção 5 com conclusão integrada.

Frase forte do canário real:

```text
APLICAR OPÇÃO 5 COM CONCLUSÃO EM 5 PROTOCOLOS
```

## Critérios de aceite da Etapa 4.3

- [ ] Data extraída do bloco correto.
- [ ] Nenhuma data de outra etapa capturada.
- [ ] PDF reutilizado ainda abre detalhes.
- [ ] Opção 5 integra conclusão.
- [ ] Opção 7 removida do menu.
- [ ] `NO_CHANGE` avalia conclusão separadamente.
- [ ] Data gravada como data Excel.
- [ ] `EM ABERTO` gravado como texto.
- [ ] Transação única preservada.
- [ ] Limite global preservado.
- [ ] Dry-run integrado aprovado.
- [ ] Canário real executado somente com autorização forte.
