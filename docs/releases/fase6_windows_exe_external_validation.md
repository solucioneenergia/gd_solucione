# Fase 6 - Validacao externa do executavel Windows

Data: 2026-08-04

Classificacao: **BUNDLE CORRIGIDO SINCRONIZADO E VALIDADO NO STAGING; APTO PARA REVISAO DE DIFF**

Nenhum commit, tag, push, release, Portal, planilha oficial, `.env` real, perfil real do Edge,
unidade `Z:`, PDF, log operacional ou dado real foi acessado deliberadamente.

## Identidade

- Branch: `main`
- Commit: `070a4206b017b51f142919e6e98a0b290e0c8953`
- Tag no HEAD: `v2.0.1`
- Remote: ausente
- Worktree: suja antes e depois da validacao

## Staging localizado

Staging final registrado:

`C:\CAMINHO\SINTETICO

Hashes reconferidos no staging:

| Artefato | SHA-256 | Tamanho/contagem |
|---|---|---:|
| `desktop/AutomacaoGDNeoenergia/AutomacaoGDNeoenergia.exe` | `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E` | 10.413.869 bytes |
| `release/gd-neoenergia-2.0.2.zip` | `5887DCE67114CCF224B713AA68B80567AE9091C51B6ACFDCC0F853FFF25FB74B` | 4.521.369 bytes |
| `wheelhouse/automacao_gd_neoenergia-2.0.2-py3-none-any.whl` | `0CA845821533970AAE1639E092E6692E6906D2B1AB47EDBD5457072EC9C2950B` | 1.905.088 bytes |
| `reports/artifact-manifest.json` | `33ED40D09910E3C46117AD2731A535A86C0ED8B944D2F3321C78D228181380D9` | 1.771 bytes |
| `reports/desktop-bundle-manifest.csv` | `BEAB26E244FA474708AEF67015EFCBC955A6A598CCC45684771327CE5453B3A6` | 3.365 arquivos |

Observacao: esse staging ainda contem o EXE anterior a correcao da Fase 6. O bundle corrigido
disponivel em `dist/AutomacaoGDNeoenergia` tem SHA-256
`91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467` e 3.435 arquivos.

## Copia externa limpa

O bundle corrigido foi copiado para:

`C:\Teste_AutomacaoGD_2_0_2_Fase6_Externa_20260804120446\AutomacaoGDNeoenergia`

Hash do EXE copiado:

`91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467`

Manifesto do bundle copiado:

`C:\Teste_AutomacaoGD_2_0_2_Fase6_Externa_20260804120446\bundle-manifest.csv`

SHA-256 do manifesto: `FFB2B2E8735382D29525BEAA1333A926CD88E83C52CBFBD33CD92598DF39CD6C`

Arquivos no bundle copiado: 3.435.

## Smoke offline

Comando executado por harness Python usando `scripts.smoke_desktop_executable.run_smoke`, com
ambiente sintetico reforcado (`USERPROFILE`, `TEMP`, `TMP`, `LOCALAPPDATA`, `APPDATA`,
`PLANILHA_PATH`, `CLIENTES_ROOT`, `DOWNLOADS_DIR`, `LOGS_DIR`, `AUTH_STATE_PATH` e
`BROWSER_PROFILE_DIR` apontando para a raiz sintetica).

Relatorio:

`C:\Teste_AutomacaoGD_2_0_2_Fase6_Externa_20260804120446\smoke-offline-synthetic.json`

Resultado:

| Campo | Valor |
|---|---|
| Exit code do comando | 0 |
| `approved` | `True` |
| `process_started` | `True` |
| `window_created` | `True` |
| `frontend_loaded` | `True` |
| `bridge_initialized` | `True` |
| `empty_state_visible` | `True` |
| `operation_started` | `False` |
| `external_requests` | `0` |
| `controlled_shutdown` | `True` |
| `exit_code` | `0` |

## Abertura direta controlada

O EXE foi aberto por harness controlado, sem usar o smoke, com ambiente 100% sintetico e
fechamento por mensagem de janela.

Relatorio:

`C:\Teste_AutomacaoGD_2_0_2_Fase6_Externa_20260804120446\manual-open-synthetic.json`

Resultado:

| Campo | Valor |
|---|---|
| Exit code do comando | 0 |
| Processo iniciado | `True` |
| Janela criada | `True` |
| Titulo | `Automação GD Neoenergia - Desktop Visual` |
| Fechamento controlado | `True` |
| Exit code do app | `0` |
| Log sintetico | `logs/app.log`, 0 bytes |

## Inventario sintetico criado

Smoke:

- `auth/`
- `browser-profile/`
- `clients/`
- `documents/`
- `downloads/`
- `logs/app.log` com 0 bytes
- `temp/`
- `user-profile/AppData/Local/AutomacaoGDNeoenergia/cache/qtpipelinecache-x86_64-little_endian-llp64/`

Abertura controlada:

- `auth/`
- `browser-profile/`
- `clients/`
- `documents/`
- `downloads/`
- `logs/app.log` com 0 bytes
- `temp/`
- `user-profile/AppData/Local/AutomacaoGDNeoenergia/cache/qtpipelinecache-x86_64-little_endian-llp64/`

## Arquivos fora da raiz sintetica

Foi criada intencionalmente a pasta externa de teste e seus relatorios:

`C:\Teste_AutomacaoGD_2_0_2_Fase6_Externa_20260804120446`

Watchlist comparada antes/depois para locais sensiveis de escrita ja conhecidos:

- `%LOCALAPPDATA%\AutomacaoGDNeoenergia`
- `%APPDATA%\AutomacaoGDNeoenergia`
- `C:\Teste_AutomacaoGD_2_0_2`

Resultado:

- `WATCH_NEW=0`
- `WATCH_REMOVED=0`
- `WATCH_SIZE_CHANGED=0`

Nao foi detectada criacao/alteracao adicional nesses locais durante esta validacao.

## Processos remanescentes

Consulta por processos `AutomacaoGD*` e `QtWebEngine*`: sem resultados apos o fechamento.

## Alertas do sistema

Nao foi observado bloqueio de Windows Defender, SmartScreen, antivirus, DLL ausente, erro de
permissao ou falha de plugin durante smoke ou abertura controlada. Consulta de eventos recentes:

- Windows Defender Operational: apenas eventos informativos.
- SmartScreen Debug: nenhum evento correspondente.
- AppLocker EXE and DLL: nenhum evento correspondente.

## Decisao

O bundle corrigido copiado para a pasta externa passou a Fase 6. Em 2026-08-04, o staging final
registrado ainda permanecia bloqueado enquanto nao fosse atualizado com o EXE corrigido, novo
manifesto e novos hashes. A atualizacao controlada de 2026-08-05 abaixo substitui esse bloqueio
de staging. Nenhuma das duas validacoes autoriza producao, canario, tag, push, release ou
publicacao.

## Atualizacao controlada do staging - 2026-08-05

O bundle completo de `dist/AutomacaoGDNeoenergia` foi copiado para o staging oficial. O bundle
anterior e o ZIP anterior foram preservados em
`C:\CAMINHO\SINTETICO

