"""Compatibilidade com a API e entrada da versão 1.x."""
import sys as _sys
import automacao_gd.application.full_pipeline as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())
else:
    _sys.modules[__name__] = _impl
