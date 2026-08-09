# ADR-001 — Caminho canônico da aplicação desktop

- Status: **substituída** por `docs/adr/0005-canonical-desktop-path.md`
- Data: 2026-07-19
- Escopo: apresentação desktop e frontend
- Spec relacionada: `specs/desktop_production_readiness.md`

## Contexto

O projeto mantém duas implementações completas:

1. `desktop_app.py -> apps/desktop -> apps/desktop/frontend`;
2. `automacao_gd/presentation/desktop -> frontend`.

O primeiro caminho é o entrypoint público atual e contém a interface mais recente. O segundo
contém uma bridge mais antiga, uma política WebEngine mais restritiva e um build React próprio.
Contratos, confirmação produtiva, workers, documentação e scripts de build não estão alinhados.
Remover qualquer lado agora criaria risco de regressão.

## Decisão histórica proposta

Adotar como caminho canônico futuro:

```text
desktop_app.py
  -> apps.desktop.main
  -> apps.desktop.window
  -> apps.desktop.bridge
  -> casos de uso/contratos de automacao_gd.application
  -> apps/desktop/frontend/dist/index.html
```

As regras da decisão são:

1. `desktop_app.py` permanece uma fachada mínima e estável.
2. `apps/desktop/frontend/src` é a única fonte React que recebe evolução.
3. `apps/desktop/frontend/dist` é artefato gerado, validado e empacotado; não é editado à mão.
4. `apps/desktop/frontend/static` é somente fallback offline/recuperação. Não deve crescer como
   uma segunda aplicação funcional.
5. A UI chama contratos/casos de uso; não chama Playwright, openpyxl ou serviços concretos.
6. Capacidades úteis e hardening da pilha legada devem ser migrados incrementalmente com testes.
7. `automacao_gd/presentation/desktop` e `frontend` ficam congelados até paridade comprovada.
8. Nenhum código legado é removido por esta ADR; remoção exige ADR complementar e release já
   validado no caminho canônico.

## Motivos

- é o caminho já alcançado por `desktop_app.py` e pelo console script declarado;
- contém a interface visual e os contratos de progresso/erro mais recentes;
- mantém o frontend próximo do adaptador que o hospeda;
- reduz ambiguidade entre documentação, build e execução;
- permite preservar o backend homologado e migrar apenas apresentação/empacotamento.

## Alternativas consideradas

### A. Retornar ao caminho `automacao_gd.presentation.desktop` + `frontend`

Rejeitada como direção principal porque o entrypoint atual e a interface homologada offline já
evoluíram em `apps/desktop`. A pilha antiga, contudo, contém controles WebEngine e ações que
devem ser avaliados durante a migração.

### B. Manter as duas pilhas indefinidamente

Rejeitada. Contratos de produção diferentes (`SIM` versus `SIM, EXECUTAR PRODUÇÃO`), dois
workers e dois builds tornam correções, testes e suporte não determinísticos.

### C. Remover imediatamente a pilha antiga

Rejeitada nesta etapa. Não há Git disponível, o empacotamento canônico está incompleto e ainda
existem dependências compartilhadas, como `_qt`.

## Consequências

### Positivas

- uma origem inequívoca para execução, build, documentação e suporte;
- redução progressiva de duplicação;
- release e CI podem validar o mesmo artefato;
- preservação da fachada e dos casos de uso homologados.

### Negativas e custos

- migração temporária de hardening, bridge e worker;
- necessidade de lockfile e pipeline frontend;
- necessidade de corrigir package discovery/package data/PyInstaller;
- manutenção transitória do legado congelado.

## Plano incremental, sem execução nesta ADR

1. Criar testes RED de package discovery, package data e smoke test do artefato.
2. Corrigir empacotamento do caminho canônico e obter Green.
3. Criar testes RED de navegação/origem e migrar o hardening WebEngine.
4. Congelar formalmente bridge/frontend legados e alinhar documentação.
5. Unificar contrato de execução, progresso, erro e cancelamento.
6. Adicionar lockfile, build e testes frontend ao CI.
7. Validar wheel/executável offline em ambiente limpo.
8. Somente após paridade e rollback comprovados, propor remoção do legado em nova ADR.

## Guardrails de rollback

- cada passo deve ser um commit pequeno e reversível;
- não alterar domínio nem pipeline funcional para acomodar a UI;
- não apontar automaticamente ao frontend legado como fallback oculto;
- se o build canônico falhar, reprovar o artefato e usar apenas o fallback estático explícito;
- se uma capacidade legada não tiver paridade, manter o módulo congelado até o teste existir;
- nenhuma execução produtiva faz parte da migração arquitetural.

## Critério para aceitar esta ADR

A equipe deve concordar que `apps/desktop` é o único destino de evolução e aprovar a sequência
de correção dos P0/P1 da spec. Até essa aprovação, o documento é uma proposta e nenhum código
deve ser removido.
