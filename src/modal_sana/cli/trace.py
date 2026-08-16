from __future__ import annotations

from typing import Annotated

import typer
from rich.pretty import Pretty
from rich.table import Table

from modal_sana.cli.common import console, service
from modal_sana.core.cost import format_rate, format_usd
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
    job_id: Annotated[str | None, typer.Argument(help="Job id. Omit for rates + every Modal charge today.")] = None,
) -> None:
    """Every Modal GPU charge: seconds × published $/s, call chain, matching job."""
    svc = service()
    if job_id is None:
        _print_rate_table()
        _print_workspace_ledger()
        jobs = svc.list_jobs(limit=1)
        if not jobs:
            return
        job_id = jobs[0].id
        console.print(f"\n[bold]latest job[/bold] {job_id}")
    try:
        report = svc.cost_report(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    console.print(f"GPU: {report['gpu']}  ·  {report.get('rate_display') or format_rate(report.get('usd_per_second'))}")
    console.print(f"charged GPU seconds: {report['gpu_seconds']:.4f}")
    console.print(f"estimated cost: {report['cost_display']}")
    if report.get("formula"):
        console.print(report["formula"])
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
    table.add_column("VRAM")
    table.add_column("GPU-S")
    table.add_column("$/s")
    table.add_column("$")
    table.add_column("INPUT")
    for item in report["by_generation"]:
        table.add_row(
            item["generation_id"],
            item["status"],
            _ms(item.get("load_ms")),
            _ms(item.get("infer_ms")),
            _ms(item.get("encode_ms")),
            _vram(item),
            f"{item.get('gpu_seconds') or 0:.4f}",
            f"{(item.get('usd_per_second') or 0):.6f}" if item.get("usd_per_second") is not None else "—",
            format_usd(item.get("cost_usd")),
            item.get("modal_input_id") or "—",
        )
    console.print(table)
    for item in report["by_generation"]:
        if item.get("formula"):
            console.print(f"[dim]{item['generation_id']}  {item['formula']}[/dim]")
    _print_job_ledger_events(job_id)
    console.print(f"\n[dim]{report['notes']}[/dim]")


def _print_workspace_ledger() -> None:
    try:
        from modal_sana.modal.billing import workspace_balance
        from modal_sana.modal.ledger import safe_query_shared_ledger
    except Exception as exc:  # noqa: BLE001
        console.print(f"[dim]shared ledger unavailable: {exc}[/dim]")
        return
    balance = workspace_balance()
    if balance.get("ok"):
        console.print(
            f"\nworkspace {balance.get('workspace') or ''}  ·  "
            f"this month {format_usd(balance.get('metered_usd'))} metered  ·  "
            f"remaining est. {format_usd(balance.get('remaining_usd'))}"
        )
    else:
        console.print(f"\n[dim]Modal balance: {balance.get('error') or 'unavailable'}[/dim]")
    ledger = safe_query_shared_ledger(period="day", page=1, per_page=20)
    snaps = ledger.get("snapshots") or {}
    if snaps:
        console.print(
            "shared ledger  "
            + "  ".join(
                f"{grain} {format_usd((snaps.get(grain) or {}).get('total_cost_usd'))}"
                for grain in ("hour", "day", "week", "month")
            )
        )
    _print_ledger_event_table(ledger.get("items") or [], title="Today's Modal charges")
    if ledger.get("error"):
        console.print(f"[dim]{ledger['error']}[/dim]")


def _print_job_ledger_events(job_id: str) -> None:
    try:
        from modal_sana.modal.ledger import safe_query_shared_ledger
    except Exception:
        return
    ledger = safe_query_shared_ledger(period="all", page=1, per_page=50, job_id=job_id)
    items = ledger.get("items") or []
    if not items:
        return
    _print_ledger_event_table(items, title="Modal charges for this job")


def _print_ledger_event_table(items: list[dict], *, title: str) -> None:
    if not items:
        return
    table = Table(title=title)
    table.add_column("TIME")
    table.add_column("KIND")
    table.add_column("JOB")
    table.add_column("GPU")
    table.add_column("$/s")
    table.add_column("GPU-S")
    table.add_column("$")
    table.add_column("CHAIN")
    for item in items:
        chain = item.get("chain") or []
        chain_text = " → ".join(
            str(step.get("detail") or step.get("name") or "")
            for step in chain
            if step.get("kind") in {"function_call", "input", "gpu_load", "gpu_generate", "job"}
        )
        table.add_row(
            str(item.get("ts") or "").replace("T", " ")[:19],
            str(item.get("kind") or ""),
            str(item.get("job_id") or "—"),
            str(item.get("billed_gpu") or item.get("actual_gpu") or item.get("requested_gpu") or "—"),
            f"{item['usd_per_second']:.6f}" if item.get("usd_per_second") is not None else "—",
            f"{float(item.get('gpu_seconds') or 0):.4f}",
            format_usd(item.get("cost_usd")),
            chain_text or "—",
        )
    console.print(table)


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


def _vram(item: dict) -> str:
    reserved = item.get("vram_reserved_mb")
    allocated = item.get("vram_allocated_mb")
    peak = item.get("vram_peak_mb")
    mb = reserved if reserved is not None else allocated if allocated is not None else peak
    if mb is None:
        return "—"
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.0f} MB"
