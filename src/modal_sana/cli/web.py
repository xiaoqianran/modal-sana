from __future__ import annotations

import webbrowser
from typing import Annotated

import typer
import uvicorn

from modal_sana.cli.common import console, settings


def web(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Start the local workbench on http://127.0.0.1:7860."""
    cfg = settings()
    bind_host = host or cfg.host
    bind_port = port or cfg.port
    url = f"http://{bind_host}:{bind_port}"
    console.print(f"modal-sana web → {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(
        "modal_sana.web.server:app",
        host=bind_host,
        port=bind_port,
        reload=False,
        log_level="info",
    )
