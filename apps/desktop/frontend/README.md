# Frontend desktop visual

Interface visual local carregada por `apps.desktop.window.DesktopVisualWindow`
em um `QWebEngineView`.

## Desenvolvimento

```powershell
cd apps/desktop/frontend
npm install
npm run dev
```

Depois abra:

```powershell
cd "C:\CAMINHO\SINTETICO de projetos\gd_neoenergia"
python desktop_app.py --dev
```

## Build local

```powershell
cd apps/desktop/frontend
npm run build
```

O desktop carrega `apps/desktop/frontend/dist/index.html` quando o build existe.
Sem build, carrega `apps/desktop/frontend/static/index.html`, que contém uma
versão visual estática segura para renderização offline.

`apps/desktop/frontend/index.html` é apenas a entrada do Vite. Ele referencia
`src/main.tsx` e não deve ser carregado diretamente pelo `QWebEngineView` em
modo normal.

## Bridge

`src/bridge/qtBridge.ts` usa `QWebChannel` quando disponível e expõe wrappers:

- `checkEnvironment()`
- `openEdgeCdp()`
- `testCdpConnection()`
- `inspectPortal()`
- `runDryRun()`
- `requestProduction()`
- `openReport()`
- `openLogsFolder()`
- `openWorkbook()`
- `cleanupDryRun()`

Sem QWebChannel, o frontend usa placeholders visuais que não executam produção.

## Segurança

O frontend não acessa Playwright, Excel, Portal GD, `data/state`, `data/cache`,
`data/downloads` ou pastas de clientes diretamente. Toda ação real deve passar
pelas bridges Python validadas.
