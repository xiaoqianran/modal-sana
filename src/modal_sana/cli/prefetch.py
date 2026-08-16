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
        typer.Argument(help="One model id. Omit to download the base 1024px set."),
    ] = None,
    all_models: Annotated[
        bool,
        typer.Option("--all", help="Download every registered model, including 2K/4K."),
    ] = False,
    status: Annotated[bool, typer.Option("--status", help="List what is already on the Volume")] = False,
    deployed: Annotated[
        bool | None,
        typer.Option(
            "--deployed/--ephemeral",
            help="Call the deployed app or force ephemeral. Default: deploy the app if missing.",
        ),
    ] = None,
) -> None:
    """Download SANA weights onto the Modal Volume using CPU only.

    Default (no model name) downloads the base 1024px models.
    Pass --all to include 2K/4K as well.
    """
    import modal

    from modal_sana.modal.app import app
    from modal_sana.modal.client import ensure_local_app_objects
    from modal_sana.modal.deploy_mode import deploy_local_app, ensure_deployed_or_fallback
    from modal_sana.modal.prefetch import list_volume_models, prefetch_model

    ids = models_to_prefetch(model, all_models=all_models)
    secrets = modal_download_secrets()
    ensure_local_app_objects()

    decision = ensure_deployed_or_fallback(deployed, required_models=ids)
    console.print(f"Modal: [bold]{decision.mode}[/bold] ({decision.reason})")

    if decision.use_deployed:
        name = decision.app_name
        if status:
            rows = modal.Function.from_name(name, "list_volume_models").remote()
            _print_status(rows)
            return
        unknown = _run_ids(modal.Function.from_name(name, "prefetch_model"), ids)
        if unknown:
            console.print(
                "[yellow]Deployed prefetch does not know "
                f"{', '.join(unknown)}; redeploying and retrying[/yellow]"
            )
            deploy_local_app(name)
            _run_ids(modal.Function.from_name(name, "prefetch_model"), unknown)
        return

    with modal.enable_output():
        with app.run():
            if status:
                _print_status(list_volume_models.remote())
                return
            fn = prefetch_model.with_options(secrets=secrets) if secrets else prefetch_model
            _run_ids(fn, ids)


def _run_ids(fn, ids: list[str]) -> list[str]:
    """Prefetch each id. Returns ids that failed with an unknown-model error."""
    from modal_sana.modal.deploy_mode import is_unknown_model_error

    unknown: list[str] = []
    if len(ids) == 1:
        console.print(f"[bold]CPU prefetch[/bold] {ids[0]}")
        try:
            console.print(Pretty(fn.remote(ids[0])))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]{exc}[/red]")
            if is_unknown_model_error(exc):
                unknown.append(ids[0])
        return unknown
    console.print(f"[bold]CPU prefetch[/bold] {len(ids)} models in parallel: {', '.join(ids)}")
    for model_id, result in zip(
        ids,
        fn.map(ids, order_outputs=True, return_exceptions=True),
        strict=True,
    ):
        if isinstance(result, Exception):
            console.print(f"[red]{result}[/red]")
            if is_unknown_model_error(result):
                unknown.append(model_id)
        else:
            console.print(Pretty(result))
    return unknown


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
