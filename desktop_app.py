"""Wrapper compatível para a interface desktop visual.

Uso mantido: ``python desktop_app.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from apps.desktop.main import main as desktop_main


def main(argv: Sequence[str] | None = None) -> int:
    return desktop_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
