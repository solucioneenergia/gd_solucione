# SPEC-002 — Auditoria e correção histórica de equipamentos

Status: aceita para implementação  
Data: 2026-07-22  
Responsável: Automação GD Neoenergia  
Revisores: revisão sênior independente  
ADRs relacionadas: [ADR-0004](../docs/adr/0004-versioned-historical-backfill-plan.md)  
Issues relacionadas: Etapa 2 do roadmap

## Contexto

Registros históricos podem conter equipamentos formatados por regras anteriores ao parser
técnico V4. A Etapa 1.1 tornou extração, validação e cache seguros; esta etapa usa essas regras
para auditar e, em execução separada e explicitamente autorizada, corrigir somente Placa e
Inversor.

## Problema

Há indícios históricos de Placa vazia, módulo contaminando Inversor, GOKIN no tipo errado,
aliases SOLPLANET/AISWEI duplicados e campos incompletos. Uma alteração direta impediria revisão,
rollback e detecção de concorrência entre simulação e aplicação.

## Evidências do comportamento atual

- referência anterior: cerca de 95 linhas únicas e 165 ocorrências de aliases;
- não existe auditoria histórica canônica nem plano aprovado entre simulação e aplicação;
- os protocolos 2503261731, 2505160008 e 2506022885 permanecem pendentes pelas invariantes
  técnicas, nunca por lista especial em produção.

## Objetivo

Produzir uma auditoria determinística e somente leitura, seguida por aplicação opcional protegida
por ambiente, confirmação forte, plano íntegro, fingerprints, backup, gravação atômica e
verificação pós-gravação.

## Escopo

- abas operacionais cujo nome contém ano e que possuam cabeçalhos inequívocos Protocolo, Placa e
  Inversor;
- localização de PDFs locais sem Portal;
- parser e validação V4 atuais;
- plano versionado, relatórios públicos e aplicação limitada às duas células do conjunto técnico.

## Fora do escopo

Portal, download, ACOMPANHAR, paginação, UI desktop, outras colunas, inferência por potência/nome,
alteração de PDF, cache operacional, estado de conclusão e aplicação real nesta entrega.

## Fonte técnica de verdade

PDF local cujo protocolo interno corresponda à linha, processado pelo parser atual e convertido em
`CanonicalEquipmentCollection`. Cache só pode ser usado após roundtrip lossless V5, revalidação
semântica rules-4 e fingerprint compatível.

## Classificação dos registros

`NO_CHANGE`, `UPDATE_EQUIPMENT`, `PENDING_TECHNICAL_REVIEW`, `PDF_NOT_FOUND`,
`PROTOCOL_MISSING`, `DUPLICATE_PROTOCOL`, `ROW_CONFLICT`, `INVALID_WORKBOOK_ROW` e `SKIPPED`.

## Regras de comparação

- protocolo é texto de dígitos; valores ausentes/ambíguos são rejeitados;
- igualdade semântica de tipo, fabricante canônico, modelo, quantidade, potência, unidade e
  atributos evita escrita por diferença apenas cosmética;
- divergência técnica aprovada propõe Placa e Inversor como conjunto indivisível;
- duplicidade em qualquer aba operacional bloqueia todas as ocorrências.

## Etapa 2.1 — qualidade semântica das propostas

Antes da formatação, cada equipamento possui representação canônica separando tipo,
fabricante canônico e bruto, modelo canônico e bruto, quantidade, valor e unidade de potência,
atributos técnicos, origem, violações e avisos. A formatação é sempre a etapa final.

Aliases SOLPLANET/AISWEI são normalizados antes da deduplicação. Um prefixo de fabricante é
removido do modelo somente por tokens inteiros, incluindo repetições consecutivas e aliases;
assim, `JA` nunca remove o prefixo interno de `JAM66D45`. Rótulos isolados de tabela como
MÓDULO, INVERSOR, FABRICANTE e MODELO não integram o modelo, enquanto BIFACIAL, N-TYPE,
MONO, TOPCON, AFCI e dimensões permanecem. Ambiguidade insegura gera
`PENDING_TECHNICAL_REVIEW`.

Potência explicitamente acompanhada de `W` é estruturada como valor e unidade e formatada sem
perder a unidade. Números desacompanhados de evidência semântica não recebem unidade inferida.

