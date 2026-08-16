from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from modal_sana import __version__
from modal_sana.core.config import load_settings
from modal_sana.core.doctor import run_doctor
from modal_sana.core.events import EventBus
from modal_sana.core.jobs import JobService
from modal_sana.core.ledger import PERIODS, Period
from modal_sana.core.predict import predict_run
from modal_sana.core.prompts import parse_prompt_file, parse_prompt_text
from modal_sana.modal.billing import workspace_balance
from modal_sana.modal.client import ensure_local_app_objects
from modal_sana.modal.deploy_mode import inspect_deploy_target
from modal_sana.modal.gpu import list_gpus
from modal_sana.modal.ledger import load_dict_events, safe_query_shared_ledger
from modal_sana.models.sana.registry import get_model, list_models
from modal_sana.schemas.job import JobConfig, PromptSpec

router = APIRouter(prefix="/api")
ensure_local_app_objects()
_settings = load_settings()
_events = EventBus()
_service = JobService(_settings, events=_events)


def configure(settings=None, events=None) -> JobService:
    """Replace the process-wide service. Used by tests."""
    global _settings, _events, _service
    _settings = settings or load_settings()
    _events = events or EventBus()
    _service = JobService(_settings, events=_events)
    return _service


def service() -> JobService:
    return _service


class CreateJobBody(BaseModel):
    prompt: str | None = None
    prompts: list[str] = Field(default_factory=list)
    text: str | None = None
    count: int = 1
    model: str = "sana-sprint-1.6b"
    gpu: str = "L40S"
    width: int = 1024
    height: int = 1024
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    batch_size: int = 4
    workers: int = 2
    retry: int = 3
    image_format: Literal["webp", "png", "jpg"] = "png"
    quality: int = 90
    negative_prompt: str = ""
    dry_run: bool = False
    deployed: bool | None = None
    deduplicate: bool = False


def _config_from_body(body: CreateJobBody) -> JobConfig:
    return JobConfig(
        model=body.model,
        gpu=body.gpu,
        width=body.width,
        height=body.height,
        steps=body.steps,
        guidance=body.guidance,
        seed=body.seed,
        count=body.count,
        batch_size=body.batch_size,
        workers=body.workers,
        retry=body.retry,
        image_format=body.image_format,
        quality=body.quality,
        negative_prompt=body.negative_prompt,
        dry_run=body.dry_run,
        deployed=body.deployed,
        deduplicate=body.deduplicate,
    )


def _specs_from_body(body: CreateJobBody) -> list[PromptSpec]:
    specs: list[PromptSpec] = []
    if body.prompt:
        specs.append(PromptSpec(prompt=body.prompt, count=body.count, seed=body.seed))
    for line in body.prompts:
        if line.strip():
            specs.append(PromptSpec(prompt=line, count=body.count, seed=body.seed))
    if body.text:
        parsed = parse_prompt_text(body.text)
        for spec in parsed:
            spec.count = body.count
            spec.seed = body.seed
        specs.extend(parsed)
    if not specs:
        raise HTTPException(400, "Provide prompt, prompts, or text")
    return specs


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta")
def meta() -> dict[str, Any]:
    return {
        "models": [spec.__dict__ for spec in list_models()],
        "gpus": [
            {
                "id": spec.id,
                "name": spec.id,
                "recommended_batch": spec.recommended_batch,
                "usd_per_hour": spec.usd_per_hour,
                "usd_per_second": spec.usd_per_second,
                "vram_gb": spec.vram_gb,
                "notes": spec.notes,
            }
            for spec in list_gpus()
        ],
        "defaults": {
            "model": _settings.default_model,
            "gpu": _settings.default_gpu,
            "port": _settings.port,
            "data_dir": str(_settings.data_dir),
            "monthly_credits_usd": _settings.monthly_credits_usd,
            "prefer_deployed": True,
            "image_format": "png",
        },
        "runtime": inspect_deploy_target(),
        "version": __version__,
    }


