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
  CDP, o fluxo pode acionar apenas um controle local da propria tela de detalhe
  com rotulo/valor `Voltar` ou `Retornar`, incluindo botoes, links ou inputs de
  formulario JSF. Esse controle so e valido quando a URL corrente permanece
  HTTPS no host autenticado do Portal, nao e raiz/Access Denied, e a tabela
  `Minhas Solicitacoes` com linhas e reconfirmada apos o clique. Se essa
  evidencia nao existir, o fluxo deve falhar fechado.
- Se `Voltar`/`Retornar` nao estiver disponivel ou nao reconfirmar a listagem, o
  `op5-plan` pode acionar o controle visual `Home`/`Inicio`/icone de casa ja
  existente na pagina autenticada. Esse clique so e valido se nao montar URL,
  nao abrir nova aba, nao usar historico, nao usar reload, permanecer em HTTPS
  no host do Portal, nao apontar para a raiz do Portal (`/` ou `/index.jsf`),
  nao resultar em raiz/Access Denied e reconfirmar a tabela `Minhas
  Solicitacoes` com linhas diretamente ou por um controle visual autenticado da
  propria Home. Controles `Home`/`Inicio` com `href` absoluto ou relativo que
  resolva para `http://`, raiz do Portal ou host diferente devem ser ignorados
  antes do clique.
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
- O controle local `Voltar`/`Retornar` da tela de detalhe nao e considerado
  navegacao por menu, desde que seja um botao/link/input da propria tela, nao
  abra nova aba, nao use URL salva, nao use historico e seja validado pela
  tabela autenticada apos o clique.
- O controle visual `Home`/`Inicio`/icone de casa tambem nao e considerado
  navegacao por menu quando acionado por elemento ja visivel no DOM autenticado
  e seguido de validacao da tabela. A automacao continua proibida de navegar
  para raiz por URL, `http://`, `goto()`, `reload()`, `go_back()` ou nova aba.
- Se um novo `op5-plan` falhar por CDP/listagem/paginacao, qualquer
  `op5_plan_latest.json` anterior deve ser removido ou sobrescrito por marcador
  invalido com `stale_after_failed_plan=true`; `op5-apply` nao pode aceitar esse
  plano.
- No reset inicial da listagem para a pagina 1, se o contexto Playwright/CDP for
  destruido durante uma navegacao ja disparada pelo proprio Portal, a automacao
  pode aguardar apenas a estabilizacao curta ja existente e revalidar
  passivamente a tabela/paginador na aba atual. O reset so pode ser aceito se a
  pagina 1 e a tabela com linhas forem reconfirmadas; caso contrario deve falhar
  fechado com diagnostico especifico de contexto destruido. Essa recuperacao
  continua proibida de usar `goto()`, `reload()`, `go_back()`, nova aba ou URL
  raiz.
- A mensagem operacional deve orientar o operador a reabrir o Edge pelo comando
  PowerShell aprovado, fazer login manual e deixar a listagem aberta.
- `op5-apply` continua proibido de reler Portal/CDP e aplica somente plano
  congelado validado.
- Quando `op5-plan` encontra PDF local valido e `metadata.json` local valido
  para o protocolo selecionado, e `REPROCESS_EXISTING_PDFS=false`, ele deve
  reutilizar esses arquivos sem abrir a tela de detalhe do Portal.
- Em modo `batch_fast`, quando existe PDF local valido e a propria linha da
  listagem ja contem data de conclusao valida, a falta dessa data no
  `metadata.json` local nao deve forcar abertura do detalhe. A automacao deve
  propagar a data da listagem para o plano com fonte explicita
  `listing_completion_date`.
- Em modo `batch_fast`, quando existe cache privado recente de elegibilidade do
  Portal com candidatos sanitizados e todos os candidatos necessarios ao limite
  solicitado podem ser processados localmente com PDF valido e metadata/conclusao
  suficiente, o `op5-plan` pode iniciar por esse cache sem resetar paginacao nem
  reler Portal. Se o cache nao for suficiente para completar o limite, o fluxo
  deve voltar ao CDP manual autenticado e manter as mesmas regras passivas de
  retorno/listagem.
- Em modo `batch_fast`, se a paginacao do Portal falhar ou travar depois que
  pelo menos um protocolo elegivel ja foi coletado, o plano pode seguir com o
  lote parcial coletado, marcando explicitamente
  `partial_batch_due_to_pagination=true`.
- Se o retorno pos-detalhe falhar depois que o item atual ja tiver PDF local
  valido para processamento, o `op5-plan` deve parar a navegacao CDP, marcar
  `partial_batch_due_to_listing_recovery=true` e preservar o lote parcial
  processavel. Essa regra nao autoriza continuar navegando sem listagem segura.
  Se o item atual nao tiver PDF valido para processamento, a falha continua
  bloqueante com `run_error=failed_return_to_listing`.
