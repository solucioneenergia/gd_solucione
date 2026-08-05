# Runbook de produção controlada — Automação GD Neoenergia

## Estado liberado

- Versão operacional: `v2.0.1`
- Produção controlada: AUTHORIZED
- SHA oficial da planilha: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`
- Operação ampla: bloqueada até autorização operacional explícita.

## Preparação

1. Verificar unidade Z: disponível.
2. Confirmar que a planilha não está aberta.
3. Abrir Edge com CDP.
4. Fazer login manual no Portal GD.
5. Confirmar endpoint `127.0.0.1:9222/json/version`.
6. Ativar ambiente virtual.
7. Confirmar `APP_ENV=production`.

## Configuração inicial

```env
APP_ENV=production
DRY_RUN=false
APPLY_EXCEL=true
APPLY_ARCHIVE=true
RESUME_PIPELINE=true
SKIP_ALREADY_COMPLETED=true
RESET_PIPELINE_STATE=false
MAX_COMPLETED_TO_PROCESS=5
PROCESS_EXISTING_AFTER_SKIP=true
```

`MAX_COMPLETED_TO_PROCESS` é limite global de protocolos únicos analisados na
execução. Ele inclui protocolos novos do Portal, PDFs reutilizados, retomadas e
itens adicionados por `PROCESS_EXISTING_AFTER_SKIP=true`. Nenhuma fase posterior
deve adicionar protocolos acima desse limite.

Antes de selecionar o lote operacional, a opção 5 executa uma reconciliação
global read-only entre todos os protocolos do filtro `Concluídos` do Portal e
todos os protocolos existentes na planilha oficial. Essa auditoria não baixa
PDFs, não abre detalhes, não arquiva documentos, não cria backup e não grava a
planilha. O processamento operacional continua limitado por
`MAX_COMPLETED_TO_PROCESS`.

`MAX_PORTAL_PAGES` é apenas teto defensivo contra loop de paginação. Ele não
representa a quantidade esperada de páginas. Se o teto for atingido e o Portal
ainda indicar próxima página, a reconciliação será marcada como `PARCIAL`, o
lote operacional será bloqueado e nenhum protocolo será processado.

## Execução

```text
python app.py
selecionar opção 5
confirmar com SIM
```

## Resultado esperado

```text
Status: SUCESSO ou PARCIAL
Páginas lidas > 0
Protocolos analisados <= MAX_COMPLETED_TO_PROCESS
Erros sistêmicos = 0
Mudanças fora da allowlist = 0
```

O resumo também deve apresentar a seção `Reconciliação Portal × planilha` com:

```text
Escopo: GLOBAL ou PARCIAL
Páginas lidas
Última página confirmada
Concluídos únicos no Portal
Protocolos únicos na planilha
Encontrados nas duas fontes
Ausentes na planilha
Somente na planilha
Duplicados na planilha
Em aba anual incorreta
Conclusões vazias
Equipamentos vazios para revisão
Registros incompletos
Relatório portal_workbook_reconciliation_<timestamp>
```

Achados de reconciliação são insumo para saneamento posterior e não ampliam o
lote operacional da execução corrente.

## Condições de interrupção

Interromper e diagnosticar se ocorrer:

```text
ENVIRONMENT_NOT_PRODUCTION
WORKBOOK_LOCKED
NETWORK_DRIVE_UNAVAILABLE
BACKUP_VALIDATION_FAILED
UNEXPECTED_WORKBOOK_CHANGE
ATOMIC_REPLACE_FAILED
ROLLBACK_FAILED
```

## Pendências individuais

As pendências abaixo bloqueiam somente o protocolo correspondente e não devem bloquear protocolos seguros do mesmo lote:

```text
MODULE_QUANTITY_MISSING
INVERTER_QUANTITY_MISSING
SOURCE_INCOMPLETE
SOURCE_AMBIGUOUS
```

Protocolos protegidos atualmente como `PENDING_REVIEW/SOURCE_INCOMPLETE`:

```text
2605250167
2605148473
2605056663
2604275348
2603166924
2602098916
```

Regras:

- não inferir quantidade;
- não inferir quantidade individual por modelo ou fabricante;
- não atualizar Excel para protocolo pendente;
- não arquivar como concluído;
- manter o PDF disponível para retomada ou conferência;
- registrar a pendência no relatório.

## Retomada

Usar:

```env
RESUME_PIPELINE=true
SKIP_ALREADY_COMPLETED=true
RESET_PIPELINE_STATE=false
```

Não usar `RESET_PIPELINE_STATE=true` em produção controlada sem autorização operacional explícita.

## Sincronizacao de datas de conclusao - v2.1.0

Estado atual: funcionalidade integrada ao pipeline CDP completo da opcao 5 em implementacao/homologacao controlada. A opcao 7 nao deve ser usada como operacao separada.

Na opcao 5, para cada protocolo selecionado no filtro `Concluidos`, o sistema deve:

```text
abrir detalhes da solicitacao
extrair a data do bloco Ponto de Conexao Aprovado
baixar ou reutilizar o Orcamento de Conexao
processar equipamentos
aplicar Conclusao, Placa e Inversor em transacao unica quando houver mudancas seguras
arquivar o PDF somente apos Excel seguro
```

Contrato:

- a chave de correspondencia e sempre `Protocolo`;
- a data autorizada vem somente do bloco `Ponto de Conexao Aprovado`, texto `Concluido em dd/mm/aaaa`;
- PDF reutilizado ainda exige abertura dos detalhes para leitura da data;
- data encontrada recebe data real do Excel com formato `dd/mm/yyyy`;
- ausencia de data no bloco autorizado recebe `EM ABERTO`;
- `EM ABERTO` significa `data de conclusao nao disponivel`, nao significa solicitacao fora de `Concluidos`;
- data divergente, data invalida ou regressao de data ficam em `PENDING_REVIEW`;
- a allowlist da opcao 5 passa a permitir somente as colunas efetivamente propostas entre `Conclusao`, `Placa` e `Inversor`;
- pendencia de conclusao bloqueia apenas a coluna `Conclusao` daquele protocolo;
- `MAX_COMPLETED_TO_PROCESS` continua limitando o lote global de protocolos da opcao 5;
- a operacao ampla permanece bloqueada ate autorizacao operacional explicita.

## Rollback

1. Identificar backup validado.
2. Confirmar SHA do backup.
3. Fechar Excel.
4. Restaurar somente conforme procedimento operacional aprovado.
5. Registrar ocorrência no ledger.
6. Não executar nova aplicação até diagnóstico.

## Implantação gradual

### Fase 1

```env
MAX_COMPLETED_TO_PROCESS=5
```

### Fase 2

```env
MAX_COMPLETED_TO_PROCESS=10
```

### Fase 3

Limite superior somente com autorização operacional explícita.

### Critério para progressão

- nenhum P0;
- nenhum P1;
- nenhuma mudança fora da allowlist;
- nenhum rollback;
- relatórios coerentes;
- pendências individuais isoladas.

Não alterar automaticamente o limite durante uma execução.

## P3 conhecido

Execução não interativa do menu pode gerar EOF após o pipeline quando entradas extras são enviadas por pipe.

- Classificação: P3.
- Impacto: sem impacto no uso interativo normal.
- Risco de dados: nenhum identificado.
- Ação: backlog; não bloqueia produção controlada.

## Saneamento direcionado da planilha

Aplicacoes direcionadas de saneamento so podem ocorrer com plano, pre-voo, confirmacao forte e allowlist especifica.

Contrato operacional:

1. Validar SHA atual da planilha oficial.
2. Revalidar hash do plano e do pre-voo.
3. Confirmar que o protocolo alvo ainda esta no estado aprovado.
4. Exigir confirmacao forte exata em interacao separada.
5. Criar backup integral validado por SHA.
6. Aplicar somente em temporario no mesmo volume.
7. Comparar workbook original x temporario e bloquear mudancas fora da allowlist.
8. Executar `os.replace` uma unica vez.
9. Reabrir o arquivo final e validar celula por celula.
10. Executar idempotencia read-only.

Para o saneamento do protocolo `2607077271`, a unica escrita autorizada foi a criacao da linha aprovada na aba `2026`, sem alterar registros existentes.

## Diagnostico visual e estrutural da planilha

A padronizacao visual/estrutural deve iniciar sempre por diagnostico read-only. A referencia inicial e a aba `2025`, mas a referencia nao pode ser propagada se o diagnostico apontar `CANONICAL_REFERENCE_INCONSISTENT`.

Contrato operacional:

1. Recalcular a SHA da planilha oficial antes da auditoria.
2. Abrir a planilha somente para leitura.
3. Auditar as abas `2022 - 2023`, `2024`, `2025` e `2026`.
4. Gerar fingerprint visual sem depender apenas de `style_id`.
5. Gerar plano com `apply_now=false` e `changes_content=false` para todas as acoes.
6. Confirmar `workbook.save=0`, `backup=0`, `temporario de aplicacao=0` e `os.replace=0`.
7. Bloquear aplicacao se houver alteracao de conteudo proposta ou inconsistencia na referencia canonica.

Resultado read-only de 2026-07-28:

- SHA preservada: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- acoes propostas: 75;
- acoes seguras para plano futuro: 27;
- acoes bloqueadas para revisao: 48;
- alteracoes de conteudo propostas: 0;
- decisao: `STAGE4_1_PARTIAL - CANONICAL_REFERENCE_REQUIRES_REVIEW`.

Resultado final read-only de 2026-07-29:

- SHA preservada: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- content hash antes/depois: `85a0eed71927b8123dfabda0f77f84ad9efd568a2fdeac2aa192643a7ccc9c61`;
- canonical policy hash: `e09a710cea0f29d47feb76324bcc4303173a52a48f21f97d438d900feedf0662`;
- final plan hash: `3589c169354395bbe4964699a10acfbc69a00ce2ec1039fca575a87bae6fd670`;
- acoes propostas: 40;
- acoes bloqueadas: 0;
- alteracoes de conteudo propostas: 0;
- `apply_now=true`: 0;
- decisao: `STAGE4_1_COMPLETE - CANONICAL_REFERENCE_STABILIZED_AND_READ_ONLY_PLAN_APPROVED`.

Aplicacao visual/estrutural certificada em 2026-07-29:

- SHA anterior autorizada: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- SHA do backup inicial: `7ea22f78b008c236dad7323cefff302f5f4c64aa47ca3f718d12dcc9077b8076`;
- SHA final certificada: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`;
- topologia: `MULTI_CYCLE_FULLY_AUDITED`;
- acoes planejadas/reconciliadas: 40/40;
- acoes pos-aplicacao: 0;
- mudancas de valor logico: 0;
- mudancas fisicas inesperadas: 0;
- decisao: `STAGE4_3_COMPLETE - VISUAL_STANDARDIZATION_APPLIED_AND_IDEMPOTENT`.

