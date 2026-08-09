# SPEC-009 — Prontidao segura e auditavel do terminal

Status: aceita para implementacao
Data: 2026-08-09
Responsavel: Engineering Orchestrator / Codex
Revisores: Operacao GD Neoenergia e revisao independente
ADRs relacionadas: [ADR 0002](../docs/adr/0002-atomic-local-persistence.md), [ADR 0006](../docs/adr/0006-traceable-partial-terminal-operation.md)
Issues relacionadas: Etapa 1 — tudo funcional pelo terminal

## Contexto

O terminal possui um pipeline CDP protegido por limite, lote congelado e lock, mas o
processamento offline real e entradas diretas ainda podem contornar esses controles. A
linha de trabalho local tambem mistura a tag `v2.0.1`, conteudo de `v2.0.2` e drift nao
versionado. A release atual valida estrutura, mas nao prova checkout limpo, SHA ou ausencia
de dados incorporados ao conteudo.

## Problema

Uma execucao real pode selecionar PDFs sem lote autorizado, uma recusa pode retornar falso
sucesso, o protocolo extraido pode divergir do nome do arquivo, efeitos em Excel/arquivo/state
nao possuem um contrato unico de recuperacao e artefatos distribuiveis podem conter dados
operacionais ou caminhos pessoais.

## Evidencias do comportamento atual

- A opcao 4 aceita `SIM` e chama processamento real sem lista explicita, limite ou lock.
- Sem `pdf_paths`, o processamento faz busca recursiva de todos os PDFs elegiveis.
- `allowed_protocols` valida o nome do arquivo antes da extracao, nao o protocolo extraido.
- Recusa nas opcoes 4 e 5 devolve preflight, que pode ser `SUCESSO`.
- O rollback sistemico atual restaura somente o workbook.
- O relatorio consolidado privado contem payload operacional detalhado; nao existe artefato
  compartilhavel agregado com contrato explicito.
- O gerador de ZIP percorre a arvore local e aceita worktree suja ou diretorio sem Git.
- O validador de ZIP verifica nomes/versoes, mas nao o conteudo nem a proveniencia.

## Objetivo

Tornar todas as rotas terminais de leitura externa ou escrita real fechadas por padrao,
limitadas a no maximo cinco protocolos, mutuamente exclusivas, vinculadas a escopo congelado,
retomaveis e auditaveis; e impedir release sem checkout limpo, SHA e scanner de privacidade.

## Escopo

- Menu CLI, opcoes 4 e 5.
- Entradas diretas de processamento offline, download CDP, pipeline completo, reparo real e
  migracao operacional em modo apply.
- Validacao do protocolo extraido contra lote congelado.
- Rastreabilidade dos efeitos em Excel, arquivo, state e relatorio.
- Relatorios `PRIVATE_OPERATIONAL` e `SHAREABLE`.
- Scanner de codigo, fixtures, frontends e artefatos compactados.
- Geracao e validacao de release ancorada em Git limpo.
- Dry-run e ensaio de restauracao/retomada somente com fixtures sinteticas.

## Fora do escopo

- Aplicacao desktop, UX desktop, instalador ou atualizacao automatica.
- Portal, Edge/CDP, workbook, PDFs ou pastas reais sem autorizacao humana posterior.
- Execucao real de piloto/canario nesta entrega.
- Remocao de legado ou consolidacao arquitetural sem ADR propria.
- Autorizacao de lote acima de cinco protocolos em producao.

## Glossario

- **Lote congelado:** conjunto imutavel de entradas autorizado antes da primeira escrita.
- **Escopo divergente:** nome, protocolo extraido, fingerprint ou conjunto diferente do lote.
- **PRIVATE_OPERATIONAL:** evidencia local controlada, nao distribuivel.
- **SHAREABLE:** evidencia agregada criada somente por allowlist estrita.
- **Operacao parcial rastreavel:** efeitos atomicos individualmente, com estado explicito por
  protocolo e retomada idempotente, sem promessa de transacao distribuida.

## Requisitos funcionais

### RF-001 — Autorizacao comum das rotas reais

Qualquer rota capaz de baixar arquivo, alterar workbook, arquivar PDF ou migrar dado deve:

