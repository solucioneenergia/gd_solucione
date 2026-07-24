---
name: tdd-workflow
description: Use para implementar funcionalidades, correções de bugs ou refatorações com preservação de comportamento, começando pela linha de base e por um teste que falhe pelo motivo correto antes de alterar o código de produção.
---

# Executar TDD

## RED

- Executar e registrar a linha de base.
- Reproduzir o defeito ou lacuna com o menor teste comportamental suficiente.
- Confirmar que o teste falha pelo motivo correto antes de alterar produção, salvo scaffolding indispensável.

## GREEN

- Fazer a menor alteração suficiente, sem refatoração paralela.
- Executar o teste direcionado e confirmar a correção.

## REFACTOR

- Melhorar nomes e estrutura e remover duplicação sem mudar comportamento.
- Executar novamente os testes direcionados.

## Quality Gate

Executar testes direcionados, suíte completa, lint, type checking, validações específicas e revisão do diff. Separar falhas preexistentes de regressões.

Para alteração exclusivamente documental, arquivo gerado ou emergência expressamente autorizada, registrar a exceção e aplicar validação proporcional. Nunca escrever teste apenas depois para confirmar a implementação, enfraquecer assertivas, remover teste válido, usar dados reais ou declarar sucesso sem executar testes.
