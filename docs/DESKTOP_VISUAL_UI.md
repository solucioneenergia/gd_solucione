# Interface Desktop Visual

Status: interface visual preparada/homologada offline.

## CorreÃ§Ã£o de responsividade em zoom 100%

Problema identificado:

- a janela desktop impunha tamanho mÃ­nimo de `1280x780`, maior que a altura
  Ãºtil de algumas telas comuns em zoom 100%;
- `.main-content` usava `height: 100vh` com `overflow: hidden`, cortando a Ã¡rea
  inferior quando o conteÃºdo nÃ£o cabia;
- a compactaÃ§Ã£o por altura baixa nÃ£o estava explicitamente definida para
  cenÃ¡rios como `1366x768`;
- a VisualizaÃ§Ã£o do Processo mantinha altura alta mesmo em viewports menores;
- a Ã¡rea RelatÃ³rios podia ficar fora da Ã¡rea visÃ­vel sem uma rolagem vertical
  clara no conteÃºdo principal.

Ajustes aplicados:

- `apps/desktop/window.py` passou a usar tamanho mÃ­nimo de `1180x700` e tamanho
  inicial limitado pela geometria disponÃ­vel da tela;
- `apps/desktop/frontend/src/styles/layout.css` passou a permitir rolagem
  vertical em `.main-content`, mantendo `overflow-x: hidden`;
- foram adicionadas media queries para `max-height: 820px` e `max-width: 1400px`;
- em alturas menores, cards, sidebar, painel de execuÃ§Ã£o, asset do processo,
  tabela e relatÃ³rios reduzem espaÃ§amentos sem trocar o design aprovado;
- a tabela continua sem scroll interno obrigatÃ³rio para os 4 registros mockados;
- os botÃµes de relatÃ³rios continuam acessÃ­veis e sem quebra de texto.

Comportamento esperado:

- em `1366x768`, a interface abre em zoom 100% sem exigir reduÃ§Ã£o do zoom do
  Windows; se a altura Ãºtil nÃ£o comportar todos os cards, a rolagem ocorre no
  conteÃºdo principal;
- em `1440x900`, `1536x864`, `1536x960` e `1920x1080`, o visual premium Ã©
  preservado com mais respiro;
- a sidebar permanece fixa e acessÃ­vel;
- a mensagem e o percentual da VisualizaÃ§Ã£o do Processo continuam fora da
  imagem raster;
- produÃ§Ã£o real permanece bloqueada pela confirmaÃ§Ã£o textual jÃ¡ validada.

Escopo preservado:

- backend, CDP, download, arquivamento, parser PDF, Equipment Format V2, Excel,
  state/checkpoint, cleanup, CLI, bridges funcionais, Portal GD, planilha real e
  produÃ§Ã£o permaneceram fora do escopo.

## Etapa UX 1 - Responsividade operacional e modo compacto

Problema identificado:

- em telas com altura Ãºtil prÃ³xima de `768px`, a interface jÃ¡ permitia rolagem
  vertical, mas ainda exigia rolagem excessiva para chegar aos botÃµes de
  RelatÃ³rios;
- a regra de `max-width: 1400px` empilhava RelatÃ³rios em duas colunas, aumentando
  a altura justamente em telas como `1366x768`;
- o asset central usava `object-fit: cover` no modo compacto e podia cortar os
  labels internos do fluxo.

Ajustes aplicados:

- foi criado um modo ultracompacto para `max-height: 780px`;
- cards superiores, sidebar, painel de execuÃ§Ã£o, chips, aviso, tabela e
  RelatÃ³rios reduzem espaÃ§amento apenas em altura baixa;
- o asset da VisualizaÃ§Ã£o do Processo passa para `145px` no modo ultracompacto;
- no modo ultracompacto, o asset usa `object-fit: contain` para nÃ£o cortar
  labels;
- em largura atÃ© `1400px`, os botÃµes de RelatÃ³rios permanecem em uma linha
  quando hÃ¡ espaÃ§o suficiente;
- o fallback para duas colunas fica restrito a telas realmente estreitas
  (`max-width: 1220px`).

Comportamento em zoom 100%:

- em `1366x768`, os principais blocos ficam visÃ­veis com rolagem mÃ­nima ou sem
  necessidade prÃ¡tica de reduzir zoom;
- se a altura Ãºtil do Windows for menor que a altura nominal da tela, a rolagem
  permanece apenas em `.main-content`;