1. exigir limite inteiro entre 1 e 5;
2. preparar e congelar o escopo antes da aplicacao;
3. exibir resumo de escopo antes da confirmacao;
4. exigir frase forte exata, vinculada a operacao e ao limite;
5. adquirir o mesmo mutex global antes de preflight externo ou escrita;
6. revalidar o escopo sob o lock;
7. falhar fechado em concorrencia, ausencia de limite ou divergencia.

`SIM`, texto parcial, texto adicional e frase de outra operacao devem ser rejeitados.

### RF-002 — Opcao 4 offline

A opcao 4 nao pode executar busca recursiva aberta em modo real. O lote deve conter caminhos
explicitos, protocolos esperados unicos, fingerprints e limite autorizado. A frase forte e:

```text
APLICAR OPCAO 4 EM <N> PROTOCOLOS
```

Cancelamento ou confirmacao invalida retorna `BLOQUEADO`, codigo
`OPERATION_CANCELLED` ou `STRONG_CONFIRMATION_MISMATCH` e exit code 2.

### RF-003 — Opcao 5 e entradas diretas

A confirmacao forte da opcao 5 deve ser validada na fronteira do caso de uso, nao apenas no
menu. Entradas diretas nao podem usar `SIM` simples nem chamar servicos internos sem a prova de
autorizacao. Scripts diagnosticos permanecem dry-run; scripts reais reutilizam a fronteira
segura ou encerram bloqueados com codigo nao zero.

Mesmo com `DRY_RUN=true`, a opcao 5 exige confirmacao forte antes de acessar Portal/CDP ou
baixar PDFs. O dry-run integrado totalmente offline nao usa essa rota externa.

### RF-003A — Lock global de execucao real

O mutex compartilhado por opcoes 4, 5 e rotas reais deve usar nome semantico neutro
`data/locks/real_run_execution.lock`. O nome historico `option5_execution.lock` permanece apenas
como alias de compatibilidade de configuracao, sem ser a fonte semantica do contrato.

O arquivo marcador do lock deve existir somente enquanto a execucao detem o lock. Ao sair do
contexto, em sucesso, erro, bloqueio controlado, retorno antecipado ou excecao de relatorio/backup,
o marcador pertencente ao `execution_id` deve ser removido por `finally` ou mecanismo equivalente.

Se um marcador persistente for encontrado antes da aquisicao:

- PID inexistente, invalido ou processo finalizado e tratado como lock orfao, removido com warning
  operacional sanitizado e a nova execucao pode prosseguir;
- PID ativo continua bloqueando a nova execucao com `GLOBAL_EXECUTION_LOCKED`;
- lock ja detido no mesmo processo continua bloqueando reentrada com
  `GLOBAL_EXECUTION_LOCK_REENTRANT`.

O tratamento de orfao nao pode remover marcador de outro `execution_id` ativo nem permitir duas
escritas reais simultaneas.

### RF-004 — Protocolo canonico extraido

Antes de Excel, arquivamento ou state, o sistema compara o protocolo esperado pelo nome, o
protocolo extraido/cache validado e o lote congelado. PDFs selecionados ficam vinculados ao
lote por SHA-256 e sao revalidados imediatamente antes do processamento. Divergencia gera
`FROZEN_BATCH_SCOPE_VIOLATION` e nenhum efeito real daquele item.

### RF-005 — Unidade de consistencia

A unidade e `OPERATION_PARTIAL_TRACEABLE_BY_PROTOCOL`, conforme ADR 0006. Cada protocolo
registra:

```text
excel_effect: not_applied | applied | no_change | rolled_back | failed
archive_effect: not_applied | archived | already_present | failed
state_effect: not_applied | persisted | failed
report_effect: not_applied | persisted | failed
manual_action_required: boolean
rollback_possible: boolean
```

Falha sistemica pode restaurar o workbook a partir do backup. A restauracao so e confirmada
apos validar integridade e identidade do arquivo restaurado. Arquivo ja copiado nao e apagado
automaticamente; fica registrado para retomada/manual. Retomada revalida workbook, destino e
state e nao duplica linha nem arquivo.

