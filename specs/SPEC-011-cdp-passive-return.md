# SPEC-011 — Retorno CDP passivo apos detalhe

## Status

Aceita para correcao operacional da Etapa 1.

## Problema

Durante `op5-plan`, a automacao pode ficar na tela de detalhe apos abrir um
orcamento. Tentativas automaticas de recuperar a listagem com `goto()`,
`reload()`, `go_back()` ou navegacao por menu podem interferir na sessao CDP
manual do operador e recriar `Access Denied` no Portal GD.

## Contrato

- O CDP permanece attach-only.
- O Edge deve ser aberto e autenticado manualmente pelo operador.
- A automacao nao pode abrir Edge, abrir nova aba do Portal, navegar para a raiz
  do Portal nem tentar autenticar automaticamente.
- No retorno pos-detalhe do `op5-plan`, se a tabela `Minhas Solicitacoes` nao
  estiver visivel na aba corrente ou em outra aba ja existente do mesmo contexto
  CDP, o fluxo deve falhar fechado.
- A revalidacao da pagina/linha de origem de um protocolo selecionado no
  `op5-plan` tambem deve operar em modo passivo: quando a listagem ja estiver
  visivel, pode usar apenas controles de paginacao existentes; quando a listagem
  nao estiver visivel, deve falhar fechado sem menu, historico, reload, URL
  salva ou nova aba.
- Nesse retorno pos-detalhe, a automacao nao pode chamar:
  - `goto()`;
  - `reload()`;
  - `go_back()`;
  - navegacao por menu.
- Se um novo `op5-plan` falhar por CDP/listagem/paginacao, qualquer
  `op5_plan_latest.json` anterior deve ser removido ou sobrescrito por marcador
  invalido com `stale_after_failed_plan=true`; `op5-apply` nao pode aceitar esse
  plano.
- A mensagem operacional deve orientar o operador a reabrir o Edge pelo comando
  PowerShell aprovado, fazer login manual e deixar a listagem aberta.
- `op5-apply` continua proibido de reler Portal/CDP e aplica somente plano
  congelado validado.
- Quando `op5-plan` encontra PDF local valido e `metadata.json` local valido
  para o protocolo selecionado, e `REPROCESS_EXISTING_PDFS=false`, ele deve
  reutilizar esses arquivos sem abrir a tela de detalhe do Portal.
- Em modo `batch_fast`, se a paginacao do Portal falhar ou travar depois que
  pelo menos um protocolo elegivel ja foi coletado, o plano pode seguir com o
  lote parcial coletado, marcando explicitamente
  `partial_batch_due_to_pagination=true`.
- Lote parcial por falha de paginacao nao autoriza extrapolar escopo nem reler
  Portal durante `op5-apply`; a aplicacao real continua limitada ao plano
  congelado.

## Teste de aceite

Dado um detalhe aberto na mesma aba, sem tabela de listagem visivel,
quando o retorno pos-detalhe for acionado,
entao o resultado deve ser `failed_return_to_listing`,
e nenhuma chamada a `goto()`, `reload()`, `go_back()` ou menu deve ocorrer.

Dado um protocolo concluido com PDF e `metadata.json` locais validos,
quando `op5-plan` selecionar esse protocolo,
entao o detalhe do Portal nao deve ser aberto e o PDF local deve entrar no
plano.

Dado `batch_fast` com paginacao incompleta apos coletar protocolos elegiveis,
quando a automacao nao conseguir avancar a pagina sem risco ao CDP,
entao o plano deve seguir com o lote parcial ja coletado e reportar a causa,
sem bloquear por reconciliacao global.

Dado um protocolo coletado originalmente em pagina posterior,
quando a listagem reaparecer em pagina incorreta apos detalhe,
entao o `op5-plan` deve recuperar a pagina de origem apenas por paginacao segura
e localizar o protocolo, sem `goto()`, `reload()`, `go_back()` ou menu.

Dado um `op5_plan_latest.json` valido anterior,
quando um novo `op5-plan` falhar por retorno/listagem/paginacao,
entao o plano anterior deve ficar explicitamente inutilizavel antes de qualquer
`op5-apply`.