Novas auditorias visuais/estruturais devem usar a SHA final certificada como baseline oficial. A operacao ampla continua bloqueada ate autorizacao explicita.

## Inventario e manutencao segura do projeto

Diagnosticos de limpeza do projeto devem ser sempre read-only.

Contrato operacional:

1. Recalcular a SHA da planilha oficial antes do inventario.
2. Nao acessar Portal, PDFs, Edge/CDP ou pastas reais de clientes.
3. Nao ler `.env`, cookies, tokens, storage state ou perfis de navegador.
4. Registrar raizes externas como protegidas e sem varredura recursiva.
5. Classificar arquivos em protegidos, ativos, historicos, caches regeneraveis, temporarios, duplicados e revisao manual.
6. Manter `delete_now=false`, `move_now=false`, `archive_now=false` e `compress_now=false` para todos os candidatos.
7. Nao criar backup, temporario de aplicacao, zip, copia de limpeza ou pacote compartilhavel durante o diagnostico.
8. Gerar somente relatorios finais em `data/logs`.

Resultado da Etapa 5.1 de 2026-07-29:

- SHA da planilha preservada: `192084b5d780db5ad027aaa640ed418072cfafc4ca9b8b62648a987acc58594b`;
- arquivos inventariados: 1591;
- arquivos protegidos: 1526;
- grupos duplicados: 28;
- caches regeneraveis: 24;
- candidatos de exclusao segura: 0;
- acao automatica habilitada: 0;
- decisao: `STAGE5_1_REJECTED - FILESYSTEM_CHANGED_DURING_DIAGNOSTIC`.

