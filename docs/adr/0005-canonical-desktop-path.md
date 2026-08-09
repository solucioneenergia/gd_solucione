# ADR 0005 — Caminho canônico do desktop

Status: aceita
Data: 2026-08-03
Decisores: responsável pelo produto e executor técnico da candidata 2.0.2
SPEC relacionada: `specs/desktop_production_readiness.md`
Substitui: `docs/ADR-001_DESKTOP_CANONICAL_PATH.md`
Substituída por:

## Contexto

O repositório possui duas pilhas desktop e dois frontends. O entrypoint público
`desktop_app.py` já aponta para `apps.desktop`, mas o empacotamento, o build e alguns controles
de segurança ainda não representam esse caminho de forma reproduzível.

## Problema arquitetural

Sem uma fonte canônica aceita, correções de segurança, contratos de bridge, frontend e build
podem evoluir em árvores diferentes e produzir artefatos incompatíveis com o código-fonte.

## Forças de decisão

- preservar a fachada pública e a compatibilidade Windows;
- não remover o legado antes de comprovar paridade;
- manter regras de negócio fora da apresentação;
- permitir empacotamento e validação offline do mesmo artefato entregue;
- reduzir a superfície privilegiada do QWebChannel.

## Decisão

O caminho canônico de evolução é:

```text
desktop_app.py
  -> apps.desktop.main
  -> apps.desktop.window
  -> apps.desktop.bridge
  -> automacao_gd.application
  -> apps/desktop/frontend/dist/index.html
```

`apps/desktop/frontend/src` é a única fonte React que recebe evolução. O `dist` é artefato
gerado e obrigatório em modo de release. O fallback estático é permitido somente como estado
de recuperação explicitamente identificado em desenvolvimento.

`automacao_gd/presentation/desktop` e `frontend` permanecem preservados e congelados. Sua
remoção exige ADR futura, inventário de capacidades, paridade funcional e de segurança,
smoke test do artefato instalado e rollback comprovado.

## Alternativas consideradas

- Retornar ao desktop legado: rejeitado porque não corresponde ao entrypoint público atual.
- Manter duas fontes evolutivas: rejeitado por criar contratos e builds divergentes.
- Remover o legado agora: rejeitado pela ausência de evidência de paridade.

## Consequências positivas

- build, CI, documentação e suporte passam a validar o mesmo caminho;
- controles úteis do legado podem ser portados de forma incremental e testada;
- o frontend instalado deixa de depender da árvore do repositório.

## Consequências negativas

- o legado continuará existindo durante a transição;
- package data, PyInstaller, WebEngine e bridge exigem novos testes;
- o build frontend passa a ser gate obrigatório da candidata.

## Riscos

- bloquear recursos locais legítimos com uma política de origem excessivamente restritiva;
- deixar o canal privilegiado acessível após navegação não autorizada;
- divergir fallback e React durante a transição.

## Segurança

A página deve permitir somente o frontend local empacotado. HTTP loopback é permitido apenas
em modo de desenvolvimento. Navegação externa, novas janelas remotas e acesso arbitrário a
arquivos locais são bloqueados. A bridge só é anexada a uma origem local autorizada.

## Compatibilidade

`desktop_app.py` e o console script permanecem estáveis. O legado não recebe novas funções,
mas também não é removido nesta candidata.

## Plano de implementação

1. tornar `apps` e o frontend canônico instaláveis;
2. exigir o build canônico em modo de release;
3. portar o hardening de origem com testes positivos e negativos;
4. remover dados hardcoded e desabilitar operações sem contrato;
5. validar cancelamento, confirmação, CI e release sintética.

## Plano de rollback

Cada frente deve permanecer em diff localizado e reversível. Em falha, reverter somente a
frente afetada; não apontar silenciosamente ao frontend legado e não remover dados locais.

## Validação da decisão

A decisão é satisfeita quando entrypoint, wheel, frontend, PyInstaller, testes, CI e
documentação apontarem para `apps/desktop`, com o legado preservado e sem novas dependências
de negócio na UI.

## Evidências

- decisão explícita da tarefa de hardening da candidata 2.0.2 em 2026-08-03;
- entrypoint atual em `desktop_app.py`;
- baseline de wheel sem `apps` e importação instalada reprovada;
- SPEC `specs/desktop_production_readiness.md`.
