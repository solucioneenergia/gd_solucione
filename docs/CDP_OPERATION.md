# Operação do Portal GD via CDP

Configuração recomendada:

```dotenv
CDP_MODE=true
CDP_ENDPOINT=http://127.0.0.1:9222
ALLOW_REMOTE_CDP=false
USE_PERSISTENT_CONTEXT=false
```

## Abrir o Edge manualmente

Feche instâncias anteriores do Edge e execute:

```powershell
Start-Process "msedge.exe" -ArgumentList `
  '--remote-debugging-port=9222', `
  '--user-data-dir=<V2>\data\edge_cdp_profile', `
  '--no-first-run', `
  '--no-default-browser-check', `
  '--new-window', `
  'https://gdneoenergiapernambuco.neoenergia.com/'
```

Faça login manual no Portal GD e abra a página **Minhas Solicitações**. Depois,
execute `python app.py` e use a opção 2 ou 5.

Abra o Edge CDP diretamente na URL HTTPS canônica do Portal GD. Não use `http://`
e não abra o perfil CDP em `about:blank`, pois o Edge pode restaurar uma sessão
anterior bloqueada em HTTP e exibir `Access Denied`.

Com `CDP_MODE=true`, todos os fluxos usam `create_portal_automation(settings)`,
que seleciona `CDPPortalGDAutomation`. A aplicação conecta com
`chromium.connect_over_cdp`, reutiliza a aba autenticada e, ao terminar, apenas
desconecta o Playwright. Ela não chama `browser.close()` nem `context.close()`.

## Como validar o modo efetivo

Os logs detalhados devem conter:

- `Configuração efetiva: CDP_MODE=true`;
- `Conectando ao Edge existente via CDP`;
- `Aba do Portal GD identificada`;
- `Encerrando conexão CDP sem fechar o Edge aberto manualmente`.

Não devem aparecer `Iniciando navegador`, `Usando contexto persistente` ou
`Perfil do navegador`. Se a aba não for localizada, confira o endpoint, o login
manual e se o Portal GD está aberto no mesmo perfil iniciado com a porta 9222.

Abas que exibem `Access Denied` sao ignoradas pela deteccao CDP. Feche essas
abas e abra uma nova navegacao manual autenticada antes de executar o pipeline.

Endpoints remotos permanecem bloqueados, salvo quando
`ALLOW_REMOTE_CDP=true` for definido conscientemente.
