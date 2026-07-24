# Aplicação desktop

- Mantenha regras de negócio fora da UI; a bridge chama contratos ou casos de uso.
- Execute trabalho pesado fora da thread principal e nunca bloqueie a interface.
- Modele estados de carregamento, vazio, sucesso e erro com mensagens compreensíveis.
- Preserve acessibilidade, foco por teclado e navegação previsível.
- Considere prevenção, recuperação e rastreabilidade de erros na UX.
- Não edite `dist`.
- Não remova fallback ou frontend alternativo sem ADR aceita.