- sidebar, botÃµes de simulaÃ§Ã£o/produÃ§Ã£o, tabela e botÃµes de RelatÃ³rios
  continuam acessÃ­veis;
- em telas maiores, as regras premium existentes continuam prevalecendo.

ValidaÃ§Ã£o visual:

- captura segura do fallback estÃ¡tico: `docs/images/desktop-ux1-compact-1366x768.png`;
- a captura nÃ£o usa bridge, nÃ£o carrega relatÃ³rios reais e nÃ£o acessa Portal GD.

## Objetivo

Criar uma camada visual desktop baseada na imagem de referência, sem alterar o
backend validado. A interface é carregada por PySide6 com `QWebEngineView` e
usa um frontend local React/Vite.

## Stack

- PySide6
- Qt WebEngine / `QWebEngineView`
- Qt WebChannel / `QWebChannel`
- React
- TypeScript
- Vite
- CSS local com design system
- `lucide-react` para ícones quando o build React estiver disponível

## Estrutura

```text
apps/desktop/
  main.py
  window.py
  bridge/
  workers/
  resources/
  frontend/
    package.json
    vite.config.ts
    index.html
    static/
      index.html
      styles.css
      app.js
    src/
      App.tsx
      main.tsx
      bridge/qtBridge.ts
      components/
      data/mockDashboard.ts
      styles/theme.css
      styles/layout.css
    assets/
```

## Como abrir

```powershell
python desktop_app.py
```

O wrapper `desktop_app.py` chama `apps.desktop.main`, que abre a janela visual.

## Desenvolvimento frontend

```powershell
cd apps/desktop/frontend
npm install
npm run dev
```

Em outro terminal, na raiz do projeto:

```powershell
python desktop_app.py --dev
```

## Build frontend

```powershell
cd apps/desktop/frontend
npm run build
```

Quando `dist/index.html` existe, o desktop carrega esse arquivo. Quando não
existe, carrega `static/index.html` local com a interface visual estática e
segura.

## Correção de carregamento frontend sem Node/NPM

`apps/desktop/frontend/index.html` é a entrada do Vite. Ele pode referenciar
`src/main.tsx`, mas só deve ser usado por `npm run dev` ou `npm run build`.

O desktop em modo normal não carrega esse arquivo diretamente via `file://`.
Sem build em `dist`, o `QWebEngineView` carrega
`apps/desktop/frontend/static/index.html`, que não importa React, TypeScript ou
arquivos `.tsx`.

O `vite.config.ts` mantém `base: "./"` para que os assets do build React sejam
compatíveis com `QUrl.fromLocalFile`.

## QWebEngineView

`apps/desktop/window.py` cria a janela PySide6, registra a bridge Python e
carrega:

1. `http://localhost:5173` em modo `--dev`;
2. `apps/desktop/frontend/dist/index.html` quando existir;
3. `apps/desktop/frontend/static/index.html` como fallback visual offline.

## QWebChannel

O objeto Python `AutomationBridge` é registrado como `backend`. O frontend usa
`src/bridge/qtBridge.ts` para chamar métodos seguros. Sem QWebChannel, as ações
viram placeholders visuais.

## Ações conectadas

- Verificar ambiente: bridge preparada.
- Testar CDP: bridge preparada.
- Rodar simulação: bridge preparada.
- Abrir relatórios/logs/planilha: bridge preparada por `FileBridge`.
- Cleanup dry-run: bridge preparada.

## Ações placeholder nesta etapa

- Abrir Edge CDP: instrução visual segura.
- Inspecionar portal: preparado para conexão posterior.
- Dados dinâmicos do dashboard: mockados nesta etapa.

## Produção

Produção exige confirmação textual exata:

```text
SIM, EXECUTAR PRODUÇÃO
```

Sem esse texto, o frontend não chama a bridge de produção.

## Etapa Visual 1.2 — Polimento visual

A interface foi ajustada para ficar mais próxima da imagem de referência:

- a faixa visível de fallback saiu do dashboard principal;
- a indicação de modo estático ficou discreta no tooltip do card Python da
  sidebar;
- sidebar, cards superiores, painel de execução, visualização do processo,
  tabela e relatórios receberam ajustes de espaçamento, bordas, sombras e cores;
- o fallback estático e o frontend React continuam compartilhando o mesmo padrão
  visual por meio dos estilos locais;
- a tabela de protocolos recentes prioriza a exibição das quatro linhas mockadas
  e mantém rolagem apenas quando a área disponível não for suficiente;
