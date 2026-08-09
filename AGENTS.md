# Instruções de engenharia

## Missão do projeto

Este sistema automatiza o Portal GD Neoenergia: lê solicitações, baixa orçamentos de conexão, extrai PDFs, atualiza Excel de forma controlada, arquiva documentos nas pastas dos clientes e oferece execução por CLI e aplicação desktop.

## Fontes de verdade

Decida nesta ordem: (1) solicitação explícita e atual do usuário; (2) SPEC aprovada; (3) ADR aceita; (4) `AGENTS.md` aplicável ao diretório; (5) testes automatizados; (6) comportamento atual do código. Se uma SPEC nova e aprovada contrariar teste antigo, atualize o teste explicitamente e justifique.

## Skills obrigatórias de desenvolvimento

Todo desenvolvimento relacionado a este projeto deve anunciar, ler e aplicar as seguintes
Skills antes de executar alterações:

- `karpathy-guidelines`: explicitar premissas, preferir a solução mínima, fazer mudanças
  cirúrgicas e definir critérios verificáveis de sucesso;
- `prompt-engineering`: estruturar planos, instruções, prompts e delegações com escopo,
  restrições, evidências esperadas e formato de retorno claros;
- `test-driven-development`: para funcionalidade, correção, refatoração ou mudança de
  comportamento, criar e observar um teste RED válido antes do código de produção, seguir com
  GREEN e REFACTOR e então executar a suíte proporcional ao risco;
- `subagent-driven-development`: avaliar obrigatoriamente a decomposição do trabalho e usar
  subagentes para tarefas independentes quando os critérios da Skill forem atendidos, incluindo
  revisão independente. Não forçar paralelismo em trabalho sequencial, exploratório ou com
  estado compartilhado.

O uso dessas Skills complementa, e não substitui, as Skills versionadas em `.agents/skills/`,
as SPECs, ADRs e demais regras deste arquivo. Em tarefas somente de leitura ou documentação sem
efeito comportamental, não simule RED/GREEN nem delegação artificial; aplique os critérios de
evidência, simplicidade e verificação pertinentes.

## Fluxo obrigatório

Para toda alteração funcional, siga:

```text
Entendimento → SPEC → ADR, quando necessário → linha de base → teste RED
→ implementação mínima → GREEN → REFACTOR → suíte completa
→ revisão independente → relatório de evidências
```

Crie ou atualize uma SPEC para nova funcionalidade, mudança de comportamento, correção funcional, integração, contrato, migração, backfill, fluxo UX ou operação de produção. Dispense nova SPEC somente para correção ortográfica isolada, documentação sem efeito comportamental, formatação ou ajuste trivial totalmente coberto por SPEC existente.

Crie ADR para decisão difícil de reverter: framework, arquitetura, remoção de legado, fonte canônica, banco ou persistência, protocolo, dependência estrutural ou segurança relevante.

## Regras de implementação

- Faça a menor alteração suficiente; não inclua refatoração sem relação.
- Use funções pequenas e coesas, nomes claros, tipagem quando aplicável e evite duplicação.
- Trate erros explicitamente e produza logs sem dados sensíveis.
- Inverta dependências: o domínio não depende diretamente de infraestrutura.
- Preserve compatibilidade Windows, idempotência de operações repetíveis e gravação atômica de dados críticos.
- Nunca declare sucesso sem executar testes proporcionais ao risco.

## Segurança

É proibido ler ou expor `.env`, versionar credenciais, registrar tokens/cookies/senhas, incluir perfil de navegador em pacotes, alterar dados reais em testes, usar PDFs reais com dados pessoais como fixtures ou executar produção sem solicitação e autorização explícitas.

## Diretórios

- `automacao_gd/`: backend principal.
- `src/`: compatibilidade/legado; não receba funcionalidade nova sem justificativa.
- `tests/`: testes automatizados.
- `specs/`: especificações comportamentais canônicas.
- `docs/adr/`: ADRs canônicas; siga o status das decisões existentes sobre frontends.
- `docs/templates/`: templates oficiais.
- `.agents/skills/`: Skills versionadas do projeto.
- `data/`: dados operacionais, fora do escopo de testes.
- `dist/`: artefato gerado; não editar.
- `node_modules/`: dependências instaladas; nunca editar.

## Comandos de qualidade

```powershell
python -m pytest -q
python -m ruff check automacao_gd src scripts tests
python -m mypy automacao_gd
python scripts/validate_engineering_foundation.py
```

No frontend, detecte o gerenciador pelo arquivo de lock do respectivo projeto e use apenas scripts declarados no `package.json`; não invente comandos.
