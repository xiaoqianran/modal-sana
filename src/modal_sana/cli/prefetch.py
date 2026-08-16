from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated, Any

import typer
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from modal_sana.cli.common import console
from modal_sana.modal.secrets import modal_download_secrets
from modal_sana.modal.weights import ids_needing_prefetch, models_to_prefetch


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

    Complete snapshots are skipped. Incomplete ones stream live byte progress.
    Default (no model name) downloads the base 1024px models.
    Pass --all to include 2K/4K as well.
    """
    import modal

    from modal_sana.modal.app import app
    from modal_sana.modal.client import ensure_local_app_objects
    from modal_sana.modal.deploy_mode import deploy_local_app, ensure_deployed_or_fallback
    from modal_sana.modal.prefetch import list_volume_models, prefetch_progress

    ids = models_to_prefetch(model, all_models=all_models)
    secrets = modal_download_secrets()
    ensure_local_app_objects()

    decision = ensure_deployed_or_fallback(deployed, required_models=ids)
    console.print(f"Modal: [bold]{decision.mode}[/bold] ({decision.reason})")

    if decision.use_deployed:
        name = decision.app_name
        progress_fn = _deployed_progress_fn(name)
        rows = _safe_volume_rows(modal.Function.from_name(name, "list_volume_models"))
        if status:
            _print_status(rows)
            return
        needed, cached = ids_needing_prefetch(ids, rows)
        _print_cached(cached)
        if not needed:
            return
        unknown = _stream_ids(progress_fn, needed, secrets=None)
        if unknown:
            console.print(
                "[yellow]Deployed prefetch does not know "
                f"{', '.join(unknown)}; redeploying and retrying[/yellow]"
            )
            deploy_local_app(name)
            _stream_ids(modal.Function.from_name(name, "prefetch_progress"), unknown, secrets=None)
        return

    with modal.enable_output():
        with app.run():
            rows = _safe_volume_rows(list_volume_models)
            if status:
                _print_status(rows)
                return
            needed, cached = ids_needing_prefetch(ids, rows)
            _print_cached(cached)
            if not needed:
                return
            fn = prefetch_progress.with_options(secrets=secrets) if secrets else prefetch_progress
            _stream_ids(fn, needed, secrets=None)


def _deployed_progress_fn(app_name: str):
    import modal
    from modal.exception import NotFoundError

    from modal_sana.modal.deploy_mode import deploy_local_app

    try:
        fn = modal.Function.from_name(app_name, "prefetch_progress")
        hydrate = getattr(fn, "hydrate", None)
        if callable(hydrate):
            hydrate()
        return fn
    except NotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        if "not found" not in str(exc).lower():
            raise
    console.print("[yellow]Deployed app has no prefetch_progress; redeploying[/yellow]")
    deploy_local_app(app_name)
    return modal.Function.from_name(app_name, "prefetch_progress")


def _safe_volume_rows(fn) -> list[dict[str, Any]]:
    try:
        rows = fn.remote()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Could not list Volume models ({exc}); will download[/yellow]")
        return []
    return list(rows or [])


def _print_cached(cached: list[str]) -> None:
    for model_id in cached:
        console.print(f"[green]✓[/green] {model_id} 已完整，跳过")


def _stream_ids(fn, ids: list[str], *, secrets: list[Any] | None) -> list[str]:
    """Stream live progress for each id. Returns unknown-model ids."""
    from modal_sana.modal.deploy_mode import is_unknown_model_error

    bound = fn
    if secrets and hasattr(fn, "with_options"):
        bound = fn.with_options(secrets=secrets)

    unknown: list[str] = []
    errors: list[tuple[str, BaseException]] = []
    console.print(f"[bold]CPU prefetch[/bold] {len(ids)} model(s): {', '.join(ids)}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.fields[model_id]}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[detail]}"),
        console=console,
    ) as progress:
        tasks = {
            model_id: progress.add_task(model_id, total=None, model_id=model_id, detail="排队")
            for model_id in ids
        }

        def run_one(model_id: str) -> None:
            try:
                for event in bound.remote_gen(model_id):
                    apply_prefetch_event(progress, tasks[model_id], event)
            except Exception as exc:  # noqa: BLE001
                apply_prefetch_event(
                    progress,
                    tasks[model_id],
                    {"event": "error", "error": str(exc), "model_id": model_id},
                )
                if is_unknown_model_error(exc):
                    unknown.append(model_id)
                else:
                    errors.append((model_id, exc))

        if len(ids) == 1:
            run_one(ids[0])
        else:
            with ThreadPoolExecutor(max_workers=len(ids)) as pool:
                futures = [pool.submit(run_one, model_id) for model_id in ids]
                for future in as_completed(futures):
                    future.result()

    for model_id, exc in errors:
        console.print(f"[red]{model_id}: {exc}[/red]")
    if errors:
        raise typer.Exit(code=1)
    return unknown


def apply_prefetch_event(progress: Progress, task_id: int, event: dict[str, Any]) -> None:
    """Update one Rich task from a Modal generator event."""
    kind = str(event.get("event") or "")
    status = str(event.get("status") or kind)
    current = str(event.get("current") or "")
    bytes_done = int(event.get("bytes_done") or 0)
    bytes_total = int(event.get("bytes_total") or 0)
    files_done = int(event.get("files_done") or 0)
    files_total = int(event.get("files_total") or 0)
    if kind in {"cached", "done"} and status == "cached":
        total = max(int(event.get("bytes") or 0), 1)
        progress.update(task_id, completed=total, total=total, detail="已完整，跳过")
        return
    if kind == "done" and status == "downloaded":
        total = max(int(event.get("bytes") or bytes_total or 1), 1)
        progress.update(task_id, completed=total, total=total, detail="完成")
        return
    if kind == "error":
        progress.update(task_id, detail=f"失败: {event.get('error') or 'error'}")
        return
    if kind == "start":
        progress.update(task_id, detail="检查本地快照")
        return
    total = bytes_total or files_total or None
    completed = bytes_done if bytes_total else files_done
    detail = current or status
    if files_total:
        detail = f"{files_done}/{files_total} {current}".strip()
    progress.update(task_id, completed=completed, total=total, detail=detail)


def _print_status(rows: list[dict]) -> None:
    table = Table(title="Modal Volume /cache/models")
    table.add_column("ID")
    table.add_column("COMPLETE")
    table.add_column("BYTES")
    table.add_column("MISSING")
    table.add_column("HF")
    for row in rows:
        missing = row.get("missing") or []
        table.add_row(
            row["model_id"],
            "yes" if row.get("complete") else "no",
            _human_bytes(int(row.get("bytes") or 0)),
            ", ".join(str(item) for item in missing[:3]),
            row.get("hf_id") or "",
        )
    console.print(table)


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} PB"
