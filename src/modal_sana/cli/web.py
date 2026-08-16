from __future__ import annotations

import webbrowser
from typing import Annotated

import typer
import uvicorn

from modal_sana import __version__
from modal_sana.cli.common import console, settings
from modal_sana.modal.deploy_mode import inspect_deploy_target


def web(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Start the local workbench on http://127.0.0.1:7862."""
    cfg = settings()
    bind_host = host or cfg.host
    bind_port = port or cfg.port
    url = f"http://{bind_host}:{bind_port}"
    console.print(f"modal-sana web → {url}  ·  v{__version__}")
    try:
        info = inspect_deploy_target()
        mark = "green" if info.get("available") else "yellow"
        console.print(
            f"[{mark}]Modal path:[/{mark}] {info.get('would_use')}  "
            f"app={info.get('app_name')}  deployed={info.get('available')}"
        )
        if info.get("note"):
            console.print(info["note"])
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Modal path probe failed:[/yellow] {type(exc).__name__}: {exc}")
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
