# SPEC-000 — Fundação de governança de engenharia

Status: aceita
Data: 2026-07-21
Responsável: Engineering Orchestrator / Tech Lead
Revisores: Senior Reviewer
ADRs relacionadas: [ADR 0003](../docs/adr/0003-codex-engineering-governance.md)
Issues relacionadas: solicitação explícita desta etapa

## Contexto

O repositório possui automação funcional, testes, Specs e ADRs, mas não tinha instruções persistentes e verificáveis para o trabalho do Codex.

## Problema

Sem governança versionada, diferentes sessões podem variar na especificação, TDD, separação arquitetural, revisão, segurança e evidências.

## Evidências do comportamento atual

Antes desta SPEC não existiam `AGENTS.md`, as quatro Skills ou um validador estrutural. Já existiam Specs legadas, ADRs 0001/0002, CI e duas estruturas de frontend, preservadas sem migração.

## Objetivo

Implantar instruções persistentes, templates, modelo operacional, Skills, testes e gate de CI para trabalho consistente pelo Codex.

## Escopo

`AGENTS.md` raiz e especializados; templates SPEC/ADR; índices; modelo operacional; Definition of Done; Skills `engineering-orchestrator`, `spec-driven-development`, `tdd-workflow` e `senior-code-review`; validador padrão; testes estruturais; etapa no CI.

## Fora do escopo

Qualquer regra funcional, Portal, PDFs, Excel real, dados operacionais, UI, unificação de frontends, remoção de legado, empacotamento, release e produção.

## Glossário

- **SPEC:** contrato verificável da alteração.
- **ADR:** registro de decisão arquitetural.
- **RED/GREEN/REFACTOR:** ciclo TDD de falha esperada, correção mínima e melhoria segura.
- **Quality gate:** validação obrigatória antes de concluir.

## Requisitos funcionais

RF-01. O Codex deve ler as instruções aplicáveis e seguir Spec Driven Development e TDD em mudanças comportamentais.

RF-02. A estrutura deve oferecer quatro Skills iniciais, com revisão sênior somente por invocação explícita.

RF-03. Um validador deve detectar arquivos/metadados/seções ausentes e links locais quebrados.

RF-04. O CI deve executar o validador antes dos testes.

## Requisitos não funcionais

O validador usa apenas biblioteca padrão, é determinístico, compatível com Windows/Linux e não percorre áreas protegidas.

## Contratos e interfaces

Os contratos são Markdown versionado, frontmatter YAML simples em `SKILL.md`, `agents/openai.yaml` e a saída `Engineering foundation: VALID|INVALID` do validador.

## Dados e persistência

Somente arquivos de governança são criados. `.env`, `data/`, perfis, downloads, logs, estado e caches não são lidos nem alterados.

## Estados e tratamento de erros

O validador agrega erros objetivos e retorna código diferente de zero; em sucesso, retorna zero. Dependências de quality gates ausentes são relatadas separadamente de falhas.

## Segurança e privacidade

Dados reais, credenciais, Portal e planilha operacional são proibidos nos testes. Fixtures devem ser sintéticas e anônimas.

## UX e acessibilidade, quando aplicável

NÃO APLICÁVEL: nenhuma interface visual é modificada.

## Observabilidade

Comandos, códigos de saída e contagens de testes formam as evidências; o validador lista cada contrato violado.

## Compatibilidade

Preservar Windows, CI Linux, documentos legados e ambas as estruturas de frontend.

## Migração ou backfill

NÃO APLICÁVEL: nenhuma migração destrutiva, renomeação ou backfill nesta etapa.

## Estratégia de testes

Registrar baseline; criar teste estrutural RED; implementar o validador e os documentos; obter GREEN; validar Skill inválida, link quebrado, isolamento de `.env`/`data`, políticas de invocação, suíte completa, lint e tipos quando disponíveis.

## Critérios de aceite

- [ ] Dado o repositório, quando o validador for executado, então todos os contratos obrigatórios são aceitos.
- [ ] Dada uma Skill inválida, quando validada, então nome divergente e descrição vazia são reportados.
- [ ] Dado um link local quebrado, quando validado, então a falha é reportada.
- [ ] Dadas `.env` e `data/`, quando o validador roda, então nenhum desses caminhos é lido ou percorrido.
- [ ] Dadas as quatro Skills, quando os metadados são verificados, então somente a revisão sênior desabilita invocação implícita.
- [ ] Dada esta etapa, quando os artefatos são revisados, então nenhum código funcional ou dado operacional foi alterado.

## Rollout

Versionar a fundação e executar o validador no CI antes da suíte. Aplicar as instruções nas próximas demandas.

## Rollback

Reverter somente os arquivos criados nesta etapa e a etapa adicionada ao CI. Não tocar documentos ou código preexistentes.

## Riscos

Validação textual não prova qualidade semântica; Skills iniciais exigem evolução por uso; ferramentas locais podem estar ausentes.

## Decisões pendentes

Definição do frontend canônico e eventual remoção de legado permanecem pendentes conforme ADR proposta existente.

## Evidências de homologação

Preencher no relatório da entrega com RED, GREEN, REFACTOR, validador, testes, lint, tipos e revisão.