O comparador aplica não degradação por dimensões de informação. Perda de fabricante, modelo,
quantidade, tipo, unidade, atributo técnico, equipamento válido ou distinção entre inversor e
microinversor é bloqueante. Fabricante duplicado, rótulo genérico e truncamento também bloqueiam
`UPDATE_EQUIPMENT`. Itens bloqueados ficam pendentes; itens semanticamente iguais ficam
`NO_CHANGE`.

O plano V2 persiste status, erros, avisos, violações e fingerprints semânticos. Um quality gate
integral roda antes da gravação e novamente no carregamento/aplicação. Um único update inválido
torna o plano `INVALID`, antes de abrir a planilha. Planos V1 ou produzidos por
`equipment-v2-technical-v4-rules-1` são incompatíveis e devem ser regenerados do PDF.

## Etapa 2.2 — coleção canônica e comparação integral

`CanonicalEquipment` passa a ser a identidade técnica única. Cada item preserva tipo,
fabricantes e modelos bruto/canônico, quantidade individual, potência/unidade, atributos,
`EquipmentSource` tipada, violações e avisos. `CanonicalEquipmentCollection` separa módulos,
inversores convencionais e microinversores e registra versões e violações da coleção.

O fluxo canônico obrigatório é `parser → cache V5 → validação → auditoria → comparação por
multiset → plano V3 → formatação V2`. Estruturas legadas são somente adaptadores de fronteira e
não são fonte para decisão técnica. O cache V5 deve fazer roundtrip lossless e caches V4 ou
anteriores são incompatíveis.

A comparação pareia deterministicamente cada categoria por identidade técnica completa. Item
perdido, adicionado sem evidência, quantidade individual ausente/alterada, modelo truncado,
token técnico perdido, correspondência ambígua, origem desconhecida relevante ou coleção
incompleta gera violação bloqueante e `PENDING_TECHNICAL_REVIEW`. Totais iguais não compensam
distribuição diferente.

Fabricante/alias só pode ser removido como sequência inteira e consecutiva no início do modelo.
Rótulo genérico só pode ser removido quando `EquipmentSourceType` comprovar contaminação por
cabeçalho, rótulo de campo ou prefixo gerado; catálogo e origem desconhecida são preservados, e
ambiguidade bloqueia atualização.

Quando a contaminação estiver comprovada, a rules-4 remove o rótulo estrutural antes de eliminar
uma repetição do fabricante causada por ele, por exemplo `TSUN MODULO TSUN` ou
`SAJ INVERSOR SAJ`. Sem prova de origem, `GENERIC_LABEL_IN_MODEL`,
`GENERIC_LABEL_ORIGIN_UNPROVEN` e `DUPLICATED_MANUFACTURER_IN_MODEL` são bloqueantes.
Fabricantes internos sem rótulo contaminante não são removidos.

O quality gate integral é executado na auditoria, antes de criar o plano, no carregamento do
plano, antes de abrir o workbook e antes de cada linha. Todo `UPDATE_EQUIPMENT` deve possuir as
coleções atual/proposta completas, sem violações bloqueantes e com cache V5/rules-4.

`plan_version = 3`, `TECHNICAL_PROCESSING_FORMAT_VERSION = 5` e
`equipment_rules_version = equipment-v2-technical-v5-rules-4`. Planos V1/V2, caches V4 e rules-3 são
rejeitados, nunca editados ou promovidos manualmente.

## Regras de aplicação

Somente `UPDATE_EQUIPMENT` aprovado, com plano/hashes/versões atuais e fingerprint da linha ainda
igual. Apenas os valores das células Placa e Inversor podem mudar. Conflitos permanecem intactos.
O fluxo é separado em `prepare_backfill_application`, que não cria backup nem altera o workbook,
e `execute_backfill_application`, que somente pode ser chamado depois do pré-voo e da confirmação.

### Quality gate operacional V7/rules-7

O quality gate operacional executado em `prepare_backfill_application` e antes da aplicação deve
usar a mesma semântica de transição de linha aprovada pelo validador V7. Para cada
`UPDATE_EQUIPMENT`, a validação compara a linha combinada atual (`Placa + Inversor`) contra a linha
combinada proposta e contra a coleção canônica documental aprovada, em vez de comparar
isoladamente a célula `Inversor` atual contaminada com a proposta.

