# Auditoria preparatoria de refatoracao segura - 2026-08-10

## Status

Rodada de preparacao concluida parcialmente: artefatos regeneraveis foram movidos para
quarentena local em `lixeira/`, e os candidatos de refatoracao foram classificados sem remover
codigo rastreado.

Esta auditoria nao executou Portal, Edge/CDP, planilha real, PDFs reais, `.env` ou pastas reais
de clientes.

## Baseline

- Branch: `codex/etapa1-terminal-hardening`
- HEAD no inicio da auditoria: `bb6920fbea81905d3bcac27beb4bfa0aac972ae1`
- `git describe`: `v2.0.2-22-gbb6920f`
- Estado inicial: limpo para arquivos rastreados.

## Politica de quarentena

`lixeira/` e uma area local, ignorada pelo Git, usada somente para quarentena reversivel de
artefatos gerados. Ela nao deve entrar em release, wheel, ZIP ou relatorio compartilhavel.

Arquivos rastreados, codigo legado protegido, testes, SPECs, ADRs, `data/`, `.env`, PDFs,
planilhas, perfis de navegador, `node_modules` e o `apps/desktop/frontend/dist` canonico foram
preservados.

## Itens movidos para lixeira

Destino principal:

```text
lixeira/refactor_audit_20260810T112116Z
```

Entradas movidas:

```text
.coverage
.mypy_cache
.mypy_cache_after_validation
.mypy_cache_final
.pytest_cache
.ruff_cache
__pycache__
apps/__pycache__
apps/desktop/__pycache__
apps/desktop/bridge/__pycache__
apps/desktop/resources/__pycache__
apps/desktop/workers/__pycache__
automacao_gd/__pycache__
automacao_gd/application/__pycache__
automacao_gd/application/historical_backfill/__pycache__
automacao_gd/application/use_cases/__pycache__
automacao_gd/domain/__pycache__
automacao_gd/infrastructure/__pycache__
automacao_gd/infrastructure/excel/__pycache__
automacao_gd/infrastructure/files/__pycache__
automacao_gd/infrastructure/locking/__pycache__
automacao_gd/infrastructure/metadata/__pycache__
automacao_gd/infrastructure/pdf/__pycache__
automacao_gd/infrastructure/persistence/__pycache__
automacao_gd/infrastructure/portal/__pycache__
automacao_gd/infrastructure/state/__pycache__
automacao_gd/presentation/__pycache__
automacao_gd/presentation/desktop/__pycache__
automacao_gd_neoenergia.egg-info
automacao_gd_neoenergia.egg-info_after_full_pytest
AutomacaoGDNeoenergia.spec
build
build_after_full_pytest
dist
frontend/dist
scripts/__pycache__
src/__pycache__
tests/__pycache__
```

Resumo da quarentena:

- entradas internas: 4.497 na movimentacao inicial, mais `.mypy_cache_after_validation`
  recriado pelo MyPy durante os gates e `build_after_full_pytest` /
  `automacao_gd_neoenergia.egg-info_after_full_pytest` recriados pela suite completa;
- tamanho aproximado: 827,25 MB;
- classificacao: `REGENERABLE_CACHE` ou `REGENERABLE_BUILD`;
- reversao: mover a entrada correspondente de `lixeira/refactor_audit_20260810T112116Z/<origem>`
  de volta para `<origem>`.

Observacao: duas pastas vazias criadas por tentativas abortadas ficaram dentro de `lixeira/`:
`refactor_audit_20260810T000000Z` e `refactor_audit_20260810T112045Z`. Elas nao contem artefatos.

## Preservado por contrato

- `src/`: fachada de compatibilidade aceita pela ADR 0001; ainda usada por testes e por
  compatibilidade externa.
- `frontend/`: frontend legado congelado, preservado pela ADR 0005.
- `automacao_gd/presentation/desktop`: legado/conector preservado pela ADR 0005.
- `apps/desktop/frontend/static`: fallback estatico ainda coberto por testes.
- `apps/desktop/frontend/src/assets` e `apps/desktop/frontend/static/assets`: duplicados
  intencionais ate haver paridade e ADR de remocao.
- `apps/desktop/frontend/dist`: build canonico do desktop/release; preservado.
- `scripts/connect_existing_edge.py`, `scripts/inspect_portal_table.py` e
  `scripts/process_first_solicitation_cdp.py`: scripts legados/diagnosticos ainda protegidos por
  testes de fail-closed.
- `automacao_gd/application/completion_sync_service.py`: ainda referenciado por use case, CLI,
  testes e SPEC-003.

## Candidatos para refatoracao futura

Prioridade sugerida:

1. `automacao_gd/infrastructure/portal/cdp_service.py` - separar navegacao, paginacao, download,
   detalhe de protocolo e extracao de estado do Portal.
2. `automacao_gd/application/full_pipeline.py` - separar orquestracao OP5, planejamento,
   aplicacao, relatorios e reconciliacao.
3. `automacao_gd/application/processing_service.py` - separar validacao de escopo, extracao PDF,
   planejamento Excel, aplicacao, arquivamento, state e relatorios.
4. `automacao_gd/infrastructure/excel/service.py` - separar leitura, indice, escrita atomica,
   formatacao, reparo e disponibilidade.
5. `automacao_gd/application/project_maintenance_inventory_service.py` - separar inventario,
   grafo de referencias, duplicados e renderizacao.
6. Frontend desktop - consolidar React, fallback estatico e legado raiz somente apos ADR de
   remocao e testes de paridade.

## Regras para a refatoracao completa

- Uma frente por vez, com SPEC antes de comportamento novo.
- Para cada extracao de modulo: teste de caracterizacao antes, refactor minimo, testes verdes.
- Nao remover `src/` ou `frontend/` sem ADR propria, inventario de consumidores e release major.
- Nao mover dados operacionais para fixtures permanentes.
- Nao incluir `lixeira/` em release ou pacote.
- Rodar scanner de privacidade e suite completa antes de qualquer commit de refatoracao.

## Evidencias executadas nesta rodada

- RED do scanner: `lixeira/` na raiz ainda era varrida como fonte e falhou.
- GREEN do scanner: `lixeira/` na raiz passou a ser ignorada pela varredura de fonte.
- Controle de release: arquivo dentro de ZIP em `lixeira/**` continua bloqueado se contaminado.
- Quarentena: 34 entradas de topo/caches foram movidas para `lixeira/refactor_audit_20260810T112116Z`.
