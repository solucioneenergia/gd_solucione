# Interface desktop web — PySide6 + React

## Arquitetura

A interface visual é uma camada de apresentação sobre o
`ApplicationController`. Ela não reimplementa o pipeline, o processamento de
PDFs, a escrita no Excel nem o acesso ao Portal GD.

```text
React + TypeScript + Three.js
          |
      QWebChannel
          |
 AutomationBridge (QObject)
          |
 OperationWorker + QThread
          |
 ApplicationController / casos de uso existentes
          |
 Portal CDP, PDF, Excel e arquivos
```

Componentes principais:

- `desktop_app.py`: entrada da aplicação;
- `automacao_gd/presentation/desktop/main_window.py`: janela e
  `QWebEngineView`;
- `web_bridge.py`: API permitida entre JavaScript e Python;
- `worker.py`: execução das operações fora da thread gráfica;
- `web_assets.py`: seleção segura entre Vite local e o build;
- `frontend/`: aplicação React.

O Tkinter permanece disponível apenas como código legado. A opção 6 do CLI
abre a interface PySide6.

## Dependências

```powershell
python -m pip install -r requirements.txt
```

O pacote `PySide6` fornece Qt, Qt WebEngine e QWebChannel. Em uma distribuição
PyInstaller será necessário incluir `frontend/dist` e os binários/plugins do
Qt WebEngine.

## Desenvolvimento do frontend

Em um terminal:

```powershell
cd frontend
npm install
npm run dev
```

Em outro terminal:

```powershell
python desktop_app.py --dev
```

Por padrão, o modo de desenvolvimento aceita somente
`http://localhost:5173`. Uma URL local alternativa pode ser fornecida por
`--dev-url` ou `AUTOMACAO_GD_UI_DEV_URL`. Endereços remotos são recusados.

## Build e execução local

```powershell
cd frontend
npm install
npm run build
cd ..
python desktop_app.py
```

A aplicação carrega `frontend/dist/index.html`. Quando o build não existe,
uma página local orienta como gerá-lo. O diretório `frontend/dist` não é
versionado nem incluído no ZIP limpo.

## Comunicação QWebChannel

O Python registra um único objeto chamado `backend`. O wrapper
`frontend/src/api/qtBridge.ts` converte os nomes Python para uma API JavaScript
em camelCase:

```typescript
window.backend?.runPipelineDryRun();
window.backend?.openEdgeCdp();
window.backend?.statusChanged.connect((status) => console.log(status));
```

Somente um resumo com campos permitidos cruza o canal. Não são enviados:

- texto integral do PDF (`raw_text`);
- CPF/CNPJ;
- e-mail;
- telefone;
- cookies, tokens ou estado de autenticação;
- JSON bruto do pipeline.

O número do protocolo é preservado. Logs e erros passam pela sanitização antes
de chegar ao frontend.

## Execução e cancelamento

As operações são executadas por `OperationWorker` em `QThread`. A UI bloqueia
uma segunda execução enquanto a primeira estiver ativa.

O cancelamento é cooperativo. A solicitação impede uma operação ainda não
iniciada e sinaliza parada, mas uma chamada síncrona já em andamento termina
sua etapa atual antes de liberar os recursos. A thread nunca é encerrada à
força.

## Produção

O botão de produção abre uma confirmação contendo:

- `DRY_RUN=false`;
- `PLANILHA_PATH`;
- `CLIENTES_ROOT`;
- `APPLY_EXCEL`;
- `APPLY_ARCHIVE`;
- `BACKUP_EXCEL`;
- `MAX_PORTAL_PAGES`;
- `MAX_COMPLETED_TO_PROCESS`.

A execução somente começa após digitar `SIM`. As alterações feitas nos
controles da tela são temporárias e não modificam `.env`.

## CDP

O botão **Abrir Edge CDP** inicia um processo externo do Edge com a porta
configurada e o perfil dedicado `data/edge_cdp_profile`. Isso não usa
`playwright.launch`.

Os botões **Testar CDP**, **Inspecionar portal** e os pipelines continuam
usando a factory existente. Com `CDP_MODE=true`, ela seleciona
`CDPPortalGDAutomation`, conecta ao Edge externo autenticado e apenas encerra a
conexão Playwright ao finalizar. O Edge não é fechado pela aplicação.

O Edge CDP abre em pagina neutra (`about:blank`). A navegacao para o Portal GD
deve ser manual: acesse o Portal, faca login e abra **Minhas Solicitacoes**.
Isso evita que a aplicacao navegue automaticamente para o Portal na primeira
abertura do perfil, reduzindo o risco de bloqueio `Access Denied`.

## Cena 3D e acessibilidade

`EnergyFlowScene.tsx` usa Three.js com geometrias simples para painel,
inversor e fluxo de energia. As cores reagem aos estados operacionais.

A opção **Reduzir animações** pausa rotação e pulsação, mantendo indicadores
estáticos. A preferência `prefers-reduced-motion` também reduz transições CSS.

## Limitações da primeira entrega

- o progresso é informado por etapas gerais porque o pipeline existente ainda
  não publica callbacks granulares;
- o cancelamento não interrompe uma operação síncrona no meio de uma gravação;
- testes visuais automatizados do React não fazem parte da suíte Python;
- o executável PyInstaller ainda não é gerado.

## Checklist de homologação

- [ ] `python -m pytest -q` passa;
- [ ] `npm run build` passa;
- [ ] `python app.py` mantém todas as opções do CLI;
- [ ] opção 6 abre a interface visual;
- [ ] o build local é carregado sem acesso a sites remotos;
- [ ] Edge abre com a porta CDP local;
- [ ] login manual e aba “Minhas Solicitações” estão ativos;
- [ ] teste CDP não abre navegador via Playwright;
- [ ] simulação não altera planilha nem pastas;
- [ ] produção exibe todos os campos e exige `SIM`;
- [ ] protocolos aparecem sem `raw_text` ou dados pessoais;
- [ ] janela continua responsiva durante o pipeline;
- [ ] ZIP limpo não contém `frontend/node_modules` nem `frontend/dist`.
