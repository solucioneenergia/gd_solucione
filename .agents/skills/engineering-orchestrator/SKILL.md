---
name: engineering-orchestrator
description: Use para conduzir funcionalidades, correções ou mudanças multidisciplinares que exijam decomposição, seleção de especialistas, avaliação de riscos, coordenação de SPEC, TDD, implementação, revisão e quality gates. Não use para uma correção textual trivial.
---

# Orquestrar engenharia

1. Ler o `AGENTS.md` raiz e todos os `AGENTS.md` aplicáveis aos arquivos envolvidos.
2. Identificar a solicitação, o resultado esperado e as restrições.
3. Localizar SPECs e ADRs relacionadas; classificar o trabalho como análise, especificação, implementação, revisão, operação ou produção.
4. Avaliar risco funcional, arquitetural, de segurança, dados, UX, compatibilidade e produção.
5. Identificar áreas impactadas e selecionar somente as Skills e responsabilidades necessárias.
6. Definir a ordem das atividades e a propriedade dos arquivos. Impedir edição simultânea dos mesmos arquivos.
7. Exigir linha de base e acompanhar cada critério de aceite até a evidência correspondente.
8. Para ciclo completo, concluir SPEC e ADR necessária antes de conduzir TDD, implementação mínima e quality gates.
9. Consolidar comandos, resultados, arquivos, riscos e pendências em relatório verificável.

Evitar planejamento infinito. Quando a solicitação pedir implementação e houver informação suficiente, prosseguir. Tratar análise/revisão como leitura, especificação como documentação, implementação como mudança testada e operação/produção como ações separadas. Nunca interpretar esta Skill como autorização para produção.
