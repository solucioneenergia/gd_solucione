# ADR 0001 — Migração Strangler

**Status:** aceito

## Contexto

Os serviços CDP e Excel são grandes, porém possuem comportamento validado por testes e por uso real. Uma reescrita integral antes da produção aumentaria o risco.

## Decisão

Mover as implementações para adaptadores de infraestrutura, criar camadas novas ao redor e manter fachadas compatíveis. A divisão interna será incremental e orientada por specs/testes de contrato.

## Consequências

- redução imediata do acoplamento da UI;
- regressão menor;
- dívida interna dos adaptadores permanece visível e controlada;
- futura remoção de `src.*` exige versão principal.
