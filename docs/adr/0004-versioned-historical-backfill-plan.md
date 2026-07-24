# ADR 0004 — Plano versionado para backfill histórico

Status: aceita  
Data: 2026-07-22  
Decisores: Automação GD Neoenergia  
SPEC relacionada: [SPEC-002](../../specs/SPEC-002-historical-equipment-backfill.md)  
Substitui: —  
Substituída por: —

## Contexto

A auditoria e a aplicação ocorrem em momentos diferentes. A planilha e as regras podem mudar
entre ambos.

## Problema arquitetural

É necessário transportar propostas auditáveis sem confiar em JSON editado, caminhos, timestamps
instáveis ou flags de sucesso.

## Forças de decisão

Segurança, revisão humana, determinismo, portabilidade, privacidade, idempotência e simplicidade
operacional.

## Decisão

Persistir plano JSON canônico com `plan_version`, versões técnicas, fingerprint do workbook,
itens tipados, fingerprint de cada linha e `plan_hash`. O hash é SHA-256 da representação JSON
canônica sem o próprio campo `plan_hash`.

A evolução semântica rules-2 usou `plan_version = 2` porque tornou obrigatórios, em cada item, status,
erros, avisos, violações e fingerprints semânticos. O plano também persiste status e resumo do
quality gate. A versão de regras passa a `equipment-v2-technical-v4-rules-2`; planos V1 ou
rules-1 são incompatíveis e precisam ser integralmente regenerados. O formato técnico do cache
permaneceu V4 naquele ciclo.

A Etapa 2.2 adota `plan_version = 3`: cada item passa a persistir as coleções canônicas atual e
proposta, incluindo origem tipada, quantidades individuais, potência/unidade, atributos,
violações e avisos. O cache passa a V5 e as regras a
`equipment-v2-technical-v5-rules-4`. Plano V1/V2, cache V4 ou rules-1/rules-2/rules-3 são incompatíveis e
devem ser regenerados a partir do PDF. O hash canônico abrange integralmente as coleções.

A rules-4 também vincula plano e relatórios por `execution_id`, derivado do instante de criação
e do fingerprint do workbook. Os três artefatos compartilham `execution_id`, `created_at`,
`plan_hash`, `workbook_fingerprint` e todas as versões. Essa identidade não substitui o hash do
plano nem autoriza a aplicação; ela torna divergências entre relatórios explicitamente detectáveis.

`CanonicalEquipmentCollection` é a fonte técnica única do parser à formatação. Modelos legados
são adaptadores de fronteira, nunca fonte do quality gate. Esta decisão evita que conversões
intermediárias descartem origem, quantidade individual ou tokens técnicos.

## Alternativas consideradas

- aplicar imediatamente após auditoria: rejeitada por impedir revisão;
- confiar em arquivo editável sem hash: rejeitada;
- assinatura assimétrica: adiada por complexidade sem infraestrutura existente;
- banco de migrações: rejeitado por ampliar escopo.

## Consequências positivas

Detecção de edição, versão obsoleta e conflito por linha; auditoria reproduzível; nenhum dado
pessoal necessário.

## Consequências negativas

O plano deve ser regenerado quando workbook ou regras mudarem; hash não comprova autoria, apenas
integridade contra edição acidental ou não aprovada.

## Riscos

Serialização não determinística e inclusão acidental de caminhos. Mitigados por JSON canônico,
Enums e allowlist.

## Segurança

O plano não autoriza produção sozinho. Pré-voo, ambiente, confirmação forte, backup e fingerprints
continuam obrigatórios.

A aplicação rules-4 separa preparação somente leitura de execução confirmada. A confirmação é
derivada do plano, o backup é validado por hash e abertura, e a substituição somente ocorre depois
da validação integral de um temporário no mesmo volume. Falha posterior aciona rollback automático
e produz estado tipado. Essa evolução não altera schema, hash, fingerprints ou semântica do plano V3.

## Compatibilidade

Formato JSON independente de Windows/Linux. Protocolo sempre textual.

## Plano de implementação

Modelos no domínio, funções puras para hash/validação e adaptadores separados de auditoria e
aplicação.

## Plano de rollback

Planos incompatíveis, coleções incompletas ou com quality gate inválido são rejeitados antes de abrir a planilha;
remover o comando não altera planilha nem estado. Backups da
aplicação seguem o procedimento da SPEC-002.

## Validação da decisão

Testes de determinismo, adulteração, versões, privacidade, conflito e idempotência.

## Evidências

Preenchidas pelos testes e pela auditoria 2A da implementação.

## Referências

- SPEC-002
- ADR-0002 — Persistência local atômica
