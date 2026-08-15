from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from automacao_gd.infrastructure.files.file_service import ensure_directories
from automacao_gd.infrastructure.logging import setup_logger

from apps.desktop.branding import APP_NAME
from apps.desktop.window import QT_AVAILABLE, run_desktop


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Interface desktop visual {APP_NAME}")
    parser.add_argument(
        "--dev",
        action="store_true",
        help="carrega http://localhost:5173 em vez do frontend local",
    )
    args = parser.parse_args(argv)
    ensure_directories()
    setup_logger()
    return run_desktop(dev=args.dev)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