A rejeicao ocorreu porque a propria execucao criou um arquivo temporario e relatorios invalidos em caminho Unicode/mojibake antes do inventario final. Esses arquivos foram apenas reportados; nao foram removidos durante a etapa.

Procedimento futuro corrigido para a Etapa 5.1B:

1. Executar o diagnostico em processo separado, sem rodar testes no mesmo processo.
2. Resolver a raiz canônica por `scripts/..` e validar a identidade fisica com Git apenas como verificacao.
3. Bloquear qualquer raiz paralela, caminho com mojibake ou `data/logs` fora da raiz canônica.
4. Manter snapshots de filesystem exclusivamente em memoria.
5. Renderizar os oito relatorios finais em memoria a partir de um unico bundle.
6. Confirmar `cleanup_plan_hash` identico no JSON e no Markdown antes da escrita.
7. Validar UTF-8 strict e bloquear mojibake em textos institucionais controlados.
8. Escrever somente os oito destinos finais com criacao exclusiva, sem temporarios e sem `os.replace`.
9. Comparar snapshot inicial e final permitindo apenas os oito relatorios finais.
10. Nao executar limpeza, compactacao, movimentacao, exclusao, pacote `.zip` ou nova release.

Disposicao terminal da Etapa 5.1C:

- artefatos-fonte congelados: `project_*stage5_1_20260729T153455Z.*`;
- execution id: `2e42348a7221cec8c9075a0a9994e82e1fac3991b251cbc043f18444934c93ae`;
- cleanup plan hash: `bccb5e1b58c8ee892685b7b39040e58bb91fafb44cb6a872b331a88a3290c9f2`;
- disposition hash: `2cd2e2e0b88cb5db2d2724d03c10fea610e75274e5f150a818e3428dbdae5390`;
- referencias quebradas: 6/6 com disposicao terminal;
- arquivos `UNKNOWN_REVIEW_REQUIRED`: 52/52 com disposicao terminal;
- grupos duplicados: 28/28 com disposicao terminal;
- total reconciliado: 86/86;
- achados internos em aberto: 0;
- acoes automaticas autorizadas: 0.