A transição só pode ser aprovada quando `technical_validation_status = approved`,
`semantic_validation_status = approved`, `blocking_violations = []`, o fingerprint atual da linha
corresponde ao plano e a coleção proposta formatada reproduz exatamente os textos propostos de
`Placa` e `Inversor`. Warnings como `CROSS_FIELD_CONTAMINATION_RESOLVED`,
`PROVEN_STRUCTURAL_CONTAMINATION_REMOVED`, `DUPLICATED_MANUFACTURER_IN_MODEL_REMOVED` e
`SOLPLANET_ALIAS_NORMALIZED` são evidências auditáveis, mas não são bypass: perda real de
equipamento, quantidade, potência, unidade, atributo técnico, modelo, fabricante ou microinversor
continua bloqueante.

No plano rules-7 aprovado, o pré-voo operacional deve preparar 30/30 updates, com 0 bloqueios pelo
quality gate, 30/30 fingerprints correspondentes e allowlist restrita a 28 células de `Placa` e 27
células de `Inversor`. O plano e seu `plan_hash` permanecem imutáveis.

## Segurança

2A usa workbook `read_only`, não salva nem cria backup. 2B exige `APP_ENV=production`,
`DRY_RUN=false`, `APPLY_EXCEL=true`, pré-voo da Etapa 0 e plano íntegro. A confirmação é
derivada das regras e da quantidade validadas; para o plano rules-4 autorizado, o texto exato é
`APLICAR RULES-4 120 UPDATES`. Ela somente é solicitada depois de todo o pré-voo. Nenhum Portal
é acessado.

## Privacidade

Plano e relatórios são construídos por allowlist. Não incluem nomes, documentos, endereço,
e-mail, texto integral do PDF, caminhos, Settings, state ou metadados completos. Hashes integrais
ficam no plano técnico; Markdown usa abreviação.

## Backup

Uma cópia completa por aplicação, com nome neutro, timestamp UTC e sem sobrescrita. SHA-256
original e backup devem ser iguais, e o backup deve abrir em modo somente leitura antes da
primeira alteração.

## Rollback

Falha depois da substituição dispara rollback automático pelo backup validado. O arquivo restaurado
é reaberto e seu SHA-256 deve coincidir com o original; os estados distinguem rollback confirmado
de rollback não confirmado. Não existe sucesso parcial.

## Idempotência

Após aplicação verificada, nova auditoria deve produzir zero atualizações para as linhas aplicadas.
Plano e hashes são serializados de modo canônico e determinístico.

Artefatos rules-4 usam nomes versionados e não sobrescrevem silenciosamente rules-1/rules-2/rules-3.

## Relatórios

2A rules-4 gera `historical_equipment_backfill_audit_rules4_<timestamp>.{json,md}` e
`historical_equipment_backfill_plan_rules4_<timestamp>.json`, preservando os artefatos anteriores. Os
três artefatos compartilham `execution_id`, `created_at`, `plan_hash`, `workbook_fingerprint` e versões. 2B gera
`historical_equipment_backfill_apply_rules4_<timestamp>.{json,md}`. O relatório usa allowlist,
não inclui caminhos ou conteúdo integral das células e expõe somente protocolos que falharam.

O `execution_id` é derivado de `created_at` e do fingerprint do workbook. Plano e relatórios JSON
e Markdown expõem exatamente o mesmo identificador, hash canônico, fingerprint e conjunto de
versões, sem incluir caminho absoluto. Divergência em qualquer desses campos impede tratar os
artefatos como uma mesma execução.

## Estados

Auditoria: `AUDITED`, `PENDING`, `BLOCKED`. A aplicação termina exatamente em um estado tipado:
`APPLIED_SUCCESSFULLY`, `ABORTED_PRE_FLIGHT`, `ABORTED_CONFIRMATION`,
`ABORTED_FINGERPRINT_CONFLICT`, `ABORTED_UNEXPECTED_CHANGE`, `FAILED_ROLLED_BACK` ou
`FAILED_ROLLBACK_UNCONFIRMED`.

## Contratos e interfaces

