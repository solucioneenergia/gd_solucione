# Backend Python

- Mantenha o domínio independente de apresentação e infraestrutura. Nunca importe essas camadas no domínio.
- Use application/use cases para coordenar ações, infraestrutura para detalhes e apresentação somente para entrada e saída.
- Prefira contratos e injeção de dependência.
- Modele exceções operacionais tipadas; não capture `Exception` indiscriminadamente.
- Gere logs estruturados sem segredos ou dados pessoais.
- Torne operações críticas idempotentes e grave arquivos críticos de forma atômica.
- Cubra domínio e application com testes unitários; teste adaptadores por integração isolada.
- Não replique correções em `src/`; altere o legado somente com justificativa de compatibilidade.
