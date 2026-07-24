# Análise técnica do projeto recebido

## Resumo executivo

O projeto recebido estava funcional e possuía uma suíte consistente: compilação válida e 138 testes aprovados. A lógica principal de download CDP, extração de PDF, atualização de Excel, busca de pastas e retomada por checkpoints já existia.

O risco não estava na ausência de funcionalidade, mas na preparação para produção: acoplamento, arquivos monolíticos, persistência não atômica, pacote contendo dados sensíveis e ausência de uma camada de aplicação reutilizável pelo futuro Tkinter.

## Inventário observado

- aproximadamente 13.764 linhas Python entre aplicação, scripts e testes;
- `cdp_portal_service.py`: cerca de 3.020 linhas;
- `excel_service.py`: cerca de 1.919 linhas;
- `processing_service.py`: cerca de 970 linhas;
- `client_folder_service.py`: cerca de 819 linhas;
- `run_full_cdp_pipeline.py`: cerca de 828 linhas;
- suíte inicial: 138 testes aprovados;
- pacote original: aproximadamente 89 MB, incluindo `.venv` do Windows.

## Pontos fortes

- comportamento relevante coberto por testes unitários e de regressão;
- uso de Pydantic para configuração e modelos;
- modo `DRY_RUN`, backup de planilha e confirmação para alterações reais;
- retomada idempotente por protocolo;
- cache de busca de clientes;
- relatórios JSON e Markdown;
- login manual, sem tentativa de contornar controles do portal.

## Problemas encontrados

### Críticos para distribuição

1. O ZIP incluía `.env`, estado de autenticação do navegador, logs, PDFs, planilhas e ambiente virtual. O estado de autenticação pode conter cookies e tokens válidos.
2. JSONs de checkpoint/cache eram gravados diretamente. Interrupção durante a escrita poderia deixar o pipeline sem estado recuperável.
3. Atualizações normais da planilha usavam `wb.save()` sobre o arquivo oficial, apesar de o reparo já possuir estratégia temporária. Uma interrupção poderia corromper o arquivo principal.
4. `CDP_ENDPOINT` aceitava host remoto sem proteção. Uma porta CDP exposta permite controle do navegador.

### Altos

1. Menu, scripts e regras de aplicação estavam acoplados; o Tkinter teria de importar funções de baixo nível e duplicar confirmações/tratamento de erros.
2. Destinos de arquivamento dependiam de dados externos sem uma verificação explícita de contenção na raiz permitida.
3. O cache era reutilizado apenas por protocolo/nome, sem validar se o caminho pertencia à raiz de clientes atual.
4. `utc_now_iso()` usava horário local, apesar do nome indicar UTC.
5. O salvamento do estado de autenticação informava que senhas não eram salvas, mas não alertava que cookies/tokens de sessão são sensíveis.

### Médios

1. Arquivos de infraestrutura muito grandes misturavam descoberta, navegação, recuperação, relatórios e regras de seleção.
2. Scripts de diagnóstico duplicavam partes do serviço CDP.
3. Não havia pré-voo unificado para planilha, pastas, permissões e endpoint CDP.
4. Não havia CI, configuração de empacotamento ou interface Tkinter.
5. PDFs eram abertos sem limite de tamanho e sem validação prévia de assinatura.

## Correções aplicadas na versão 2

- pacote higienizado, sem dados reais nem sessão;
- arquitetura em camadas e controlador único para CLI/Tkinter;
- compatibilidade `src.*` preservada;
- gravação atômica de JSON, relatórios, cópia de PDF e Excel;
- validação de PDF por extensão, tamanho e assinatura `%PDF-`;
- validação de protocolo e contenção em `CLIENTES_ROOT`;
- cache com raiz, TTL e limite;
- CDP remoto bloqueado por padrão;
- pré-voo para operações reais;
- timestamps UTC timezone-aware;
- interface Tkinter inicial com worker thread;
- CI, scripts de instalação/build e documentação de produção;
- 147 testes aprovados e 5 pulados por dependerem de PDFs reais.

## Riscos que permanecem

- O Portal GD é externo e seu HTML pode mudar. Os seletores devem ser validados em homologação.
- Os cinco testes que usam PDFs reais continuam pulados na distribuição higienizada. Devem rodar em um ambiente privado com fixtures anonimizadas.
- Os adaptadores CDP e Excel permanecem grandes. Eles foram isolados, não totalmente reescritos, para reduzir risco de regressão.
- O empacotamento PyInstaller precisa ser validado no Windows de produção, especialmente Playwright/Edge, caminhos de rede e antivírus.
- Logs ainda podem conter nomes e caminhos operacionais; retenção e acesso devem ser tratados conforme a política interna de dados.
