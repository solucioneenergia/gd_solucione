"""Compatibilidade com a API da versão 1.x."""
import sys as _sys
import automacao_gd.infrastructure.files.file_service as _impl
_sys.modules[__name__] = _impl
