from __future__ import annotations

from rich.table import Table

from modal_sana.cli.common import console
from modal_sana.modal.gpu import list_gpus
from modal_sana.models.sana.registry import list_models


def models() -> None:
    """List registered SANA models."""
    table = Table(title="Models")
    table.add_column("ID")
    table.add_column("NAME")
    table.add_column("STEPS")
    table.add_column("PIPELINE")
    table.add_column("NOTES")
    for spec in list_models():
        table.add_row(
            spec.id,
            spec.name,
            str(spec.default_steps),
            spec.pipeline,
            spec.description,
        )
    console.print(table)


def gpus() -> None:
    """List Modal GPUs this CLI knows how to request."""
    table = Table(title="GPUs")
    table.add_column("ID")
    table.add_column("BATCH")
    table.add_column("$/HOUR")
    table.add_column("VRAM")
    table.add_column("NOTES")
    for spec in list_gpus():
        table.add_row(
            spec.id,
            str(spec.recommended_batch),
            f"{spec.usd_per_hour:.2f}",
            f"{spec.vram_gb} GB",
            spec.notes,
        )
    console.print(table)
