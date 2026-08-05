"""Wrapper compatível para a interface desktop visual.

Uso mantido: ``python desktop_app.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

def main(argv: Sequence[str] | None = None) -> int:
    from apps.desktop.main import main as desktop_main

    return desktop_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
