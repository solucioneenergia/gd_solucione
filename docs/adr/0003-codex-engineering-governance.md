# ADR 0003 — Governança de engenharia do Codex

Status: aceita
Data: 2026-07-21
Decisores: Product Owner / Solicitante e Engineering Orchestrator / Tech Lead
SPEC relacionada: [SPEC-000](../../specs/SPEC-000-engineering-governance-foundation.md)
Substitui: nenhuma
Substituída por: nenhuma

## Contexto

O projeto precisa preservar comportamento validado enquanto diferentes sessões do Codex executam mudanças de forma consistente e auditável.

## Problema arquitetural

Instruções apenas conversacionais não persistem e não oferecem contratos automatizados para SPEC, TDD, revisão e quality gates.

## Forças de decisão

Baixo risco de regressão, contexto conciso, versionamento, compatibilidade, segurança por padrão, rastreabilidade e adoção incremental.

## Decisão

Adotar `AGENTS.md` raiz e por diretório; versionar Skills em `.agents/skills/`; usar `specs/` e `docs/adr/` como locais canônicos; exigir TDD para mudanças comportamentais e revisão independente sempre que possível; validar automaticamente a fundação e executar o validador no CI. Documentos legados permanecem temporariamente em seus caminhos, sem migração destrutiva.

## Alternativas consideradas

- Manter regras apenas no prompt: rejeitada por não ser persistente.
- Migrar e renomear todo documento agora: rejeitada pelo risco e escopo.
- Depender de ferramenta externa: rejeitada; a validação estrutural usa biblioteca padrão.

## Consequências positivas

Fluxo uniforme, contratos verificáveis, melhor evidência, segurança e redução de mudanças fora do escopo.

## Consequências negativas

Mais artefatos de governança e manutenção; validação estrutural não substitui julgamento técnico.

## Riscos

Documentos podem ficar desatualizados ou o processo se tornar excessivo em tarefas triviais; as regras de proporcionalidade mitigam isso.

## Segurança

Validador e testes não leem `.env` ou `data/`; Skills proíbem credenciais, dados pessoais e produção não autorizada.

## Compatibilidade

Nenhuma regra funcional, frontend, legado, contrato produtivo ou dado operacional é modificado.

## Plano de implementação

Criar instruções, templates, modelo operacional, índices, Skills, validador e testes; integrar uma etapa anterior aos testes no CI.

## Plano de rollback

Remover apenas os novos artefatos e a etapa de CI desta fundação, preservando todos os documentos preexistentes.

## Validação da decisão

Testes estruturais, execução direta do validador, validação das Skills e suíte existente.

## Evidências

Resultados RED/GREEN/REFACTOR e quality gates devem constar no relatório final desta etapa.

## Referências

- [SPEC-000](../../specs/SPEC-000-engineering-governance-foundation.md)
- [Modelo operacional](../engineering/OPERATING_MODEL.md)
