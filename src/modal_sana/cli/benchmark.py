from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from modal_sana.cli.common import console, job_config, service
from modal_sana.modal.gpu import estimate_cost_usd, get_gpu
from modal_sana.schemas.job import PromptSpec


def benchmark(
    gpus: Annotated[str, typer.Option("--gpu", help="Comma-separated GPU ids")] = "L40S,RTX-PRO-6000",
    model: Annotated[str, typer.Option("--model")] = "sana-sprint-1.6b",
    count: Annotated[int, typer.Option("--count", "-n")] = 8,
    batch_size: Annotated[int | None, typer.Option("--batch-size", help="Default: auto per GPU/model")] = None,
    workers: Annotated[int, typer.Option("--workers")] = 1,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    prompt: Annotated[str, typer.Option("--prompt")] = "a cinematic photo of a red bicycle in the rain",
) -> None:
    """Run a small job on each GPU and print images/sec plus estimated $."""
    svc = service()
    table = Table(title="Benchmark")
    table.add_column("GPU")
    table.add_column("IMAGES")
    table.add_column("BATCH")
    table.add_column("VRAM PEAK")
    table.add_column("SEC")
    table.add_column("IMG/S")
    table.add_column("SEC/IMG")
    table.add_column("$/IMAGE")
    table.add_column("$/1000")
    table.add_column("STATUS")

    for raw in gpus.split(","):
        gpu_id = raw.strip()
        if not gpu_id:
            continue
        get_gpu(gpu_id)
        config = job_config(
            model=model,
            gpu=gpu_id,
            count=count,
            steps=None,
            guidance=None,
            width=1024,
            height=1024,
            seed=0,
            batch_size=batch_size,
            workers=workers,
            retry=1,
            image_format="png",
            quality=90,
            negative="",
            dry_run=dry_run,
            deployed=None,
            deduplicate=False,
        )
        job = svc.create_job([PromptSpec(prompt=prompt, count=count, seed=0)], config)
        console.print(f"\n[bold]{gpu_id}[/bold] job {job.id}")
        try:
            final = svc.run_job(job.id)
        except Exception as exc:  # noqa: BLE001
            table.add_row(gpu_id, "0", "-", "-", "-", "-", "-", "-", "-", str(exc))
            continue
        elapsed = 0.0
        if final.started_at and final.completed_at:
            elapsed = (final.completed_at - final.started_at).total_seconds()
        images = max(final.completed_images, 0)
        detail = svc.get_job_detail(final.id)
        rows = detail.get("generations") or []
        effective_batches = [int(row.get("batch_size_effective") or 0) for row in rows]
        effective_batch = max(effective_batches, default=int(final.config.batch_size or 1))
        peaks = [
            float(row.get("vram_peak_reserved_mb") or row.get("vram_peak_mb") or 0.0)
            for row in rows
        ]
        totals = [float(row.get("vram_total_mb") or 0.0) for row in rows]
        peak_mb = max(peaks, default=0.0)
        total_mb = max(totals, default=0.0)
        if peak_mb:
            vram_text = f"{peak_mb / 1024:.1f}G"
            if total_mb:
                vram_text += f"/{total_mb / 1024:.0f}G"
        else:
            vram_text = "-"
        ips = images / elapsed if elapsed else 0.0
        spi = elapsed / images if images else 0.0
        cost = estimate_cost_usd(gpu_id, elapsed)
        per_image = cost / images if images else 0.0
        table.add_row(
            gpu_id,
            str(images),
            str(effective_batch),
            vram_text,
            f"{elapsed:.2f}",
            f"{ips:.2f}",
            f"{spi:.3f}",
            f"{per_image:.4f}",
            f"{per_image * 1000:.2f}",
            final.status,
        )
    console.print()
    console.print(table)
    if dry_run:
        console.print("[yellow]dry-run timings are local placeholders, not GPU truth.[/yellow]")
