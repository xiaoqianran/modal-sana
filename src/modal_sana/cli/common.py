from __future__ import annotations

import queue
import threading
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from modal_sana.core.config import Settings, load_settings
from modal_sana.core.cost import format_usd
from modal_sana.core.events import Event
from modal_sana.core.jobs import JobService
from modal_sana.schemas.job import JobConfig, JobSummary

console = Console()

OptPrompt = Annotated[str | None, typer.Argument(help="Text prompt. Omit when using --file.")]
OptModel = Annotated[str, typer.Option("--model", "-m", help="SANA model id")]
OptGPU = Annotated[str, typer.Option("--gpu", "-g", help="Modal GPU id")]
OptCount = Annotated[int, typer.Option("--count", "-n", help="Images per prompt")]
OptSteps = Annotated[int | None, typer.Option("--steps", help="Inference steps")]
OptGuidance = Annotated[float | None, typer.Option("--guidance", help="Guidance / CFG")]
OptWidth = Annotated[int, typer.Option("--width", help="Image width")]
OptHeight = Annotated[int, typer.Option("--height", help="Image height")]
OptSeed = Annotated[int | None, typer.Option("--seed", help="Base seed; count expands +1")]
OptBatch = Annotated[int, typer.Option("--batch-size", help="Images per GPU forward")]
OptWorkers = Annotated[int, typer.Option("--workers", help="Concurrent Modal GPU containers")]
OptRetry = Annotated[int, typer.Option("--retry", help="Local retries after Modal gives up")]
OptFormat = Annotated[str, typer.Option("--format", help="webp | png | jpg")]
OptQuality = Annotated[int, typer.Option("--quality", help="WebP/JPEG quality")]
OptNegative = Annotated[str, typer.Option("--negative", help="Negative prompt")]
OptDryRun = Annotated[bool, typer.Option("--dry-run", help="Local placeholder images, no GPU")]
OptDeployed = Annotated[bool, typer.Option("--deployed", help="Call a deployed Modal app")]
OptDedup = Annotated[bool, typer.Option("--deduplicate", help="Skip duplicate prompt+config+seed")]


def settings() -> Settings:
    return load_settings()


def service() -> JobService:
    return JobService(settings())


def job_config(
    *,
    model: str,
    gpu: str,
    count: int,
    steps: int | None,
    guidance: float | None,
    width: int,
    height: int,
    seed: int | None,
    batch_size: int,
    workers: int,
    retry: int,
    image_format: str,
    quality: int,
    negative: str,
    dry_run: bool,
    deployed: bool,
    deduplicate: bool,
) -> JobConfig:
    fmt = image_format.lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"webp", "png", "jpg"}:
        raise typer.BadParameter("format must be webp, png, or jpg")
    return JobConfig(
        model=model,
        gpu=gpu,
        count=count,
        steps=steps,
        guidance=guidance,
        width=width,
        height=height,
        seed=seed,
        batch_size=batch_size,
        workers=workers,
        retry=retry,
        image_format=fmt,  # type: ignore[arg-type]
        quality=quality,
        negative_prompt=negative,
        dry_run=dry_run,
        deployed=deployed,
        deduplicate=deduplicate,
    )


def print_job_header(job: JobSummary) -> None:
    console.print(f"[bold]Submitted job:[/bold] {job.id}")
    console.print(f"GPU: {job.gpu}")
    console.print(f"Model: {job.model}")
    if job.config.dry_run:
        console.print("[yellow]dry-run[/yellow] — local placeholders, Modal is not called")
    console.print()


def run_and_watch(svc: JobService, job: JobSummary, *, resume: bool = False) -> JobSummary:
    print_job_header(job)
    subscriber = svc.events.subscribe(job.id)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            if resume:
                svc.resume(job.id)
            else:
                svc.run_job(job.id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Generating", total=max(job.total_images, 1))
            while worker.is_alive() or not subscriber.empty():
                try:
                    event = subscriber.get(timeout=0.2)
                except queue.Empty:
                    continue
                _handle_event(progress, task_id, event)
        worker.join()
        if errors:
            raise errors[0]
        final = svc.get_job(job.id)
        _print_footer(svc, final)
        if final.status == "failed":
            raise typer.Exit(code=1)
        return final
    finally:
        svc.events.unsubscribe(job.id, subscriber)


def watch_only(svc: JobService, job: JobSummary) -> None:
    """Used when the job already runs in a background thread."""
    print_job_header(job)
    subscriber = svc.events.subscribe(job.id)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Generating", total=max(job.total_images, 1))
            while True:
                event = subscriber.get()
                _handle_event(progress, task_id, event)
                if event.type.startswith("job.") and event.type != "job.started" and event.type != "job.created":
                    break
        _print_footer(svc, svc.get_job(job.id))
    finally:
        svc.events.unsubscribe(job.id, subscriber)


def _handle_event(progress: Progress, task_id: int, event: Event) -> None:
    payload = event.payload or {}
    prog = payload.get("progress") or {}
    if event.type == "image.completed":
        done = int(prog.get("completed") or 0)
        total = int(prog.get("total") or 0)
        progress.update(task_id, completed=done, total=total or None, description="Generating")
        running = payload.get("cost_usd")
        extra = f"  {format_usd(running)}" if running else ""
        console.print(f"[green]✓[/green] image {done}/{total or '?'}{extra}")
    elif event.type == "image.failed":
        console.print(f"[red]✗[/red] {payload.get('error', 'failed')}")
    elif event.type == "job.failed":
        console.print(f"[red]job failed:[/red] {payload.get('error', '')}")


def _print_footer(svc: JobService, job: JobSummary) -> None:
    console.print()
    if job.status == "completed":
        console.print(f"[green]✓ {job.completed_images} images[/green]")
    elif job.status == "failed":
        console.print(f"[red]✗ {job.completed_images}/{job.total_images} saved, {job.failed_images} failed[/red]")
        if job.error:
            console.print(job.error)
    else:
        console.print(f"{job.status}: {job.completed_images}/{job.total_images}")
    if job.cost_usd is not None:
        seconds = job.gpu_seconds or 0.0
        console.print(
            f"cost ≈ {format_usd(job.cost_usd)}  ·  {seconds:.3f}s GPU on {job.gpu}"
        )
    if job.modal_run_url:
        console.print(job.modal_run_url)
    console.print(f"saved → {svc.settings.outputs_dir / job.id}/")
    console.print(f"trace → modal-sana trace {job.id}")


def jobs_table(jobs: list[JobSummary]) -> Table:
    table = Table(title="Jobs")
    table.add_column("ID")
    table.add_column("STATUS")
    table.add_column("IMAGES")
    table.add_column("MODEL")
    table.add_column("GPU")
    table.add_column("COST")
    for job in jobs:
        table.add_row(
            job.id,
            job.status,
            f"{job.completed_images}/{job.total_images}",
            job.model,
            job.gpu,
            format_usd(job.cost_usd) if job.cost_usd is not None else "—",
        )
    return table