- Um lote com `partial_batch_due_to_listing_recovery=true` nao pode ser
  classificado como `SUCESSO` aplicavel pela opcao 5 interativa. O dry-run deve
  reportar `PARCIAL` ou bloquear a aplicacao real, invalidando o plano latest
  para impedir que um lote menor que o solicitado seja aplicado por engano.
- Em `batch_fast`, antes de abrir qualquer detalhe, a selecao deve priorizar
  protocolos com PDF local valido e metadata/conclusao suficiente. Protocolos
  que exigem detalhe so entram depois de esgotados os candidatos processaveis
  localmente. A paginacao incremental nao deve parar apenas por atingir N
  elegiveis brutos se ainda nao houver N candidatos locais seguros.
- Lote parcial por falha de paginacao nao autoriza extrapolar escopo nem reler
  Portal durante `op5-apply`; a aplicacao real continua limitada ao plano
  congelado.

## Teste de aceite

Dado um detalhe aberto na mesma aba, sem tabela de listagem visivel,
quando o retorno pos-detalhe for acionado,
entao a automacao deve tentar apenas o controle local `Voltar`/`Retornar` da
tela de detalhe, inclusive quando ele for um input JSF; se a listagem com linhas
for reconfirmada, o retorno deve ser aceito; caso contrario o resultado deve ser
`failed_return_to_listing`, e nenhuma chamada a `goto()`, `reload()`,
`go_back()` ou menu deve ocorrer.

Dado um detalhe aberto na mesma aba, sem `Voltar`/`Retornar` util,
quando houver botao visual `Home`/`Inicio`/icone de casa autenticado,
entao a automacao pode clicar esse controle e so deve aceitar o retorno se a
tabela `Minhas Solicitacoes` com linhas for reconfirmada sem URL manual,
historico, reload, `http://`, raiz insegura ou nova aba. Se o `href` do
controle resolver para `/`, `/index.jsf`, `http://` ou host diferente, o
controle deve ser recusado antes do clique para preservar a sessao CDP.

Dado um protocolo concluido com PDF e `metadata.json` locais validos,
quando `op5-plan` selecionar esse protocolo,
entao o detalhe do Portal nao deve ser aberto e o PDF local deve entrar no
plano.

Dado um protocolo concluido com PDF local valido, `metadata.json` incompleto e
data de conclusao valida na listagem,
quando `op5-plan` rodar em modo `batch_fast`,
entao o detalhe do Portal nao deve ser aberto apenas para completar metadata; a
data da listagem deve ser usada no plano.

Dado `batch_fast` com cache privado recente de candidatos e PDFs/metadados
locais suficientes para o limite solicitado,
quando `op5-plan` iniciar,
entao a automacao nao deve resetar paginacao nem reler Portal antes de formar o
lote local; se o cache for insuficiente, deve cair para o fluxo CDP normal sem
gerar plano enganoso.

Dado `batch_fast` com paginacao incompleta apos coletar protocolos elegiveis,
quando a automacao nao conseguir avancar a pagina sem risco ao CDP,
entao o plano deve seguir com o lote parcial ja coletado e reportar a causa,
sem bloquear por reconciliacao global.

Dado um detalhe aberto com PDF ja reutilizado e valido para processamento,
quando o retorno seguro para a listagem falhar,
entao a automacao deve parar a navegacao CDP, preservar esse PDF no lote parcial
e nao marcar `run_error`; se nao houver PDF valido, deve manter
`failed_return_to_listing` como erro bloqueante.

Dado um dry-run OP5 em `batch_fast`,
quando a selecao ainda nao tiver N protocolos com PDF local e conclusao
suficientes,
entao a paginacao nao deve parar apenas por existir N protocolos concluidos
brutos; e, se houver candidatos locais em paginas posteriores, eles devem ser
priorizados antes de qualquer abertura de detalhe.

Dado um dry-run que preserve lote parcial por perda de listagem pos-detalhe,
quando a opcao 5 interativa avaliar o plano,
entao o plano nao deve ser aceito para aplicacao real sem nova geracao valida.

Dado um protocolo coletado originalmente em pagina posterior,
quando a listagem reaparecer em pagina incorreta apos detalhe,
entao o `op5-plan` deve recuperar a pagina de origem apenas por paginacao segura
e localizar o protocolo, sem `goto()`, `reload()`, `go_back()` ou menu.

Dado um `op5_plan_latest.json` valido anterior,
quando um novo `op5-plan` falhar por retorno/listagem/paginacao,
entao o plano anterior deve ficar explicitamente inutilizavel antes de qualquer
`op5-apply`.

Dado que o Portal destrua temporariamente o contexto CDP durante o reset inicial
para a pagina 1,
quando a aba atual estabilizar novamente na listagem autenticada,
entao a automacao deve aceitar o reset somente apos reconfirmar pagina 1 e
tabela com linhas; se nao houver essa evidencia, deve falhar fechado sem
navegacao agressiva.
