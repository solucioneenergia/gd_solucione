---
name: spec-driven-development
description: Use para criar, revisar ou atualizar a especificação de uma funcionalidade, correção comportamental, integração, migração, fluxo UX ou alteração operacional antes da implementação. Não use para mudanças puramente ortográficas ou de formatação.
---

# Desenvolver pela especificação

1. Procurar SPECs relacionadas em `specs/` e atualizar a fonte existente em vez de duplicá-la.
2. Usar `docs/templates/SPEC_TEMPLATE.md` e documentar evidências do comportamento atual.
3. Separar explicitamente escopo e fora do escopo.
4. Escrever requisitos funcionais e não funcionais verificáveis, contratos, dados, estados de erro, segurança, compatibilidade e observabilidade.
5. Incluir UX e acessibilidade quando aplicáveis.
6. Mapear cada requisito para testes e escrever critérios de aceite com checkboxes, preferencialmente Dado/Quando/Então.
7. Identificar decisão difícil de reverter e criar ou relacionar ADR quando necessário.
8. Se o pedido for somente especificação, não implementar código. Em ciclo completo, concluir a SPEC antes do código.

## Checklist de qualidade

- [ ] Problema, objetivo e comportamento atual têm evidências.
- [ ] Escopo, exclusões e decisões pendentes não são ambíguos.
- [ ] Requisitos, erros, segurança, UX e compatibilidade são verificáveis.
- [ ] Testes e critérios cobrem sucesso, falha e regressão.
- [ ] Rollout, rollback, riscos e ADRs estão tratados.