### RF-006 — Relatorios

`PRIVATE_OPERATIONAL` pode conter contexto necessario para recuperacao, deve usar persistencia
privada e nunca entra em release. `SHAREABLE` e agregado, sem linhas por protocolo e sem texto
livre. Sua allowlist permite apenas classificacao, modo, versao de schema, timestamps, status,
codigos enumerados, booleanos e contadores.

O relatorio compartilhavel nao contem nomes, protocolos, UCs, enderecos, caminhos absolutos,
nomes de PDF/planilha, tokens, cookies ou dados brutos do Portal em qualquer profundidade.

### RF-007 — Substituicao atomica do workbook

O salvamento continua com temporario validado no mesmo volume e `os.replace`. Se
`os.replace` negar permissao, o original permanece intacto, o temporario e removido e uma
falha operacional tipada `WORKBOOK_ATOMIC_REPLACE_DENIED` e retornada. Nao existe fallback por
copia direta nesta etapa.

### RF-008 — Scanner de privacidade

Um unico scanner reutilizavel deve examinar, sem reproduzir o valor detectado: codigo,
fixtures, `apps/desktop/frontend/index.html`, builds `dist` existentes, frontend legado,
relatorios compartilhiveis, wheel, ZIP e pacote final. Hits de credencial, cookie, token,
documento, identificador operacional, dado pessoal ou caminho pessoal absoluto bloqueiam o
gate. Apenas marcadores sinteticos oficiais podem ser permitidos explicitamente.

### RF-009 — Release reproduzivel

