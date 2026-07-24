# Automação GD Neoenergia — versão 2.0

Aplicação Python para consultar solicitações no Portal GD Neoenergia Pernambuco, baixar Orçamentos de Conexão, extrair dados técnicos dos PDFs, atualizar uma planilha Excel e arquivar os documentos nas pastas dos clientes.

A autenticação continua manual. O projeto não tenta contornar CAPTCHA, token, MFA ou qualquer mecanismo de segurança do portal.

## O que mudou

A versão 2 separa domínio, casos de uso, infraestrutura e apresentação. O CLI e a interface desktop PySide6/React usam o mesmo `ApplicationController`, evitando que regras de negócio fiquem dentro de menus ou componentes visuais. A API antiga `src.*` foi mantida como camada de compatibilidade, permitindo regressão controlada.

Principais correções:

- gravação atômica de estado, cache, relatórios e Excel;
- validação de extensão, assinatura e tamanho dos PDFs;
- contenção dos destinos de arquivamento em `CLIENTES_ROOT`;
- CDP remoto bloqueado por padrão;
- cache de clientes validado por raiz, TTL e limite de entradas;
- timestamps do pipeline em UTC com timezone;
- pré-voo para operações reais;
- interface PySide6 com React, QWebChannel e worker em `QThread`;
- pacote de produção sem `.env`, cookies, logs, PDFs, planilhas ou `.venv`.

## Estrutura

```text
automacao_gd/
├── domain/                 # modelos e erros sem dependência de UI
├── application/            # casos de uso, pré-voo e orquestração
├── infrastructure/         # Playwright, CDP, PDF, Excel, filesystem e estado
└── presentation/           # CLI, controlador, desktop web e Tkinter legado
frontend/                   # React, TypeScript, Vite e Three.js
src/                        # compatibilidade com imports da versão 1.x
scripts/                    # utilitários operacionais e diagnósticos
tests/                      # regressão, segurança e aplicação
docs/                       # specs, arquitetura, QA e produção
```

Os adaptadores de Portal/CDP e Excel ainda concentram lógica extensa, mas agora estão isolados na infraestrutura. Isso foi intencional para preservar o comportamento já validado; a divisão interna desses adaptadores deve ocorrer por specs menores e testes de contrato, sem reescrita total de uma vez.

## Requisitos

- Windows 10/11 para o ambiente de produção com Edge;
- Python 3.12 ou 3.13;
- Microsoft Edge para CDP ou navegador suportado pelo Playwright;
- acesso autorizado ao Portal GD;
- planilha `.xlsx` e pasta raiz dos clientes.

## Instalação no Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
```

Ou manualmente:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

Edite o `.env`, especialmente:

```env
APP_ENV=development
PLANILHA_PATH=C:\Caminho\planilha_gd.xlsx
CLIENTES_ROOT=Z:\Clientes
DRY_RUN=true
CDP_ENDPOINT=http://127.0.0.1:9222
ALLOW_REMOTE_CDP=false
```

## Execução

CLI:

```powershell
python app.py
# ou
python -m automacao_gd
```

Desktop visual:

```powershell
cd frontend
npm install
npm run build
cd ..
python desktop_app.py
```

Para desenvolvimento com Vite:

```powershell
python desktop_app.py --dev
```

Testes:

```powershell
python -m pytest -q
```

## Edge via CDP

Feche todas as janelas do Edge e abra uma instância dedicada:

```powershell
Start-Process "msedge.exe" -ArgumentList `
  '--remote-debugging-port=9222', `
  "--user-data-dir=$PWD\data\edge_cdp_profile", `
  '--no-first-run', `
  '--no-default-browser-check'
```

Faça login manual, abra **Minhas Solicitações** e execute o pipeline pelo CLI ou pela interface desktop. Não exponha a porta 9222 na rede. Uma sessão CDP permite controlar o navegador conectado.

## Fluxo recomendado antes de produção

1. Use uma cópia da planilha e `DRY_RUN=true`.
2. Execute o pré-voo.
3. Simule de 3 a 5 protocolos.
4. Revise o JSON/Markdown, os dados de placa/inversor e os destinos de pasta.
5. Teste restauração do backup da planilha.
6. Só então configure `APP_ENV=production` e `DRY_RUN=false`.

Consulte [docs/PRODUCTION_CHECKLIST.md](docs/PRODUCTION_CHECKLIST.md).

## Segurança de dados

Nunca distribua ou versione:

- `.env`;
- `storage_state.json`;
- perfis do navegador;
- PDFs e planilhas reais;
- logs e checkpoints com dados pessoais.

A versão original analisada continha esses itens. A versão 2 foi higienizada. Como um estado de autenticação foi incluído no anexo original, encerre essa sessão no portal e gere uma nova sessão local.

## Documentação técnica

- [Análise do projeto recebido](docs/ANALISE_PROJETO.md)
- [Arquitetura](docs/ARCHITECTURE.md)
- [Especificações e critérios de aceite](docs/SPECIFICATIONS.md)
- [Relatório de QA](docs/QA_REPORT.md)
- [Checklist de produção](docs/PRODUCTION_CHECKLIST.md)
- [Interface desktop web](docs/DESKTOP_WEB_UI.md)
- [Guia de migração](docs/MIGRATION_GUIDE.md)
- [Política de segurança](SECURITY.md)
