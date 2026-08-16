from __future__ import annotations

from typing import Annotated

import typer
from rich.pretty import Pretty
from rich.table import Table

from modal_sana.cli.common import console
from modal_sana.modal.secrets import modal_download_secrets
from modal_sana.modal.weights import models_to_prefetch


def prefetch(
    model: Annotated[
        str | None,
        typer.Argument(help="One model id. Omit (or pass --all) to download every SANA model."),
    ] = None,
    all_models: Annotated[bool, typer.Option("--all", help="Download every registered SANA model")] = False,
    status: Annotated[bool, typer.Option("--status", help="List what is already on the Volume")] = False,
    deployed: Annotated[
        bool | None,
        typer.Option(
            "--deployed/--ephemeral",
            help="Call the deployed app or force ephemeral. Default: deployed if it exists.",
        ),
    ] = None,
) -> None:
    """Download SANA weights onto the Modal Volume using CPU only.

    Default (no model name) downloads every registered model in parallel.
    """
    import modal

    from modal_sana.modal.app import app
    from modal_sana.modal.client import ensure_local_app_objects
    from modal_sana.modal.deploy_mode import DeployedAppMissing, resolve_deploy_mode
    from modal_sana.modal.prefetch import list_volume_models, prefetch_model

    ids = models_to_prefetch(model, all_models=all_models)
    secrets = modal_download_secrets()
    ensure_local_app_objects()

    try:
        decision = resolve_deploy_mode(deployed)
    except DeployedAppMissing as exc:
        raise SystemExit(str(exc)) from exc
    console.print(f"Modal: [bold]{decision.mode}[/bold] ({decision.reason})")

    if decision.use_deployed:
        name = decision.app_name
        if status:
            rows = modal.Function.from_name(name, "list_volume_models").remote()
            _print_status(rows)
            return
        _run_ids(modal.Function.from_name(name, "prefetch_model"), ids)
        return

    with modal.enable_output():
        with app.run():
            if status:
                _print_status(list_volume_models.remote())
                return
            fn = prefetch_model.with_options(secrets=secrets) if secrets else prefetch_model
            _run_ids(fn, ids)


def _run_ids(fn, ids: list[str]) -> None:
    if len(ids) == 1:
        console.print(f"[bold]CPU prefetch[/bold] {ids[0]}")
        console.print(Pretty(fn.remote(ids[0])))
        return
    console.print(f"[bold]CPU prefetch[/bold] {len(ids)} models in parallel: {', '.join(ids)}")
    for result in fn.map(ids, order_outputs=True, return_exceptions=True):
        if isinstance(result, Exception):
            console.print(f"[red]{result}[/red]")
        else:
            console.print(Pretty(result))


def _print_status(rows: list[dict]) -> None:
    table = Table(title="Modal Volume /cache/models")
    table.add_column("ID")
    table.add_column("READY")
    table.add_column("BYTES")
    table.add_column("HF")
    for row in rows:
        table.add_row(
            row["model_id"],
            "yes" if row.get("ready") else "no",
            str(row.get("bytes") or 0),
            row.get("hf_id") or "",
        )
    console.print(table)
