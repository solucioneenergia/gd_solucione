from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from automacao_gd.infrastructure.config import PROJECT_ROOT


DEFAULT_DEV_URL = "http://localhost:5173"
DEV_URL_ENV = "AUTOMACAO_GD_UI_DEV_URL"


@dataclass(frozen=True, slots=True)
class FrontendSource:
    kind: str
    location: str


def resolve_frontend_source(
    *,
    development: bool = False,
    development_url: str | None = None,
) -> FrontendSource:
    if development:
        url = development_url or os.environ.get(DEV_URL_ENV) or DEFAULT_DEV_URL
        _validate_development_url(url)
        return FrontendSource("url", url)

    index_path = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if index_path.is_file():
        return FrontendSource("file", str(index_path.resolve()))
    return FrontendSource("fallback", _fallback_html())


def _validate_development_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("A interface de desenvolvimento deve usar HTTP em localhost.")


def _fallback_html() -> str:
    return """<!doctype html>
<html lang="pt-BR">
<meta charset="utf-8">
<title>Automação GD</title>
<style>
body{font-family:Segoe UI,sans-serif;background:#08111f;color:#e7eef9;padding:48px}
main{max-width:720px;margin:auto;background:#111e30;padding:32px;border-radius:18px}
code{color:#62d9b7} h1{margin-top:0}
</style>
<main>
<h1>Frontend ainda não compilado</h1>
<p>Execute <code>cd frontend</code>, <code>npm install</code> e
<code>npm run build</code>. Depois abra novamente a aplicação.</p>
</main>
</html>"""
