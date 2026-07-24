# Modelo operacional de engenharia

Os papéis abaixo representam responsabilidades técnicas. Nem toda tarefa precisa de todos os papéis; o Tech Lead seleciona apenas os necessários. Sempre que possível, o Senior Reviewer deve ser independente da implementação. Agentes não editam simultaneamente os mesmos arquivos.

## Responsabilidades

- **Product Owner / Solicitante:** define a necessidade, valida prioridade, aprova critérios de aceite e autoriza produção.
- **Engineering Orchestrator / Tech Lead:** classifica a tarefa e riscos, seleciona Skills, decompõe o trabalho, controla escopo e consolida evidências.
- **Software Engineer / Python:** responde por domínio, casos de uso, infraestrutura Python, automação, persistência e testes.
- **Fullstack JavaScript:** responde por integrações JS, bridge, build e contratos frontend/backend.
- **Frontend Engineer:** responde por componentes, estado, responsividade, acessibilidade e integração visual.
- **UX Designer:** responde por jornada, hierarquia, estados, mensagens, prevenção/recuperação de erros e consistência visual.
- **DevOps Engineer:** responde por CI, ambientes, dependências, quality gates, release, rollback e observabilidade.
- **QA / TDD Engineer:** define matriz de testes, RED, regressão, critérios de aceite e homologação.
- **Senior Reviewer:** revisa independentemente arquitetura, segurança, regressões, risco operacional e qualidade das evidências.

## Definition of Ready

Uma demanda está pronta quando resultado esperado, escopo, restrições, critérios de aceite, riscos/dados sensíveis e autorizações estão claros; a SPEC existe quando exigida e decisões arquiteturais pendentes foram identificadas.

## Fluxo da demanda e gates

1. Entender e classificar; gate: Ready confirmado.
2. Criar ou atualizar SPEC; gate: critérios aprovados.
3. Registrar ADR quando necessário; gate: decisão aceita.
4. Registrar baseline e executar RED; gate: falha pelo motivo correto.
5. Implementar, obter GREEN e refatorar; gate: testes direcionados.
6. Executar suíte, lint, tipos e validações específicas; gate: quality gates.
7. Revisar independentemente; gate: achados bloqueantes resolvidos.
8. Consolidar evidências; gate: [Definition of Done](DEFINITION_OF_DONE.md).
9. Operar ou publicar somente com autorização do Product Owner.

## Matriz simplificada

| Atividade | Responsável | Aprova/valida |
|---|---|---|
| Necessidade e aceite | Product Owner | Product Owner |
| SPEC e coordenação | Tech Lead | Product Owner/Tech Lead |
| Implementação | Engenheiro da área | QA/Tech Lead |
| Testes e homologação | QA / TDD Engineer | Product Owner |
| CI, release e rollback | DevOps Engineer | Tech Lead/Product Owner |
| Revisão final | Senior Reviewer | Tech Lead |

## Definition of Done

Aplica-se integralmente a [Definition of Done](DEFINITION_OF_DONE.md), com justificativa para todo item `NÃO APLICÁVEL`.
