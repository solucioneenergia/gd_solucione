"""Exceções semânticas da aplicação."""


class AutomationError(Exception):
    """Erro base conhecido pela camada de apresentação."""


class ConfigurationError(AutomationError):
    """Configuração inválida ou insegura."""


class PreflightError(AutomationError):
    """Ambiente não está pronto para uma execução real."""


class OperationalBlockError(AutomationError):
    """Bloqueio esperado que impede uma operação insegura."""

    def __init__(
        self,
        *,
        code: str,
        user_message: str,
        stage: str,
        technical_cause: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = str(code)
        self.user_message = str(user_message)
        self.technical_cause = technical_cause
        self.stage = str(stage)


class PreflightBlockedError(OperationalBlockError, PreflightError):
    """Pré-voo bloqueado antes do início de qualquer operação externa."""
