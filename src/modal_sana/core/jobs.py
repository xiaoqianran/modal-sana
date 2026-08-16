from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any

from sqlmodel import col, select

from modal_sana.core.config import Settings
from modal_sana.core.cost import cost_for_seconds, format_usd
from modal_sana.core.events import Event, EventBus
from modal_sana.core.generator import GenerateRequest, GenerateResult, ImageGenerator
from modal_sana.core.hashes import task_hash
from modal_sana.core.ids import new_id
from modal_sana.core.mock import MockGenerator
from modal_sana.core.prompts import expand_seeds, resolve_steps_guidance
from modal_sana.core.scheduler import run_batches
from modal_sana.core.trace import list_spans, span, span_as_dict, span_tree, write_span
from modal_sana.modal.gpu import get_gpu
from modal_sana.models.sana.registry import get_model
from modal_sana.schemas.image import GalleryPage, ImageRecord
from modal_sana.schemas.job import JobConfig, JobSummary, PromptSpec
from modal_sana.storage.database import (
    Database,
    GenerationRow,
    ImageRow,
    JobRow,
    PromptTaskRow,
    dump_json,
    now,
)
from modal_sana.storage.images import ImageStore
from modal_sana.storage.metadata import metadata_line


class JobService:
    def __init__(self, settings: Settings, events: EventBus | None = None) -> None:
        self.settings = settings
        self.settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.images = ImageStore(settings.outputs_dir)
        self.events = events or EventBus()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._run_spans: dict[str, str] = {}
        self._map_spans: dict[str, str] = {}

    def create_job(self, specs: list[PromptSpec], config: JobConfig) -> JobSummary:
        get_model(config.model)
        get_gpu(config.gpu)
        if not specs:
            raise ValueError("No prompts to generate")

        job_id = new_id("job")
        created = now()
        generations: list[GenerationRow] = []
        tasks: list[PromptTaskRow] = []
        seen_hashes: set[str] = set()

        for spec in specs:
            if not spec.prompt.strip():
                continue
            model_id = spec.model or config.model
            get_model(model_id)
            steps, guidance = resolve_steps_guidance(spec, config)
            width = spec.width or config.width
            height = spec.height or config.height
            negative = spec.negative_prompt or config.negative_prompt
            count = spec.count if spec.count > 0 else config.count
            seeds = expand_seeds(spec.seed if spec.seed is not None else config.seed, count)
            task_id = new_id("ptk")
            tasks.append(
                PromptTaskRow(
                    id=task_id,
                    job_id=job_id,
                    prompt=spec.prompt,
                    negative_prompt=negative,
                    count=len(seeds),
                    status="pending",
                    source_id=spec.source_id,
                    config_json=dump_json(spec.model_dump()),
                )
            )
            for seed in seeds:
                digest = task_hash(
                    prompt=spec.prompt,
                    negative_prompt=negative,
                    seed=seed,
                    model=model_id,
                    width=width,
                    height=height,
                    steps=steps,
                    guidance=guidance,
                    image_format=config.image_format,
                )
                if config.deduplicate and config.dedup_mode == "skip" and digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                generations.append(
                    GenerationRow(
                        id=new_id("gen"),
                        job_id=job_id,
                        prompt_task_id=task_id,
                        prompt=spec.prompt,
                        negative_prompt=negative,
                        seed=seed,
                        model=model_id,
                        gpu=config.gpu,
                        steps=steps,
                        guidance=guidance,
                        width=width,
                        height=height,
                        image_format=config.image_format,
                        quality=config.quality,
                        status="pending",
                        task_hash=digest,
                    )
                )

        if not generations:
            raise ValueError("Every prompt was empty or deduplicated away")

        with self.db.session() as session:
            session.add(
                JobRow(
                    id=job_id,
                    status="pending",
                    created_at=created,
                    updated_at=created,
                    config_json=config.model_dump_json(),
                    total_images=len(generations),
                )
            )
            session.add_all(tasks)
            session.add_all(generations)

        write_span(
            self.db,
            name="job.create",
            job_id=job_id,
            kind="local",
            started_at=created,
            ended_at=now(),
            gpu=config.gpu,
            model=config.model,
            extra={"total_images": len(generations), "dry_run": config.dry_run},
        )
        summary = self.get_job(job_id)
        self.events.publish(Event("job.created", job_id, summary.model_dump(mode="json")))
        return summary

    def start(self, job_id: str) -> JobSummary:
        with self._lock:
            existing = self._threads.get(job_id)
            if existing and existing.is_alive():
                return self.get_job(job_id)
            thread = threading.Thread(target=self.run_job, args=(job_id,), daemon=True)
            self._threads[job_id] = thread
            thread.start()
        return self.get_job(job_id)

    def run_job(self, job_id: str, generator: ImageGenerator | None = None) -> JobSummary:
        job = self.get_job(job_id)
        config = job.config
        runner = generator or self._build_generator(config)
        started = now()
        self._update_job(job_id, status="running", started_at=started, updated_at=started)
        self.events.publish(
            Event(
                "job.started",
                job_id,
                {"total": job.total_images, "completed": job.completed_images},
            )
        )

        try:
            with span(
                self.db,
                "job.run",
                job_id,
                kind="local",
                gpu=config.gpu,
                model=config.model,
            ) as run_fields:
                self._run_spans[job_id] = run_fields["span_id"]
                self._execute(job_id, config, runner)
                meta = getattr(runner, "last_meta", {}) or {}
                if meta:
                    run_fields["modal_app_id"] = meta.get("modal_app_id")
                    run_fields["extra"] = meta
                    self._apply_run_meta(job_id, meta)
        except Exception as exc:
            self._update_job(job_id, status="failed", error=str(exc), completed_at=now())
            self.events.publish(Event("job.failed", job_id, {"error": str(exc)}))
            raise
        finally:
            self._run_spans.pop(job_id, None)

        return self.get_job(job_id)

    def resume(self, job_id: str, generator: ImageGenerator | None = None) -> JobSummary:
        self._cancel.discard(job_id)
        with self.db.session() as session:
            job = session.get(JobRow, job_id)
            if job is None:
                raise KeyError(job_id)
            for generation in session.exec(
                select(GenerationRow).where(
                    GenerationRow.job_id == job_id,
                    col(GenerationRow.status).in_(("failed", "retrying")),
                )
            ):
                generation.status = "pending"
                generation.error = None
            job.status = "pending"
            job.error = None
            job.completed_at = None
        return self.run_job(job_id, generator=generator)

    def cancel(self, job_id: str) -> JobSummary:
        self._cancel.add(job_id)
        self._update_job(job_id, status="cancelled", updated_at=now())
        self.events.publish(Event("job.cancelled", job_id, {}))
        return self.get_job(job_id)

    def list_jobs(self, limit: int = 100) -> list[JobSummary]:
        with self.db.session() as session:
            rows = list(
                session.exec(select(JobRow).order_by(col(JobRow.created_at).desc()).limit(limit))
            )
            return [row.summary() for row in rows]

    def get_job(self, job_id: str) -> JobSummary:
        with self.db.session() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise KeyError(job_id)
            return row.summary()

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        summary = self.get_job(job_id)
        with self.db.session() as session:
            generations = [
                _generation_dict(item)
                for item in session.exec(select(GenerationRow).where(GenerationRow.job_id == job_id))
            ]
            images = list(session.exec(select(ImageRow).where(ImageRow.job_id == job_id)))
        spans = list_spans(self.db, job_id)
        return {
            "job": summary.model_dump(mode="json"),
            "generations": generations,
            "images": len(images),
            "trace": [span_as_dict(row) for row in spans],
            "trace_tree": span_tree(spans),
            "cost": self.cost_report(job_id),
        }

    def get_trace(self, job_id: str) -> dict[str, Any]:
        self.get_job(job_id)
        spans = list_spans(self.db, job_id)
        return {
            "job_id": job_id,
            "spans": [span_as_dict(row) for row in spans],
            "tree": span_tree(spans),
        }

    def cost_report(self, job_id: str) -> dict[str, Any]:
        job = self.get_job(job_id)
        spec = get_gpu(job.gpu)
        with self.db.session() as session:
            generations = list(session.exec(select(GenerationRow).where(GenerationRow.job_id == job_id)))
        by_generation = [
            {
                "generation_id": item.id,
                "status": item.status,
                "gpu_seconds": item.gpu_seconds,
                "cost_usd": item.cost_usd,
                "load_ms": item.load_ms,
                "infer_ms": item.infer_ms,
                "encode_ms": item.encode_ms,
                "modal_function_call_id": item.modal_function_call_id,
                "modal_input_id": item.modal_input_id,
            }
            for item in generations
        ]
        return {
            "job_id": job_id,
            "gpu": job.gpu,
            "usd_per_second": spec.usd_per_second,
            "usd_per_hour": spec.usd_per_hour,
            "gpu_seconds": job.gpu_seconds or sum(item.gpu_seconds or 0.0 for item in generations),
            "cost_usd": job.cost_usd or sum(item.cost_usd or 0.0 for item in generations),
            "cost_display": format_usd(job.cost_usd or 0.0),
            "dry_run": job.dry_run,
            "modal_app_id": job.modal_app_id,
            "modal_run_url": job.modal_run_url,
            "by_generation": by_generation,
            "notes": (
                "List-price estimate: GPU seconds × Modal published $/s. "
                "Includes load (first call on a container), infer, and encode. "
                "Excludes image-build CPU and scaledown idle. "
                "Compare with `modal billing report` for invoice truth."
            ),
        }

    def list_images(
        self,
        *,
        job_id: str | None = None,
        model: str | None = None,
        gpu: str | None = None,
        q: str | None = None,
        sort: str = "newest",
        page: int = 1,
        per_page: int = 50,
    ) -> GalleryPage:
        page = max(page, 1)
        per_page = min(max(per_page, 1), 200)
        with self.db.session() as session:
            statement = select(ImageRow, GenerationRow).where(
                ImageRow.generation_id == GenerationRow.id
            )
            if job_id:
                statement = statement.where(ImageRow.job_id == job_id)
            if model:
                statement = statement.where(GenerationRow.model == model)
            if gpu:
                statement = statement.where(GenerationRow.gpu == gpu)
            if q:
                statement = statement.where(col(GenerationRow.prompt).contains(q))
            if sort == "oldest":
                statement = statement.order_by(col(ImageRow.created_at).asc())
            elif sort == "fastest":
                statement = statement.order_by(col(GenerationRow.latency_ms).asc())
            elif sort == "slowest":
                statement = statement.order_by(col(GenerationRow.latency_ms).desc())
            elif sort == "random":
                statement = statement.order_by(col(ImageRow.id))
            else:
                statement = statement.order_by(col(ImageRow.created_at).desc())

            rows = list(session.exec(statement))
            total = len(rows)
            start = (page - 1) * per_page
            sliced = rows[start : start + per_page]
            items = [
                _image_record(image, generation)
                for image, generation in sliced
            ]
        return GalleryPage(items=items, total=total, page=page, per_page=per_page)

    def get_image(self, image_id: str) -> ImageRecord:
        with self.db.session() as session:
            image = session.get(ImageRow, image_id)
            if image is None:
                raise KeyError(image_id)
            generation = session.get(GenerationRow, image.generation_id)
            if generation is None:
                raise KeyError(image.generation_id)
            return _image_record(image, generation)

    def regenerate(self, image_id: str) -> JobSummary:
        record = self.get_image(image_id)
        job = self.get_job(record.job_id)
        spec = PromptSpec(
            prompt=record.prompt,
            negative_prompt=record.negative_prompt,
            count=1,
            seed=None,
            width=record.width,
            height=record.height,
            steps=record.steps,
            guidance=record.guidance,
            model=record.model,
        )
        config = job.config.model_copy(update={"count": 1, "seed": None})
        created = self.create_job([spec], config)
        self.start(created.id)
        return created

    def _build_generator(self, config: JobConfig) -> ImageGenerator:
        if config.dry_run:
            return MockGenerator()
        from modal_sana.modal.client import ModalSanaGenerator

        return ModalSanaGenerator()

    def _execute(self, job_id: str, config: JobConfig, generator: ImageGenerator) -> None:
        with self.db.session() as session:
            pending = list(
                session.exec(
                    select(GenerationRow).where(
                        GenerationRow.job_id == job_id,
                        col(GenerationRow.status).in_(("pending", "failed", "retrying")),
                    )
                )
            )
            for generation in pending:
                generation.status = "pending"
                generation.error = None

        attempts_left = {item.id: max(config.retry, 0) for item in pending}
        remaining = list(pending)
        while remaining and job_id not in self._cancel:
            inflight = {item.id: item for item in remaining}
            failed_this_round: list[tuple[GenerationRow, str]] = []
            with span(
                self.db,
                "modal.map",
                job_id,
                kind="modal",
                parent_span_id=self._run_spans.get(job_id),
                gpu=config.gpu,
                model=config.model,
            ) as map_fields:
                self._map_spans[job_id] = map_fields["span_id"]
                try:
                    for result in run_batches(
                        remaining,
                        generator,
                        batch_size=config.batch_size,
                        gpu=config.gpu,
                        workers=config.workers,
                        model=config.model,
                        retry=config.retry,
                        deployed=config.deployed,
                    ):
                        generation = inflight.pop(result.generation_id, None)
                        if generation is None:
                            continue
                        if result.error or not result.image_bytes:
                            self._record_generation_telemetry(generation, result)
                            failed_this_round.append((generation, result.error or "empty image"))
                            continue
                        self._persist_success(generation, result)
                        self.events.publish(
                            Event(
                                "image.completed",
                                job_id,
                                {
                                    "generation_id": generation.id,
                                    "progress": self._progress(job_id),
                                    "cost_usd": self.get_job(job_id).cost_usd,
                                    **_runtime_fields(result.telemetry),
                                },
                            )
                        )
                        if job_id in self._cancel:
                            break
                finally:
                    meta = getattr(generator, "last_meta", {}) or {}
                    map_fields["modal_app_id"] = meta.get("modal_app_id")
                    map_fields["extra"] = meta
                    self._map_spans.pop(job_id, None)

            for generation in inflight.values():
                failed_this_round.append((generation, "worker did not return a result"))

            retryable: list[GenerationRow] = []
            with self.db.session() as session:
                for generation, error in failed_this_round:
                    row = session.get(GenerationRow, generation.id)
                    if row is None or row.status == "completed":
                        continue
                    if attempts_left.get(generation.id, 0) > 0:
                        attempts_left[generation.id] -= 1
                        row.status = "retrying"
                        row.attempt += 1
                        row.error = error
                        retryable.append(row)
                        self.events.publish(
                            Event(
                                "image.retrying",
                                job_id,
                                {"generation_id": generation.id, "error": error},
                            )
                        )
                    else:
                        row.status = "failed"
                        row.error = error
                        row.completed_at = now()
                        self.events.publish(
                            Event(
                                "image.failed",
                                job_id,
                                {
                                    "generation_id": generation.id,
                                    "error": error,
                                    "progress": self._progress(job_id),
                                },
                            )
                        )
            self._refresh_counters(job_id)
            remaining = retryable

        final = self.get_job(job_id)
        if job_id in self._cancel:
            status = "cancelled"
        elif final.failed_images and final.completed_images == 0:
            status = "failed"
        elif final.failed_images:
            status = "failed"
        else:
            status = "completed"
        self._update_job(job_id, status=status, completed_at=now(), error=None)
        self.events.publish(
            Event(
                f"job.{status}",
                job_id,
                self.get_job(job_id).model_dump(mode="json"),
            )
        )

    def _to_request(self, generation: GenerationRow) -> GenerateRequest:
        return GenerateRequest(
            generation_id=generation.id,
            prompt=generation.prompt,
            negative_prompt=generation.negative_prompt,
            seed=generation.seed,
            width=generation.width,
            height=generation.height,
            steps=generation.steps,
            guidance=generation.guidance,
            model=generation.model,
            image_format=generation.image_format,
            quality=generation.quality,
            job_id=generation.job_id,
            requested_gpu=generation.gpu,
        )

    def _persist_success(self, generation: GenerationRow, result: Any) -> None:
        created = now()
        parent = self._run_spans.get(generation.job_id)
        with span(
            self.db,
            "persist.image",
            generation.job_id,
            kind="local",
            parent_span_id=parent,
            generation_id=generation.id,
        ):
            self._persist_success_inner(generation, result, created)

    def _persist_success_inner(self, generation: GenerationRow, result: Any, created) -> None:
        with self.db.session() as session:
            job = session.get(JobRow, generation.job_id)
            row = session.get(GenerationRow, generation.id)
            if job is None or row is None:
                return
            next_index = job.completed_images + job.failed_images + 1
            path = self.images.write_image(
                generation.job_id,
                next_index,
                generation.image_format,
                result.image_bytes,
            )
            image_id = new_id("img")
            session.add(
                ImageRow(
                    id=image_id,
                    generation_id=generation.id,
                    job_id=generation.job_id,
                    prompt_task_id=generation.prompt_task_id,
                    path=str(path),
                    width=result.width or generation.width,
                    height=result.height or generation.height,
                    format=generation.image_format,
                    byte_size=len(result.image_bytes),
                    created_at=created,
                )
            )
            row.status = "completed"
            row.completed_at = created
            row.latency_ms = result.latency_ms
            row.error = None
            self._write_telemetry(row, job, result)
            job.completed_images += 1
            job.updated_at = created
            record = {
                "image_id": image_id,
                "generation_id": generation.id,
                "job_id": generation.job_id,
                "prompt": generation.prompt,
                "negative_prompt": generation.negative_prompt,
                "seed": generation.seed,
                "model": generation.model,
                "gpu": generation.gpu,
                "steps": generation.steps,
                "guidance": generation.guidance,
                "width": result.width or generation.width,
                "height": result.height or generation.height,
                "format": generation.image_format,
                "path": str(path),
                "latency_ms": result.latency_ms,
                "cost_usd": row.cost_usd,
                "gpu_seconds": row.gpu_seconds,
                "modal_function_call_id": row.modal_function_call_id,
                "modal_input_id": row.modal_input_id,
                "created_at": created.isoformat(),
            }
        self._record_modal_span(generation, result)
        self.images.append_metadata(generation.job_id, metadata_line(record))

    def _refresh_counters(self, job_id: str) -> None:
        with self.db.session() as session:
            job = session.get(JobRow, job_id)
            if job is None:
                return
            generations = list(
                session.exec(select(GenerationRow).where(GenerationRow.job_id == job_id))
            )
            job.completed_images = sum(1 for item in generations if item.status == "completed")
            job.failed_images = sum(1 for item in generations if item.status == "failed")
            job.updated_at = now()

    def _update_job(self, job_id: str, **fields: Any) -> None:
        with self.db.session() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise KeyError(job_id)
            for key, value in fields.items():
                setattr(row, key, value)
            if "updated_at" not in fields:
                row.updated_at = now()

    def _progress(self, job_id: str) -> dict[str, int]:
        job = self.get_job(job_id)
        return {
            "completed": job.completed_images,
            "failed": job.failed_images,
            "total": job.total_images,
        }

    def _record_generation_telemetry(self, generation: GenerationRow, result: GenerateResult) -> None:
        with self.db.session() as session:
            row = session.get(GenerationRow, generation.id)
            job = session.get(JobRow, generation.job_id)
            if row is None or job is None:
                return
            self._write_telemetry(row, job, result)
        self._record_modal_span(generation, result)

    def _write_telemetry(self, row: GenerationRow, job: JobRow, result: GenerateResult) -> None:
        tel = result.telemetry or {}
        gpu_seconds = float(tel["gpu_seconds"]) if tel.get("gpu_seconds") is not None else 0.0
        dry = bool(tel.get("dry_run") or job.config().dry_run)
        if tel.get("cost_usd") is not None:
            cost = float(tel["cost_usd"])
        elif dry:
            cost = 0.0
        else:
            billed_gpu = tel.get("actual_gpu") or row.gpu
            try:
                cost = cost_for_seconds(str(billed_gpu), gpu_seconds)
            except ValueError:
                cost = cost_for_seconds(row.gpu, gpu_seconds)

        prev_seconds = row.gpu_seconds or 0.0
        prev_cost = row.cost_usd or 0.0
        row.load_ms = _maybe_float(tel.get("load_ms"))
        row.infer_ms = _maybe_float(tel.get("infer_ms"))
        row.encode_ms = _maybe_float(tel.get("encode_ms"))
        row.gpu_seconds = gpu_seconds
        row.cost_usd = cost
        row.modal_function_call_id = tel.get("modal_function_call_id") or row.modal_function_call_id
        row.modal_input_id = tel.get("modal_input_id") or row.modal_input_id
        row.vram_allocated_mb = _maybe_float(tel.get("vram_allocated_mb"))
        row.extra_json = json.dumps(tel, default=str)
        job.gpu_seconds = (job.gpu_seconds or 0.0) - prev_seconds + gpu_seconds
        job.cost_usd = (job.cost_usd or 0.0) - prev_cost + cost
        if tel.get("modal_app_id"):
            job.modal_app_id = str(tel["modal_app_id"])

    def _record_modal_span(self, generation: GenerationRow, result: GenerateResult) -> None:
        tel = result.telemetry or {}
        duration = float(result.latency_ms or 0.0)
        ended = now()
        started = ended - timedelta(milliseconds=duration)
        cost = None
        if tel.get("cost_usd") is not None:
            cost = float(tel["cost_usd"])
        elif tel.get("gpu_seconds") is not None and not tel.get("dry_run"):
            cost = cost_for_seconds(generation.gpu, float(tel["gpu_seconds"]))
        elif tel.get("dry_run"):
            cost = 0.0
        write_span(
            self.db,
            name="modal.generate",
            job_id=generation.job_id,
            kind="modal",
            parent_span_id=self._map_spans.get(generation.job_id)
            or self._run_spans.get(generation.job_id),
            generation_id=generation.id,
            status="error" if result.error else "ok",
            started_at=started,
            ended_at=ended,
            duration_ms=duration,
            gpu=generation.gpu,
            model=generation.model,
            modal_function_call_id=tel.get("modal_function_call_id"),
            modal_input_id=tel.get("modal_input_id"),
            modal_app_id=tel.get("modal_app_id"),
            cost_usd=cost,
            extra=tel,
        )

    def _apply_run_meta(self, job_id: str, meta: dict[str, Any]) -> None:
        fields: dict[str, Any] = {}
        if meta.get("modal_app_id"):
            fields["modal_app_id"] = meta["modal_app_id"]
        if meta.get("modal_run_url"):
            fields["modal_run_url"] = meta["modal_run_url"]
        if fields:
            self._update_job(job_id, **fields)


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_extra(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _runtime_fields(tel: dict[str, Any] | None) -> dict[str, Any]:
    data = tel or {}
    applied = data.get("applied") if isinstance(data.get("applied"), dict) else {}
    out: dict[str, Any] = {}
    for key in (
        "actual_gpu",
        "actual_device",
        "requested_gpu",
        "gpu_match",
        "loaded_model",
        "requested_model",
        "model_match",
        "from_snapshot",
        "cpu_load_ms",
        "gpu_move_ms",
    ):
        value = data.get(key)
        if value is None:
            value = applied.get(key)
        if value is not None:
            out[key] = value
    return out


def _image_record(image: ImageRow, generation: GenerationRow) -> ImageRecord:
    runtime = _runtime_fields(_parse_extra(generation.extra_json))
    return ImageRecord(
        id=image.id,
        generation_id=image.generation_id,
        job_id=image.job_id,
        prompt_task_id=image.prompt_task_id,
        path=image.path,
        prompt=generation.prompt,
        negative_prompt=generation.negative_prompt,
        seed=generation.seed,
        model=generation.model,
        gpu=generation.gpu,
        steps=generation.steps,
        guidance=generation.guidance,
        width=image.width,
        height=image.height,
        format=image.format,
        byte_size=image.byte_size,
        latency_ms=generation.latency_ms,
        cost_usd=generation.cost_usd,
        infer_ms=generation.infer_ms,
        load_ms=generation.load_ms,
        encode_ms=generation.encode_ms,
        gpu_seconds=generation.gpu_seconds,
        modal_function_call_id=generation.modal_function_call_id,
        modal_input_id=generation.modal_input_id,
        created_at=image.created_at,
        actual_gpu=runtime.get("actual_gpu"),
        actual_device=runtime.get("actual_device"),
        gpu_match=runtime.get("gpu_match"),
    )


def _generation_dict(item: GenerationRow) -> dict[str, Any]:
    return {
        "id": item.id,
        "prompt": item.prompt,
        "seed": item.seed,
        "status": item.status,
        "error": item.error,
        "latency_ms": item.latency_ms,
        "load_ms": item.load_ms,
        "infer_ms": item.infer_ms,
        "encode_ms": item.encode_ms,
        "gpu_seconds": item.gpu_seconds,
        "cost_usd": item.cost_usd,
        "modal_function_call_id": item.modal_function_call_id,
        "modal_input_id": item.modal_input_id,
        "vram_allocated_mb": item.vram_allocated_mb,
        **_runtime_fields(_parse_extra(item.extra_json)),
    }
