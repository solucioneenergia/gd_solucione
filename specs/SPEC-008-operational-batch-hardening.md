# SPEC-008 — Hardening operacional para lote sintético de 10 protocolos

Status: parcialmente substituída pela SPEC-009

> A capacidade sintética de teste com 10 itens permanece como teste de limite técnico. A
> SPEC-009 passa a ser a fonte de verdade operacional: produção e piloto continuam limitados a
> no máximo 5 protocolos e esta SPEC não autoriza lote 10 real.

## Objetivo

Adicionar controles técnicos mínimos para que a automação possa ser validada
offline para um futuro pré-voo/canário de até 10 protocolos, sem autorizar
lote 10 em produção.

## Escopo

- Separar capacidade técnica de autorização operacional.
- Criar política tipada `BatchAuthorizationPolicy`.
- Manter produção controlada limitada a 5 protocolos.
- Permitir política sintética de 10 somente por injeção em testes.
- Parametrizar a confirmação forte da opção 5.
- Implementar mutex global interprocesso para o pipeline operacional.
- Consolidar lote congelado imutável após deduplicação e limite global.
- Validar limite 10, bloqueio do 11º, PDFs reutilizados, retomada e
  `PROCESS_EXISTING_AFTER_SKIP=true` com fixtures sintéticas.

## Fora do escopo

- Autorizar lote 10 em produção.
- Executar canário real.
- Acessar Portal GD, planilha oficial, PDFs reais, unidades Y: ou Z:.
- Alterar desktop, frontend, bridges desktop ou releases.
- Inserir protocolos ausentes, mover linhas entre abas ou corrigir anomalias
  automaticamente.

## Requisitos funcionais

### RF-001 — Política de autorização

Dado um `requested_batch_limit` derivado de `MAX_COMPLETED_TO_PROCESS`,
quando a política padrão for usada,
então `authorized_max_protocols` deve ser 5 e `authorization_scope` deve ser
`CONTROLLED_PRODUCTION_V2_0_1`.

Dado uma política sintética injetada em teste,
quando `authorized_max_protocols=10`,
então a validação deve permitir lote 10 apenas naquele caminho injetado.

Erros:

- `BATCH_LIMIT_NOT_AUTHORIZED` para limite solicitado maior que autorizado.
- `BATCH_LIMIT_NOT_AUTHORIZED` para limite zero, negativo ou política inválida.

### RF-002 — Confirmação forte parametrizada

A função única `build_option5_strong_confirmation(protocol_limit)` deve gerar:

- `APLICAR OPÇÃO 5 COM CONCLUSÃO EM 5 PROTOCOLOS`
- `APLICAR OPÇÃO 5 COM CONCLUSÃO EM 10 PROTOCOLOS`

A validação deve rejeitar `SIM`, frases parciais, frase de 5 para lote 10,
frase de 10 para política de produção 5 e qualquer texto extra.

Erro:

- `STRONG_CONFIRMATION_MISMATCH`.

### RF-003 — Mutex global

O pipeline compartilhado deve adquirir `data/locks/option5_execution.lock`
antes de preflight externo, Portal, workbook, PDF, download, backup ou
arquivamento.

O lock deve:

- usar `msvcrt.locking` no Windows e `fcntl.flock` em POSIX;
- manter file descriptor aberto durante o pipeline;
- registrar metadata sanitizada;
- bloquear segunda instância com `GLOBAL_EXECUTION_LOCKED`;
- bloquear reentrada com `GLOBAL_EXECUTION_LOCK_REENTRANT`;
- liberar após sucesso, exceção e retorno antecipado.

Arquivo persistente sem lock ativo não pode bloquear nova execução.

### RF-004 — Lote congelado

Após reunir candidatos, aplicar retomada/skip e deduplicar, o sistema deve
criar `FrozenProtocolBatch` imutável.

Invariantes:

- `len(protocols) <= requested_limit`;
- `requested_limit <= authorized_limit`;
- protocolos únicos;
- ordem determinística;
- nenhum protocolo adicionado após freeze;
- duplicata entre origens consome uma vaga;
- PDF reutilizado e protocolo retomado consomem vaga.

Erro:

- `FROZEN_BATCH_SCOPE_VIOLATION`.

### RF-005 — Relatório operacional

O payload deve registrar:

- `requested_batch_limit`;
- `authorized_batch_limit`;
- `authorization_scope`;
- `strong_confirmation_contract`;
- `global_lock_acquired`;
- `global_lock_status`;
- `frozen_batch_created`;
- `protocols_unique_before_limit`;
- `protocols_selected_by_global_limit`;
- `protocols_dropped_by_global_limit`;
- `protocols_added_after_limit`;
- `duplicate_protocols_in_frozen_batch`;
- `synthetic_validation`;
- `batch_10_authorized_for_production=false`.

## Segurança

A OP-2 deve ser validada somente com testes sintéticos/offline. Nenhum teste
deve acessar Portal, `127.0.0.1:9222`, planilha oficial, PDFs reais, pastas de
cliente, rede ou unidades Y:/Z:.

## Critérios para OP-3

OP-3 só pode ser iniciada quando:

- confirmação parametrizada estiver testada;
- política padrão continuar limitada a 5;
- política sintética de 10 estiver disponível apenas por injeção;
- mutex interprocesso estiver certificado offline;
- lote sintético de 10 selecionar exatamente 10 e descartar o 11º;
- `protocols_added_after_freeze=0`;
- quality gates da OP-2 estiverem aprovados;
- lote 10 continuar não autorizado para produção.

## Declaração de autorização

A existência de suporte técnico para lote 10 não constitui autorização de
produção.