@router.get("/cost/forecast")
def cost_forecast(
    model: str = "sana-sprint-1.6b",
    gpu: str = "L40S",
    count: int = Query(1, ge=1, le=10_000),
    width: int = Query(1024, ge=256, le=4096),
    height: int = Query(1024, ge=256, le=4096),
    steps: int | None = Query(None, ge=1, le=200),
    batch_size: int = Query(4, ge=1, le=64),
    workers: int = Query(2, ge=1, le=32),
    period: str = "day",
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    grain: Period = period if period in PERIODS else "day"  # type: ignore[assignment]
    try:
        get_model(model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    history = []
    history_error = None
    try:
        history = load_dict_events(refresh=False)
    except Exception as exc:  # noqa: BLE001
        history_error = f"{type(exc).__name__}: {exc}"
    try:
        predict = predict_run(
            model=model,
            gpu=gpu,
            count=count,
            width=width,
            height=height,
            steps=steps,
            batch_size=batch_size,
            workers=workers,
            history=history,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ledger = safe_query_shared_ledger(period=grain, page=page, per_page=per_page)
    if history_error and not ledger.get("error"):
        ledger["error"] = history_error
    return {
        "predict": predict,
        "balance": workspace_balance(),
        "ledger": ledger,
    }


@router.get("/cost/ledger")
def cost_ledger(
    period: str = "all",
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=200),
    kind: str | None = None,
    model: str | None = None,
    gpu: str | None = None,
) -> dict[str, Any]:
    grain: Period = period if period in PERIODS else "all"  # type: ignore[assignment]
    return safe_query_shared_ledger(
        period=grain,
        page=page,
        per_page=per_page,
        kind=kind,
        model=model,
        gpu=gpu,
    )


@router.get("/cost/balance")
def cost_balance() -> dict[str, Any]:
    return workspace_balance()


@router.get("/doctor")
def doctor() -> dict[str, Any]:
    report = run_doctor()
    return {
        "ready": report.ready,
        "checks": [check.__dict__ for check in report.checks],
    }


@router.post("/jobs")
def create_job(body: CreateJobBody) -> dict[str, Any]:
    try:
        job = _service.create_job(_specs_from_body(body), _config_from_body(body))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _service.start(job.id)
    return job.model_dump(mode="json")


@router.post("/jobs/from-file")
async def create_job_from_file(
    file: UploadFile = File(...),
    model: str = "sana-sprint-1.6b",
    gpu: str = "L40S",
    count: int = 1,
    width: int = 1024,
    height: int = 1024,
    steps: int | None = None,
    guidance: float | None = None,
    seed: int | None = None,
    batch_size: int = 4,
    workers: int = 2,
    dry_run: bool = False,
    deployed: bool | None = None,
    image_format: str = "png",
) -> dict[str, Any]:
    name = Path(file.filename or "prompts.txt").name
    tmp = _settings.data_dir / f"upload-{name}"
    tmp.write_bytes(await file.read())
    try:
        specs = parse_prompt_file(tmp)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)
    fmt = image_format if image_format in {"webp", "png", "jpg"} else "png"
    config = JobConfig(
        model=model,
        gpu=gpu,
        count=count,
        width=width,
        height=height,
        steps=steps,
        guidance=guidance,
        seed=seed,
        batch_size=batch_size,
        workers=workers,
        dry_run=dry_run,
        deployed=deployed,
        image_format=fmt,  # type: ignore[arg-type]
    )
    try:
        job = _service.create_job(specs, config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _service.start(job.id)
    return job.model_dump(mode="json")


@router.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [job.model_dump(mode="json") for job in _service.list_jobs()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return _service.get_job_detail(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc


@router.get("/jobs/{job_id}/trace")
def get_job_trace(job_id: str) -> dict[str, Any]:
    try:
        return _service.get_trace(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc


@router.get("/jobs/{job_id}/cost")
def get_job_cost(job_id: str) -> dict[str, Any]:
    try:
        return _service.cost_report(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    try:
        _service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc
    import threading

    threading.Thread(target=_service.resume, args=(job_id,), daemon=True).start()
    return _service.get_job(job_id).model_dump(mode="json")


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return _service.cancel(job_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    try:
        snapshot = _service.get_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown job {job_id}") from exc

    subscriber = _events.subscribe(job_id)

    async def stream():
        yield _sse({"type": "job.snapshot", "job_id": job_id, "payload": snapshot.model_dump(mode="json")})
        for event in _events.history(job_id):
            yield _sse(event.as_dict())
        try:
            while True:
                event = await asyncio.to_thread(subscriber.get)
                yield _sse(event.as_dict())
                if event.type in {"job.completed", "job.failed", "job.cancelled"}:
                    break
        finally:
            _events.unsubscribe(job_id, subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/gallery")
def gallery(
    job_id: str | None = None,
    model: str | None = None,
    gpu: str | None = None,
    q: str | None = None,
    sort: str = "newest",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    result = _service.list_images(
        job_id=job_id,
        model=model,
        gpu=gpu,
        q=q,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    return result.model_dump(mode="json")


@router.get("/images/{image_id}")
def image_meta(image_id: str) -> dict[str, Any]:
    try:
        return _service.get_image(image_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, f"unknown image {image_id}") from exc


@router.get("/images/{image_id}/file")
def image_file(image_id: str) -> FileResponse:
    try:
        record = _service.get_image(image_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown image {image_id}") from exc
    path = Path(record.path)
    if not path.exists():
        raise HTTPException(404, "image file missing on disk")
    media = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg"}.get(record.format, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/images/{image_id}/regenerate")
def regenerate(image_id: str) -> dict[str, Any]:
    try:
        job = _service.regenerate(image_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return job.model_dump(mode="json")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