| Evidencia | Antes | Depois | Resultado |
|---|---:|---:|---|
| Arquivos do bundle | 3.365 | 3.435 | sincronizado |
| EXE (bytes) | 10.413.869 | 12.174.313 | atualizado |
| EXE SHA-256 | `9BB59CCAD98DDC9251072D3318DD4FA06D9FEA30EBDD1FB02E989FA050EAE75E` | `91D9B1D1B9650EB381456EFE48D64629A4ED006FB7A8A0CD516EBC01C7BCB467` | igual a origem e copia externa |
| Manifesto CSV SHA-256 | `BEAB26E244FA474708AEF67015EFCBC955A6A598CCC45684771327CE5453B3A6` | `79B1743035F2D1B19793310DD9C45CF5F7E686E39D95B495009538B2248CC4BB` | 3.435 hashes individuais |

O smoke foi reexecutado diretamente em
`desktop/AutomacaoGDNeoenergia/AutomacaoGDNeoenergia.exe` do staging: codigo 0,
`approved=true`, frontend, bridge, empty state e shutdown aprovados, zero requisicoes externas e
nenhuma operacao iniciada. O release validator e o engineering foundation executado a partir do
ZIP extraido tambem terminaram com codigo 0.

Estado desta etapa: **APTA PARA REVISAO DE DIFF E COMMIT LOCAL**. CI remoto, tag `v2.0.2`,
canario, release e producao nao foram executados e permanecem fora da autorizacao.