- a produção continua bloqueada por confirmação textual exata.

Diferenças conhecidas em relação à imagem original:

- os ícones do fallback são SVG/CSS/ícones simples locais, não assets 3D
  renderizados;
- a visualização do processo simula o efeito premium com CSS, gradiente, halo e
  linhas, sem depender de imagem externa;
- dados exibidos no dashboard ainda são mockados nesta etapa.

Backend validado, produção real, Portal GD, planilha real, state/cache/downloads
e fluxo CDP não foram alterados nem executados nesta etapa.

## Etapa Visual 1.3 — Ajuste fino visual

Ajustes aplicados para aproximar a tela da referência original:

- a tabela de protocolos recentes foi ajustada para exibir os quatro registros
  mockados sem scroll obrigatório e sem linha cortada;
- os botões de relatórios passaram a usar rótulo sem quebra de linha, com
  truncamento por elipse quando necessário;
- o visual do processo ganhou classes dedicadas `process-flow` e `process-glow`,
  SVGs locais no fallback estático, halo e pedestal visual nos nós;
- a linha principal do dashboard recebeu altura controlada para evitar cortes
  entre painéis, tabela e relatórios;
- sidebar, cards e botões mantêm os tokens visuais compartilhados entre fallback
  estático e React.

Diferenças ainda existentes em relação à imagem original:

- os elementos 3D continuam simulados por SVG/CSS local, sem render 3D real;
- o fallback estático usa SVG inline próprio, enquanto a versão React usa
  `lucide-react` quando houver build;
- os dados de status e protocolos continuam mockados até a Etapa Visual 2.

Nenhuma produção real foi executada. Backend validado, CDP, download,
arquivamento, planilha, state/cache/downloads e cleanup permaneceram fora do
escopo desta etapa.

## Correção visual da sidebar

Status: sidebar refinada contra a imagem de referência.

Ajustes aplicados somente na camada visual:

- os placeholders CSS dos ícones do fallback estático foram substituídos por
  SVGs inline locais, sem CDN externa;
- a versão React passou a encapsular os ícones `lucide-react` na mesma classe
  visual usada pelo fallback estático;
- o item ativo `Dashboard` mantém texto e ícone em laranja, borda suave,
  radius de 10px e barra vertical laranja de 3px;
- itens inativos usam texto e ícones escuros, altura de 56px e alinhamento
  centralizado;
- o card inferior `Python 3.11 / Ambiente pronto` recebeu ícone com degradê,
  tipografia e alinhamento mais próximos da referência;
- fallback estático e React permanecem visualmente equivalentes.

Backend validado, CDP, download, arquivamento, Equipment Format V2, Excel,
state/checkpoint, cleanup, CLI e bridges funcionais não foram alterados.

## Etapa Visual 1.4 — Design System Premium e Ícones Vetoriais

Status: design system visual refinado com assets SVG locais.

SVGs criados em `static/assets/icons` e espelhados em `src/assets/icons`:

- sidebar: `dashboard.svg`, `portal.svg`, `pipeline.svg`, `protocolos.svg`,
  `relatorios.svg`, `configuracoes.svg`;
- cards superiores: `status-system.svg`, `cdp-link.svg`,
  `simulation-mode.svg`, `batch-layers.svg`;
- processo: `process-portal-solar.svg`, `process-download-device.svg`,
  `process-pdf.svg`, `process-spreadsheet.svg`, `process-folder.svg`;
- relatórios: `report-file.svg`, `logs-folder.svg`, `spreadsheet-file.svg`.

Tokens visuais consolidados:

- superfícies: `--app-bg`, `--surface`, `--surface-soft`;
- cores semânticas: `--orange`, `--blue`, `--green`, `--purple`, `--cyan`;
- radius: `--radius-sm`, `--radius-md`, `--radius-lg`, `--radius-xl`;
- sombras: `--shadow-card`, `--shadow-soft`, `--shadow-glow-blue`.

Componentes ajustados:

- sidebar passou a usar SVGs locais em vez de ícones improvisados por CSS;
- cards superiores usam SVGs locais dentro de círculos coloridos;
- visualização do processo usa SVGs premium com gradientes, bases luminosas,
  sombras e halos;
- botões principais usam ícones SVG locais e gradientes suaves;
- botões de relatórios usam SVGs locais e mantêm texto sem quebra.

Diferenças ainda existentes:

- os ícones premium são SVGs semi-3D locais, não renderizações 3D reais;
- o build React depende de Node/NPM disponível no ambiente;
- sem `dist`, o fallback estático continua sendo a versão carregada pelo
  desktop.

