from __future__ import annotations

from typing import Annotated

import typer
from rich.pretty import Pretty

from modal_sana.cli.common import console, jobs_table, run_and_watch, service


def jobs() -> None:
    """List recent jobs."""
    svc = service()
    console.print(jobs_table(svc.list_jobs()))


def job(
    job_id: Annotated[str, typer.Argument(help="Job id")],
) -> None:
    """Show one job and its generation statuses."""
    svc = service()
    try:
        detail = svc.get_job_detail(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    console.print(Pretty(detail, expand_all=False))


def resume(
    job_id: Annotated[str, typer.Argument(help="Job id to continue")],
) -> None:
    """Re-queue failed / pending generations only."""
    svc = service()
    try:
        summary = svc.get_job(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    run_and_watch(svc, summary, resume=True)


def cancel(
    job_id: Annotated[str, typer.Argument(help="Job id")],
) -> None:
    """Stop accepting more work for a running job."""
    svc = service()
    try:
        summary = svc.cancel(job_id)
    except KeyError as exc:
        raise typer.BadParameter(f"unknown job {job_id}") from exc
    console.print(f"cancelled {summary.id}")
