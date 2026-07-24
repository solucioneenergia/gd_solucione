# Interface desktop PySide6

Status: interface visual preparada/homologada offline.

## Objetivo

A interface desktop é um adaptador visual sobre os casos de uso existentes. Ela
não contém regra de negócio, não acessa Playwright diretamente, não edita Excel
diretamente e não recalcula progresso.

## Estrutura atual

```text
apps/desktop/
  __init__.py
  main.py
  window.py
  bridge/
    automation_bridge.py
    progress_bridge.py
    file_bridge.py
  workers/
    automation_worker.py
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
```

`desktop_app.py` permanece como wrapper de compatibilidade para
`apps.desktop.main`.

## Como executar

```powershell
python desktop_app.py
```

ou:

```powershell
python -m apps.desktop.main
```

Para desenvolvimento do frontend:

```powershell
cd apps/desktop/frontend
npm install
npm run dev
```

Em outro terminal, na raiz do projeto:

```powershell
cd "C:\Users\Solucione\Projetos_Desktop\Automação de projetos\gd_neoenergia"
python desktop_app.py --dev
```

## Dependências

Para abrir a janela real:

```powershell
pip install PySide6
```

O frontend usa React/Vite quando Node/NPM está disponível. Sem build, o
`QWebEngineView` carrega `apps/desktop/frontend/static/index.html`, que contém
uma renderização visual estática segura.

## Correção de carregamento frontend sem Node/NPM

`apps/desktop/frontend/index.html` é exclusivo do Vite e pode manter
`src/main.tsx`. O desktop normal não carrega esse arquivo diretamente.

Sem `dist/index.html`, o fallback offline é
`apps/desktop/frontend/static/index.html`. Esse fallback usa HTML, CSS e
JavaScript simples, sem React, sem TypeScript e sem `.tsx`.

O `vite.config.ts` mantém `base: "./"` para o build React funcionar via
`file://`.

## QWebEngineView

`apps/desktop/window.py` carrega:

1. `http://localhost:5173` com `--dev`;
2. `apps/desktop/frontend/dist/index.html` quando existe;
3. `apps/desktop/frontend/static/index.html` como fallback offline.

## QWebChannel

`AutomationBridge` é registrado como `backend`. O frontend usa
`src/bridge/qtBridge.ts` para chamar métodos seguros e, quando QWebChannel não
está disponível, usa placeholders visuais.

## Produção

A execução de produção exige confirmação textual exata:

```text
SIM, EXECUTAR PRODUÇÃO
```

Sem essa confirmação, a interface não chama a bridge de produção.

## Limitações

- Produção real não foi executada.
- Portal GD não foi acessado.
- Planilha real não foi usada.
- Dados reais de state/cache/downloads não são consumidos nesta etapa.
- Não há empacotamento/PyInstaller.

## Próximo passo

Etapa Visual 2 — conectar dados reais de status, relatórios recentes e execução
assistida pela interface, mantendo produção bloqueada por confirmação.
