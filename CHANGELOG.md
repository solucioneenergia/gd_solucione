# Changelog

## 2.0.0 — 2026-07-15

- Nova arquitetura em camadas: domínio, aplicação, infraestrutura e apresentação.
- Fachada de compatibilidade para imports `src.*` e scripts da versão 1.x.
- Controlador compartilhado entre CLI e Tkinter.
- Interface Tkinter inicial com execução em thread para não congelar a janela.
- Pré-voo para planilha, pastas, permissões e CDP.
- Bloqueio de CDP remoto por padrão.
- Persistência atômica de estado, cache, metadados, relatórios e Excel.
- Validação de PDF e de caminhos de arquivamento.
- Cache de clientes limitado por raiz, TTL e quantidade.
- Timestamps de estado corrigidos para UTC com timezone.
- Pipeline de CI, configuração de build e documentação de produção.
- Suíte ampliada de 138 para 152 casos coletados: 147 aprovados e 5 pulados por dependerem de PDFs reais.