Backend validado, produção real, Portal GD, planilha real, state/cache/downloads
e fluxos operacionais não foram alterados nem executados nesta etapa.

## Refinamento visual incremental pós-1.4

Status: refinamento cirúrgico aplicado, sem refazer layout.

Preservado:

- estrutura geral do dashboard;
- ordem das abas da sidebar;
- `Dashboard` ativo em laranja;
- cards superiores, tabela de quatro linhas, botões principais e relatórios;
- proteção de produção por confirmação textual.

Ajustado:

- SVGs do processo ganharam mais volume visual, principalmente o dispositivo de
  download, PDF, planilha e pasta;
- a visualização do processo recebeu escala maior, linha azul mais luminosa,
  bases com glow e fundo com grid/pontos mais perceptíveis;
- cards superiores receberam leve aumento de presença visual;
- regras responsivas foram adicionadas para 1366x768 e telas maiores, com
  respiro extra a partir de 1500px de largura;
- ícones de relatório receberam refinamento de tamanho/alinhamento.

Diferenças restantes:

- o visual ainda é SVG/CSS semi-3D, não render 3D real;
- a referência usa ilustração com profundidade mais realista;
- o build React depende de Node/NPM disponível no ambiente.

Backend validado, CDP, download, arquivamento, Excel, Equipment Format V2,
state/checkpoint, cleanup, CLI, bridges e produção permaneceram fora do escopo.

## Etapa Visual 1.11 — Asset premium da Visualização do Processo

Status: área `Visualização do Processo` substituída por asset premium local.

Escopo aplicado:

- criado `apps/desktop/frontend/static/assets/images/process-flow-premium.svg`;
- criado asset equivalente em `apps/desktop/frontend/src/assets/images/process-flow-premium.svg`;
- o fallback estático passou a usar o asset único local no card de processo;
- `ProcessVisualization.tsx` passou a usar o mesmo asset na versão React/Vite;
- a mensagem inferior `Aguardando início da automação` e o percentual `0%`
  continuam fora da imagem e permanecem dinâmicos;
- labels `Portal`, `Download`, `PDF`, `Planilha` e `Arquivo` continuam
  preservados no asset e como texto acessível.

Preservado:

- sidebar;
- cards superiores;
- painel `Portal e Execução`;
- tabela `Protocolos recentes`;
- área `Relatórios`;
- bridges;
- proteção de produção;
- backend validado.

Diferenças restantes:

- o asset é SVG local semi-3D, não render bitmap/3D real;
- a referência visual tem ilustração com profundidade mais foto-realista;
- o build React depende de Node/NPM disponível no ambiente.

Backend, CDP, download, arquivamento, Excel, state/checkpoint, cleanup, CLI,
Portal GD, planilha real e produção permaneceram fora do escopo.

## Etapa Visual 1.12 — Asset raster premium da Visualização do Processo

Status: arte central do processo substituída por asset raster local com fallback
seguro.

Escopo aplicado:

- criado `apps/desktop/frontend/static/assets/images/process-flow-premium.png`;
- criado `apps/desktop/frontend/static/assets/images/process-flow-premium.webp`;
- criadas cópias equivalentes em `apps/desktop/frontend/src/assets/images/`;
- `static/index.html` passou a priorizar `WebP`, depois `PNG`, mantendo o SVG
  anterior como fallback;
- `ProcessVisualization.tsx` passou a usar a mesma ordem de assets no build
  React/Vite;
- a classe `.process-asset` controla proporção, `object-fit`, radius e encaixe
  no card;
- a faixa inferior de status (`Aguardando início da automação` e percentual)
  continua fora da imagem e permanece dinâmica.

Preservado:

- sidebar;
- cards superiores;
- painel `Portal e Execução`;
- tabela `Protocolos recentes`;
- área `Relatórios`;
- bridges;
- proteção de produção;
- backend validado.

Diferenças restantes:

- o asset é uma renderização raster local gerada e ajustada para o dashboard,
  não uma arte 3D manualmente modelada;
- em telas menores o CSS reduz a altura para manter tabela e relatórios
  visíveis;
- o build React depende de Node/NPM disponível no ambiente.

Backend, CDP, download, arquivamento, Excel, state/checkpoint, cleanup, CLI,
Portal GD, planilha real e produção permaneceram fora do escopo.

## Etapa Visual 2 — Integração segura de dados reais

