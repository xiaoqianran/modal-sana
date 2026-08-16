from __future__ import annotations

import typer
from rich.table import Table

from modal_sana.cli.common import console
from modal_sana.core.doctor import run_doctor


def doctor() -> None:
    """Check local Python, Modal SDK, api-proxy-support extra, and authentication."""
    report = run_doctor()
    table = Table(title="modal-sana doctor")
    table.add_column("CHECK")
    table.add_column("OK")
    table.add_column("DETAIL")
    for check in report.checks:
        table.add_row(check.name, "✓" if check.ok else "✗", check.detail)
    console.print(table)
    if report.ready:
        console.print("\n[green]Ready.[/green]")
    else:
        console.print("\n[red]Not ready.[/red] Run [bold]modal setup[/bold] if authentication failed.")
        raise typer.Exit(code=1)