- `python app.py backfill-audit`: somente leitura e geração de relatório/plano;
- `python app.py backfill-apply --plan <arquivo>`: exige confirmação forte e produção;
- plano conforme ADR-0004, contendo versões, fingerprint do workbook, itens e `plan_hash`.

## Dados e persistência

Relatórios e plano usam escrita JSON/texto atômica. A planilha é alterada em memória e salva em
temporário único no mesmo volume. O temporário é reaberto, comparado integralmente contra a
allowlist e somente então substitui o original por `os.replace`. O schema V3 do plano permanece
inalterado por esta adequação operacional.

## Estratégia de testes

Workbooks e PDFs sintéticos em `tmp_path`; mocks para pré-voo e falhas. Cobertura de classificação,
abas/cabeçalhos, duplicidade, PDF, pendência, comparação, plano/hash, privacidade, ambiente,
confirmação, backup, conflito, preservação do workbook, gravação atômica, pós-verificação e
idempotência. Etapas 0 e 1.1 permanecem na suíte direcionada.

## Critérios de aceite

- [ ] Dada a auditoria, quando executada, então os bytes da planilha permanecem idênticos.
- [ ] Dadas abas anuais com contrato válido, quando auditadas, então todas são consideradas.
- [ ] Dado protocolo ausente, duplicado ou PDF inseguro, então nenhuma atualização é proposta.
- [ ] Dado resultado V4 pendente, então a ação é `PENDING_TECHNICAL_REVIEW`.
- [ ] Dada divergência aprovada, então Placa e Inversor compõem uma única proposta.
- [ ] Dado plano serializado, então hash, versões e fingerprints são determinísticos.
- [ ] Dado plano alterado, versão incompatível ou linha concorrente, então a escrita é bloqueada.
- [ ] Dado modelo iniciado por fabricante ou alias repetido, então o prefixo redundante é
  removido por token sem truncar códigos como `JAM`.
- [ ] Dada potência explícita em W, então valor e unidade são preservados separadamente e no
  texto final.
- [ ] Dada proposta semanticamente inferior ou com rótulo genérico, então ela não é
  `UPDATE_EQUIPMENT`.
- [ ] Dado plano V1/rules-1, então validação e aplicação o rejeitam antes de abrir a planilha.
- [ ] Dada aplicação autorizada, então backup íntegro precede qualquer alteração.
- [ ] Dada aplicação, então somente as células explicitamente planejadas podem mudar.
- [ ] Dado workbook complexo sintético, então estilos, fórmulas, filtros e estrutura permanecem.
- [ ] Dada segunda auditoria após aplicação sintética, então não há novas alterações.
- [ ] Dados relatórios públicos, então não contêm dados pessoais nem caminhos.
- [ ] Dada coleção com dois itens, quando a proposta perde um deles, então a linha fica pendente.
- [ ] Dado item pareado, quando sua quantidade ou token de modelo é perdido, então há violação
  bloqueante sem compensação por outro item.
- [ ] Dado prefixo de fabricante interno ou parcial, então ele é preservado.
- [ ] Dado rótulo genérico, então ele só é removido com origem tipada comprobatória.
- [ ] Dado cache V5, então serialização e leitura preservam integralmente a coleção canônica.
- [ ] Dado plano V2/cache V4/rules-2, então ele é rejeitado antes de abrir a planilha.
- [ ] Dado qualquer `UPDATE_EQUIPMENT`, então o quality gate completo resulta zero nos sete
  contadores bloqueantes.

## Rollout em lotes

1. executar 2A completa; 2. revisar pendências e duplicidades; 3. aprovar plano; 4. em nova
autorização, executar 2B em lotes por aba; 5. auditar novamente e exigir zero mudanças.

## Homologação

Nesta entrega, somente 2A pode usar dados reais, em leitura estrita. Registrar apenas contagens,
protocolos e linhas técnicas, sem dados pessoais ou caminhos.

## Riscos

PDF ambíguo, correspondência parcial ambígua, origem ausente, planilha alterada após auditoria,
lock do Excel, arquivo grande e diferenças não
intencionais do openpyxl. Mitigações: pendência conservadora, fingerprints, pré-voo, backup,
allowlist de células e comparação estrutural pós-gravação.

## Decisões pendentes

Nenhuma para implementação da 2A. A autorização e o lote real da 2B dependem de nova solicitação.