A Etapa 5.1 esta encerrada como inventario e triagem read-only. Nenhuma limpeza foi autorizada. A Etapa 5.2 deve permanecer bloqueada ate solicitacao operacional independente e explicita.

## OP-2 — Hardening operacional para lote 10

A OP-2 adiciona suporte tecnico sintético/offline para validar lote de 10,
mas nao autoriza lote 10 em producao.

Contrato operacional vigente:

1. A producao controlada permanece com `MAX_COMPLETED_TO_PROCESS=5`.
2. A politica padrao de autorizacao do pipeline e
   `CONTROLLED_PRODUCTION_V2_0_1`, com maximo autorizado de 5 protocolos.
3. A frase forte da opcao 5 e derivada do limite solicitado ja validado pela
   politica de autorizacao.
4. Solicitacao acima da politica autorizada falha com
   `BATCH_LIMIT_NOT_AUTHORIZED` antes de Portal, planilha, PDF, download ou
   arquivamento.
5. A politica `SYNTHETIC_BATCH10_VALIDATION` existe somente para testes e
   validacoes offline injetadas; ela nao deve ser carregada por `.env` nem pelo
   operador.
6. O mutex global usa `data/locks/option5_execution.lock` e deve ser adquirido
   antes de qualquer recurso externo.
7. Um arquivo de lock persistente nao significa lock ativo. Se ocorrer
   `GLOBAL_EXECUTION_LOCKED`, confirmar que nao ha outra execucao em andamento
   antes de tentar novamente.
8. O lote operacional e congelado apos deduplicacao e limite global; nenhuma
   fase posterior pode adicionar protocolos.
9. Lote 10 permanece bloqueado ate OP-3.
10. Operacao ampla permanece bloqueada ate autorizacao explicita.