Status: execução assistida preparada.

Dados e ações conectadas com segurança:

- cards superiores recebem dados da `AutomationBridge`, com leitura de
  `Settings` para modo, lote, paginação e limites;
- o card CDP inicia como `Não testado` e pode ser atualizado por
  `test_cdp_connection`;
- protocolos recentes são fornecidos pela bridge a partir do resultado da
  execução atual ou de relatórios JSON existentes em `data/logs`;
- o frontend não lê `data/logs`, state, cache ou downloads diretamente;
- botões de relatórios chamam `FileBridge`, que valida existência antes de abrir;
- o botão de engrenagem executa apenas cleanup dry-run;
- progresso é recebido por `ProgressEvent` e exibido no status do processo, com
  percentual geral e etapa ativa;
- erros estruturados são renderizados por mensagem operacional sanitizada, sem
  traceback bruto ou JSON bruto;
- produção continua bloqueada por confirmação textual exata:

```text
SIM, EXECUTAR PRODUÇÃO
```

Fallback estático:

- continua funcionando sem Node/NPM e sem Vite;
- consome QWebChannel quando disponível;
- mantém mock visual quando QWebChannel não está disponível;
- não importa `.tsx` nem depende de build.

React/Vite:

- `src/bridge/qtBridge.ts` usa os mesmos métodos e sinais da bridge Python;
- build React continua opcional nesta etapa;
- sem Node/NPM, o fallback estático é o caminho operacional.

Ações ainda placeholder:

- `Abrir Edge CDP` apenas orienta uso do Edge/CDP validado;
- `Inspecionar portal` permanece mensagem segura, sem navegação automática;
- nenhuma produção real foi executada ou homologada.

Limitações:

- dados reais aparecem quando relatórios/resultados seguros existem;
- execução de simulação pela UI chama o caso de uso existente, mas deve ser usada
  de forma assistida;
- Portal GD não é aberto automaticamente nesta etapa;
- produção real permanece pendente de checklist manual.

## Limitações desta etapa

- Não executa produção.
- Não acessa Portal GD.
- Não usa planilha real.
- Não consome dados reais de state/cache/downloads.
- Node/NPM pode não estar instalado no ambiente; nesse caso o build não é
  executado localmente, mas a estrutura React/Vite fica pronta.

## Próximos passos

Etapa Visual 2: conectar dados reais de status, relatórios recentes e execução
assistida pela interface, mantendo produção bloqueada por confirmação.

## Validação final da Etapa Visual 1.12

O asset raster da Visualização do Processo permanece local e usa a seguinte
ordem de compatibilidade:

1. `apps/desktop/frontend/static/assets/images/process-flow-premium.webp`;
2. `apps/desktop/frontend/static/assets/images/process-flow-premium.png`;
3. `apps/desktop/frontend/static/assets/images/process-flow-premium.svg`.

A classe `.process-asset`, definida em
`apps/desktop/frontend/src/styles/layout.css`, controla largura, altura,
`object-fit`, borda e comportamento responsivo. A mensagem operacional e o
percentual permanecem fora do elemento `<picture>`. O percentual continua sendo
um elemento HTML independente, identificado no fallback por
`data-field="overall-progress"`, e continua recebendo as atualizações do fluxo
existente.

Build React em 19/07/2026:

- `node --version`: comando executado, mas Node não está disponível no `PATH`;
- `npm --version`: comando executado, mas NPM não está disponível no `PATH`;
- `npm run build`: não executado por indisponibilidade de Node/NPM;
- `apps/desktop/frontend/dist/index.html`: não foi gerado neste ambiente;
- o fallback estático permanece operacional sem Node, NPM ou Vite.

Validações executadas em 19/07/2026:

- `python -m pytest -q`: `352 passed`;
- `python -m compileall automacao_gd apps`: concluído com sucesso;
- importação de `app.py`: concluída com sucesso;
- importação de `desktop_app.py`: concluída com sucesso;
- abertura da janela desktop: concluída, sem execução de produção.

Captura final:

- `docs/images/desktop-visual-1-12-final.png`.

Status:

- Backend: homologado tecnicamente/offline.
- Interface visual: preparada e integrada.
- Produção real: pendente de validação assistida com 1 protocolo.

O backend, as bridges funcionais, o CDP, o parser PDF, o Equipment Format V2,
o Excel, o state/checkpoint e o cleanup não foram alterados nesta correção.
Portal GD, planilha real e produção não foram acessados ou executados.
