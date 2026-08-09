# Índice de Specs

`specs/` é a fonte canônica de especificações comportamentais. Novas Specs usam `SPEC-NNN-slug-em-kebab-case.md`, numeração sequencial disponível e o [template oficial](../docs/templates/SPEC_TEMPLATE.md). Os nomes legados permanecem inalterados.

Status recomendados: rascunho, proposta, aceita, implementada, rejeitada ou substituída. Quando o documento legado não declarar status, o índice registra "não declarado" sem inferir decisão.

## Novo padrão

- [SPEC-009 — Prontidão segura e auditável do terminal](SPEC-009-terminal-production-readiness.md) — aceita para implementação.
- [SPEC-000 — Fundação de governança de engenharia](SPEC-000-engineering-governance-foundation.md) — aceita.
- [SPEC-002 — Auditoria e correção histórica de equipamentos](SPEC-002-historical-equipment-backfill.md) — aceita para implementação.

## Specs legadas preservadas

- [desktop_production_readiness](desktop_production_readiness.md) — em homologação final controlada da candidata 2.0.2; sem autorização de produção.
- [equipment_brand_classification](equipment_brand_classification.md) — aceita para a Etapa 1.
- [equipment_excel_fill_and_terminal_errors](equipment_excel_fill_and_terminal_errors.md) — aceita para a Etapa 1.
- [equipment_format_v2](equipment_format_v2.md) — aceita para a Etapa 1; fonte canônica de equipamentos.
- [terminal_pipeline_error_stability](terminal_pipeline_error_stability.md) — status não declarado.

Relacione ADRs no cabeçalho da SPEC e registre a SPEC correspondente no cabeçalho da ADR. A relação não altera o status de nenhum documento.