O gerador exige repositorio Git, `git status --porcelain` vazio e arquivos derivados de
`git ls-files`. O ZIP usa nomes POSIX e inclui `release-manifest.json` com HEAD, branch ou
detached state, tags no HEAD, `git describe`, versoes e resultado do scanner. O validador le
nomes internos com `/` ou `\`, rejeita colisoes normalizadas, confere o SHA e executa o mesmo
scanner no conteudo.

`build`, `dist`, `outputs`, logs e caches so entram quando declarados como artefato permitido e
validados. Excecoes sao bloqueadas por padrao.

### RF-010 — Piloto e dry-run

O suporte de piloto permanece limitado a cinco protocolos, lote congelado, lock, backup e
confirmacao forte. Esta SPEC nao autoriza sua execucao. O dry-run integrado usa somente PDF,
workbook, pastas e state sinteticos e prova ausencia de escrita real.

## Requisitos nao funcionais

- Compatibilidade Python 3.12 e 3.13 no CI em `ubuntu-latest` e `windows-latest`.
- Operacoes repetiveis devem ser idempotentes.
- Erros conhecidos nao exibem dados sensiveis nem traceback no console do operador.
- Mudancas devem ser minimas e nao evoluir o desktop.

## Contratos e interfaces

As fronteiras de autorizacao recebem uma estrutura tipada com operacao, limite, escopo
congelado, digest/fingerprint e confirmacao. Servicos internos de escrita nao aceitam modo real
sem escopo explicito. Status terminais continuam `SUCESSO`, `PARCIAL`, `BLOQUEADO` ou `FALHOU`;
cancelamento usa `BLOQUEADO` e exit code 2.

## Dados e persistencia

Fixtures usam identificadores explicitamente declarados como sinteticos no proprio teste,
preferencialmente `2600000000`, `2600000001`, `2600000002`, `CLIENTE SINTETICO LTDA`,
`0000000000` e caminhos sob `tmp_path`. Uma faixa numerica ampla nao constitui, sozinha,
allowlist de privacidade. Nenhum teste le `.env`, `data/`, Portal, CDP ou documentos
operacionais.

## Estados e tratamento de erros

Codigos minimos: `BATCH_LIMIT_NOT_AUTHORIZED`, `STRONG_CONFIRMATION_MISMATCH`,
`OPERATION_CANCELLED`, `GLOBAL_EXECUTION_LOCKED`, `GLOBAL_EXECUTION_LOCK_REENTRANT`,
`FROZEN_BATCH_SCOPE_VIOLATION`, `WORKBOOK_ATOMIC_REPLACE_DENIED`,
`DIRECT_ROUTE_AUTHORIZATION_REQUIRED`, `DIRECT_ROUTE_SCOPE_MISMATCH`,
`PRIVACY_SCAN_BLOCKED`, `DIRTY_WORKTREE`, `RELEASE_PROVENANCE_INVALID`.

## Seguranca e privacidade

Todos os gates falham fechados. Relatorios do scanner informam somente regra, caminho relativo,
posicao e hash irreversivel do match. Dados privados nunca sao promovidos a artefato
compartilhavel.

## UX e acessibilidade, quando aplicavel

O terminal mostra modo, operacao, limite, quantidade congelada, digest abreviado, flags de
escrita, backup e frase requerida antes de solicitar confirmacao. Nao imprime nomes ou caminhos
de clientes no resumo compartilhavel.

## Observabilidade

Cada execucao recebe `execution_id`; lock, lote, efeitos e classificacao dos relatorios sao
registrados. Sucesso so e emitido depois de persistir a evidencia minima. Falha de relatorio
nao reaplica efeitos e deve permitir regeneracao a partir do state privado.

## Compatibilidade

`src/` e frontends legados permanecem preservados. O frontend so pode ser alterado para remover
dado incorporado e manter os gates de seguranca, sem evolucao funcional.

## Migracao ou backfill

Nenhum backfill real sera executado. Entradas de migracao existentes devem continuar dry-run
por padrao e exigir a mesma fronteira de autorizacao em `apply`.

## Estrategia de testes

Para cada requisito: baseline, teste RED observado, implementacao minima, GREEN e suite
relevante. Cobrir confirmacao/limite/lock/escopo, divergencia do protocolo extraido, efeitos
parciais, restauracao/retomada, allowlist recursiva, `os.replace`, scanner contaminado/limpo,
Git sujo/limpo, manifesto e separadores ZIP.

## Criterios de aceite

- [ ] Opcao 4 rejeita `SIM`, frase parcial, ausencia de limite, concorrencia e escopo divergente.
- [ ] Cancelamento das opcoes 4 e 5 retorna bloqueio/exit code 2.
- [ ] A opcao 5 exige confirmacao na fronteira tambem em dry-run com Portal/CDP.
- [ ] Nenhuma rota direta contorna autorizacao, lock ou lote.
- [ ] Protocolo extraido divergente bloqueia antes de qualquer escrita.
- [ ] Falhas apos Excel, arquivo, state e relatorio produzem efeitos rastreaveis e retomada.
- [ ] Relatorio compartilhavel agregado passa por allowlist recursiva.
- [ ] `os.replace` negado preserva original e retorna erro tipado.
- [ ] Scanner bloqueia artefato contaminado e aprova artefato sintetico limpo.
- [ ] Release exige checkout limpo, manifesto/SHA e nomes POSIX.
- [ ] Dry-run e ensaio de restauracao/retomada sinteticos estao verdes.
- [ ] Suite completa, Ruff, MyPy e fundacao estao verdes.
- [ ] Piloto real nao foi executado.

## Rollout

1. Implementar e homologar somente offline/sintetico.
2. Fechar gates locais e CI 3.12/3.13.
3. Gerar release apenas de commit limpo.
4. Preparar runbook de piloto de no maximo cinco protocolos.
5. Exigir nova autorizacao humana para qualquer acesso real.

## Rollback

Reverter a fronteira de autorizacao, scanner e manifestos pelo commit da Etapa 1. Para operacao
ensaiada, restaurar workbook sintetico validado e retomar pelo state; nunca apagar arquivo
arquivado automaticamente.

## Riscos

- Regex de privacidade pode gerar falso positivo; allowlists sao pequenas e explicitas.
- Operacao parcial exige comunicacao clara de acao manual.
- Worktree herdada exige reconciliacao antes de release.

## Decisoes pendentes

- Job Windows remoto e assinatura de artefato dependem de infraestrutura externa.
- Piloto real depende de autorizacao posterior e operador responsavel.

## Evidencias de homologacao

Devem registrar baseline Git, RED/GREEN, comandos, versoes Python, scanners, SHA final, estado
limpo e revisao independente.
