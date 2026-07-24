# Política de segurança

## Dados sensíveis

`data/auth/storage_state.json`, perfis do navegador, logs, PDFs e planilhas podem conter cookies, tokens de sessão, nomes, CPF/CNPJ, endereços e dados técnicos. Esses itens não devem ser versionados, enviados em builds ou anexados a chamados sem anonimização.

A versão original recebida continha `.env`, estado de autenticação, logs, PDFs, planilhas e um ambiente virtual. A versão 2 não inclui esses artefatos. Como o estado de autenticação foi compartilhado em um arquivo compactado, recomenda-se encerrar a sessão correspondente no Portal GD e gerar um novo estado local.

## Controles implementados

- CDP remoto bloqueado por padrão; somente loopback é aceito sem liberação explícita.
- Gravação atômica de JSON, relatórios e planilha Excel.
- Arquivos de sessão/checkpoint recebem permissões privadas quando suportado.
- Validação de assinatura, extensão e tamanho de PDFs.
- Validação de protocolo e contenção de destinos dentro de `CLIENTES_ROOT`.
- Cache de pastas com validação de raiz, TTL e limite de entradas.
- Pré-voo antes de operações reais.

## Relato de vulnerabilidade

Não inclua credenciais nem dados reais no relato. Informe versão, cenário, impacto, passos reproduzíveis com dados sintéticos e logs anonimizados.
