from __future__ import annotations

from typing import Annotated

import typer
from rich.pretty import Pretty
from rich.table import Table

from modal_sana.cli.common import console, service
from modal_sana.core.cost import format_usd
from modal_sana.core.trace import format_span_tree, list_spans
from modal_sana.modal.gpu import list_gpus


def trace(
    job_id: Annotated[str, typer.Argument(help="Job id")],
) -> None:
    """Print the call-chain tree for a job (local + Modal spans)."""
    svc = service()
    try:
        detail = svc.get_trace(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    spans = list_spans(svc.db, job_id)
    lines = format_span_tree(spans)
    console.print(f"[bold]trace[/bold] {job_id}")
    if not lines:
        console.print("no spans recorded")
        return
    for line in lines:
        console.print(line)
    console.print()
    console.print(Pretty({"spans": len(detail["spans"]), "job_id": job_id}, expand_all=False))


def cost(
    job_id: Annotated[str | None, typer.Argument(help="Job id. Omit for GPU rates + last job.")] = None,
) -> None:
    """Show estimated Modal GPU cost. Per-cent tracking from list prices."""
    svc = service()
    if job_id is None:
        _print_rate_table()
        jobs = svc.list_jobs(limit=1)
        if not jobs:
            return
        job_id = jobs[0].id
        console.print(f"\n[bold]latest job[/bold] {job_id}")
    try:
        report = svc.cost_report(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    console.print(f"GPU: {report['gpu']}  ·  {format_usd(report['usd_per_second'])}/s")
    console.print(f"charged GPU seconds: {report['gpu_seconds']:.4f}")
    console.print(f"estimated cost: {report['cost_display']}")
    if report.get("modal_run_url"):
        console.print(f"Modal run: {report['modal_run_url']}")
    if report.get("dry_run"):
        console.print("[yellow]dry-run — GPU seconds and $ are zero by design[/yellow]")
    table = Table(title="Per generation")
    table.add_column("GENERATION")
    table.add_column("STATUS")
    table.add_column("LOAD")
    table.add_column("INFER")
    table.add_column("ENCODE")
    table.add_column("GPU-S")
    table.add_column("$")
    table.add_column("INPUT")
    for item in report["by_generation"]:
        table.add_row(
            item["generation_id"],
            item["status"],
            _ms(item.get("load_ms")),
            _ms(item.get("infer_ms")),
            _ms(item.get("encode_ms")),
            f"{item.get('gpu_seconds') or 0:.4f}",
            format_usd(item.get("cost_usd")),
            item.get("modal_input_id") or "—",
        )
    console.print(table)
    console.print(f"\n[dim]{report['notes']}[/dim]")


def _print_rate_table() -> None:
    table = Table(title="Modal GPU list prices (used for estimates)")
    table.add_column("GPU")
    table.add_column("$/s")
    table.add_column("$/hour")
    table.add_column("¢ / 10s")
    for spec in list_gpus():
        table.add_row(
            spec.id,
            f"{spec.usd_per_second:.6f}",
            f"{spec.usd_per_hour:.2f}",
            f"{spec.usd_per_second * 1000:.3f}",
        )
    console.print(table)


def _ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}ms"
