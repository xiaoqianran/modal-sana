from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.pretty import Pretty
from rich.table import Table

from modal_sana.cli.common import console
from modal_sana.models.sana.registry import get_model, list_models


def prefetch(
    model: Annotated[str, typer.Argument(help="Model id to download on CPU")] = "sana-sprint-1.6b",
    all_models: Annotated[bool, typer.Option("--all", help="Download every registered SANA model")] = False,
    status: Annotated[bool, typer.Option("--status", help="List what is already on the Volume")] = False,
    deployed: Annotated[bool, typer.Option("--deployed", help="Call a deployed Modal app")] = False,
) -> None:
    """Download SANA weights onto the Modal Volume using CPU only."""
    import modal

    from modal_sana.modal.app import app
    from modal_sana.modal.prefetch import list_volume_models, prefetch_model

    ids = [spec.id for spec in list_models()] if all_models else [model]
    if not all_models and not status:
        get_model(model)

    secrets = []
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        secrets.append(modal.Secret.from_dict({"HF_TOKEN": token, "HUGGING_FACE_HUB_TOKEN": token}))

    use_deployed = deployed or os.environ.get("MODAL_SANA_DEPLOYED") == "1"
    if use_deployed:
        name = os.environ.get("MODAL_SANA_APP_NAME", "modal-sana")
        if status:
            rows = modal.Function.from_name(name, "list_volume_models").remote()
            _print_status(rows)
            return
        for model_id in ids:
            result = modal.Function.from_name(name, "prefetch_model").remote(model_id)
            console.print(Pretty(result))
        return

    with modal.enable_output():
        with app.run():
            if status:
                _print_status(list_volume_models.remote())
                return
            fn = prefetch_model.with_options(secrets=secrets) if secrets else prefetch_model
            for model_id in ids:
                console.print(f"[bold]CPU prefetch[/bold] {model_id}")
                console.print(Pretty(fn.remote(model_id)))


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
