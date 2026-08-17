import os
import time
from datetime import datetime, timezone
from typing import Any

import modal

from modal_sana.core.cost import cost_for_seconds, item_gpu_seconds
from modal_sana.modal.app import app
from modal_sana.modal.gpu import get_gpu
from modal_sana.modal.image import image
from modal_sana.modal.ledger import archive_cost_events, list_cost_events, record_cost_events
from modal_sana.modal.prefetch import (
    list_volume_models,
    prefetch_model,
    prefetch_progress,
    registered_model_ids,
)
from modal_sana.modal.runtime import probe_runtime
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
from modal_sana.modal.weights import assert_model_ready
from modal_sana.models.sana.registry import get_model

MINUTES = 60
# Modal allows 2s–20min. Idle GPU+CPU of this container are billed until then.
SCALEDOWN_SECONDS = 10
# CPU memory snapshots (deployed only). Not GPU VRAM snapshots — those do not
# help when the bottleneck is reading weights off a Volume.
MEMORY_SNAPSHOT = True


def _dtype(name: str):
    import torch

    return torch.bfloat16 if name == "bfloat16" else torch.float16


def _load_pipeline_cpu(model_id: str):
    """Load weights into CPU RAM. Must not touch CUDA (breaks CPU snapshots)."""
    import torch

    spec = get_model(model_id)
    dtype = _dtype(spec.recommended_dtype)
    if spec.pipeline == "sana-sprint":
        from diffusers import SanaSprintPipeline

        pipeline_cls = SanaSprintPipeline
    else:
        from diffusers import SanaPipeline

        pipeline_cls = SanaPipeline
    path = assert_model_ready(model_id)
    pipe = pipeline_cls.from_pretrained(str(path), torch_dtype=dtype, local_files_only=True)
    if hasattr(pipe, "vae") and pipe.vae is not None:
        pipe.vae.to(dtype)
    if hasattr(pipe, "text_encoder") and pipe.text_encoder is not None:
        pipe.text_encoder.to(torch.bfloat16)
    if spec.vae_tiling:
        _enable_vae_tiling(pipe)
    return pipe


def _enable_vae_tiling(pipe) -> None:
    """Official 2K/4K path. Avoids VAE OOM; must not run CUDA in snap=True beyond this."""
    vae = getattr(pipe, "vae", None)
    if vae is None or not hasattr(vae, "enable_tiling"):
        return
    try:
        vae.enable_tiling(
            tile_sample_min_height=1024,
            tile_sample_min_width=1024,
            tile_sample_stride_height=896,
            tile_sample_stride_width=896,
        )
    except TypeError:
        vae.enable_tiling()


def _move_pipeline_cuda(pipe):
    pipe.to("cuda")
    return pipe


def detect_snapshot_restore(snap_mono: float | None, now_mono: float) -> bool:
    """Restore starts a new monotonic clock; create-path time only moves forward."""
    if snap_mono is None:
        return True
    return now_mono < snap_mono


def _encode(image, image_format: str, quality: int) -> bytes:
    from modal_sana.storage.encode import encode_image

    return encode_image(image, image_format, quality)


@app.cls(
    image=image,
    gpu="L40S",
    timeout=15 * MINUTES,
    scaledown_window=SCALEDOWN_SECONDS,
    volumes={CACHE_DIR: huggingface_cache_volume()},
    retries=modal.Retries(max_retries=2, backoff_coefficient=2.0),
    enable_memory_snapshot=MEMORY_SNAPSHOT,
)
class SanaWorker:
    """One warm GPU container = one loaded SANA pipeline.

    Weights must already be on the Volume (CPU ``prefetch_model``).
    This class only loads from ``/cache/models/{id}`` with
    ``local_files_only=True`` — it never downloads from Hugging Face.

    Deployed apps take a CPU memory snapshot after ``load_cpu`` (import +
    from_pretrained on CPU). Later cold starts restore that RAM and only run
    ``load_gpu`` (``pipe.to("cuda")``). Snapshots are per model_id and GPU
    type; they exist only after ``modal deploy``. Ephemeral ``app.run()``
    still does both enters every time.

    GPU type and max_containers MUST be applied at the call site with
    `SanaWorker.with_options(gpu=..., max_containers=...)`. The ``gpu="L40S"``
    on this decorator is only the fallback if a caller forgets with_options.
    After the last input, Modal may keep this container (GPU + its CPU)
    idle for ``SCALEDOWN_SECONDS`` then scale to zero.
    """

    model_id: str = modal.parameter(default="sana-sprint-1.6b")

    @modal.enter(snap=True)
    def load_cpu(self) -> None:
        huggingface_cache_volume().reload()
        started = time.perf_counter()
        self.pipe = _load_pipeline_cpu(self.model_id)
        self._cpu_load_ms = (time.perf_counter() - started) * 1000
        self._snap_mono = time.monotonic()

    @modal.enter(snap=False)
    def load_gpu(self) -> None:
        started = time.perf_counter()
        self.pipe = _move_pipeline_cuda(self.pipe)
        self._gpu_move_ms = (time.perf_counter() - started) * 1000
        self._from_snapshot = detect_snapshot_restore(
            getattr(self, "_snap_mono", None),
            time.monotonic(),
        )
        cpu_ms = float(getattr(self, "_cpu_load_ms", 0.0) or 0.0)
        if self._from_snapshot:
            self._load_ms = self._gpu_move_ms
        else:
            self._load_ms = cpu_ms + self._gpu_move_ms
        self._calls = 0
        self._runtime = probe_runtime(
            requested_model=self.model_id,
            loaded_model=self.model_id,
        )
        self._runtime["from_snapshot"] = self._from_snapshot
        self._runtime["cpu_load_ms"] = cpu_ms
        self._runtime["gpu_move_ms"] = self._gpu_move_ms
        self._runtime.update(_vram_stats())

    @modal.method()
    def generate_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        import torch

        items = payload.get("items") or []
        results: list[dict[str, Any]] = []
        if not items:
            return {"items": results, **_call_ids()}

        grouped: dict[tuple, list[dict[str, Any]]] = {}
        for item in items:
            key = (
                int(item["width"]),
                int(item["height"]),
                int(item["steps"]),
                round(float(item["guidance"]), 4),
                item.get("image_format", "png"),
                int(item.get("quality", 90)),
            )
            grouped.setdefault(key, []).append(item)

        ids = _call_ids()
        cold = self._calls == 0
        self._calls += 1
        n_all = max(len(items), 1)
        load_ms = float(getattr(self, "_load_ms", 0.0) or 0.0)
        runtime = probe_runtime(
            requested_gpu=payload.get("requested_gpu") or (items[0].get("requested_gpu") if items else None),
            requested_model=self.model_id,
            loaded_model=self.model_id,
        )
        if getattr(self, "_runtime", None):
            runtime = {**self._runtime, **{k: v for k, v in runtime.items() if v is not None}}

        for (width, height, steps, guidance, image_format, quality), group in grouped.items():
            batch_meta = {
                "batch_size_requested": len(group),
                "batch_size_effective": len(group),
                "batch_fallback_reason": None,
            }
            try:
                infer_ms, images, batch_meta = self._infer_group(
                    group, width, height, steps, guidance
                )
                error = None
            except Exception as exc:  # noqa: BLE001 — surface to local job state
                infer_ms = 0.0
                images = []
                error = str(exc)
                batch_meta = {
                    **batch_meta,
                    "batch_size_effective": 0,
                    "batch_fallback_reason": f"{type(exc).__name__}: {exc}",
                }
            batch_vram = {
                key: batch_meta.get(key)
                for key in (
                    "vram_allocated_mb",
                    "vram_reserved_mb",
                    "vram_peak_mb",
                    "vram_peak_reserved_mb",
                    "vram_free_mb",
                    "vram_total_mb",
                    "vram_attempt_peak_mb",
                    "vram_attempt_peak_reserved_mb",
                    "vram_oom_peak_mb",
                    "vram_oom_peak_reserved_mb",
                )
                if key in batch_meta
            }

            if error:
                per_infer = infer_ms / max(len(group), 1)
                for item in group:
                    gpu_seconds = item_gpu_seconds(
                        load_ms=load_ms,
                        infer_ms=infer_ms,
                        encode_ms=0.0,
                        cold_start=cold,
                        group_size=len(group),
                        load_group_size=n_all,
                    )
                    results.append(
                        {
                            "generation_id": item["generation_id"],
                            "image_bytes": None,
                            "width": width,
                            "height": height,
                            "latency_ms": per_infer + (load_ms / n_all if cold else 0.0),
                            "error": error,
                            **ids,
                            "load_ms": load_ms if cold else 0.0,
                            "infer_ms": per_infer,
                            "encode_ms": 0.0,
                            "gpu_seconds": gpu_seconds,
                            "cold_start": cold,
                            **batch_meta,
                            **batch_vram,
                        }
                    )
                _clear_cuda_cache()
                continue

            per_infer = infer_ms / max(len(images), 1)
            for item, image in zip(group, images, strict=False):
                encode_started = time.perf_counter()
                encoded = _encode(image, image_format, quality)
                encode_ms = (time.perf_counter() - encode_started) * 1000
                gpu_seconds = item_gpu_seconds(
                    load_ms=load_ms,
                    infer_ms=infer_ms,
                    encode_ms=encode_ms,
                    cold_start=cold,
                    group_size=len(group),
                    load_group_size=n_all,
                )
                results.append(
                    {
                        "generation_id": item["generation_id"],
                        "image_bytes": encoded,
                        "width": image.width,
                        "height": image.height,
                        "latency_ms": per_infer + encode_ms + (load_ms / n_all if cold else 0.0),
                        "error": None,
                        **ids,
                        "load_ms": load_ms if cold else 0.0,
                        "infer_ms": per_infer,
                        "encode_ms": encode_ms,
                        "gpu_seconds": gpu_seconds,
                        "cold_start": cold,
                        **batch_meta,
                        **batch_vram,
                    }
                )
            _clear_cuda_cache()
        _stamp_applied(results, items, runtime, self.model_id)
        _publish_cost_events(results, items, runtime, cold=cold, load_ms=load_ms, ids=ids)
        return {"items": results, "runtime": runtime, **ids}

    def _infer_group(self, group: list[dict[str, Any]], width: int, height: int, steps: int, guidance: float):
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            images, batch_meta = self._run_group(group, width, height, steps, guidance)
        finally:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        return (time.perf_counter() - started) * 1000, images, batch_meta

    def _run_group(self, group: list[dict[str, Any]], width: int, height: int, steps: int, guidance: float):
        """Run the largest safe batch and keep success/OOM memory peaks separate.

        ``vram_peak_*`` describes the largest *successful* effective sub-batch.
        ``vram_attempt_peak_*`` includes failed oversized attempts, while
        ``vram_oom_peak_*`` isolates CUDA OOM attempts.  This distinction makes
        the telemetry useful for tuning instead of making a BS=4 fallback look
        like it actually consumed the failed BS=8 peak.
        """
        requested = len(group)
        _reset_vram_peak_stats()
        try:
            images = self._pipe_group(group, width, height, steps, guidance)
            success = _vram_stats()
            return images, {
                "batch_size_requested": requested,
                "batch_size_effective": requested,
                "batch_fallback_reason": None,
                **success,
                "vram_attempt_peak_mb": success.get("vram_peak_mb"),
                "vram_attempt_peak_reserved_mb": success.get("vram_peak_reserved_mb"),
                "vram_oom_peak_mb": None,
                "vram_oom_peak_reserved_mb": None,
            }
        except Exception as exc:
            failed = _vram_stats()
            if requested <= 1:
                raise
            if _is_cuda_oom(exc):
                _clear_cuda_cache()
                middle = max(1, requested // 2)
                chunks = (group[:middle], group[middle:])
                images: list[Any] = []
                metas: list[dict[str, Any]] = []
                for chunk in chunks:
                    if not chunk:
                        continue
                    chunk_images, meta = self._run_group(chunk, width, height, steps, guidance)
                    images.extend(chunk_images)
                    metas.append(meta)
                effective = max((int(meta.get("batch_size_effective") or 1) for meta in metas), default=1)
                success = _merge_vram_stats(metas)
                return images, {
                    "batch_size_requested": requested,
                    "batch_size_effective": effective,
                    "batch_fallback_reason": "cuda_oom",
                    **success,
                    "vram_attempt_peak_mb": _max_number(
                        failed.get("vram_peak_mb"),
                        *(meta.get("vram_attempt_peak_mb") for meta in metas),
                    ),
                    "vram_attempt_peak_reserved_mb": _max_number(
                        failed.get("vram_peak_reserved_mb"),
                        *(meta.get("vram_attempt_peak_reserved_mb") for meta in metas),
                    ),
                    "vram_oom_peak_mb": _max_number(
                        failed.get("vram_peak_mb"),
                        *(meta.get("vram_oom_peak_mb") for meta in metas),
                    ),
                    "vram_oom_peak_reserved_mb": _max_number(
                        failed.get("vram_peak_reserved_mb"),
                        *(meta.get("vram_oom_peak_reserved_mb") for meta in metas),
                    ),
                }

            # Keep the old compatibility fallback for pipelines that reject list
            # inputs/generators for reasons unrelated to memory. Measure every
            # successful scalar call separately and report the largest peak.
            images = []
            metas = []
            for item in group:
                _reset_vram_peak_stats()
                images.extend(self._pipe_single(item, width, height, steps, guidance))
                stats = _vram_stats()
                metas.append(
                    {
                        **stats,
                        "vram_attempt_peak_mb": stats.get("vram_peak_mb"),
                        "vram_attempt_peak_reserved_mb": stats.get("vram_peak_reserved_mb"),
                    }
                )
            success = _merge_vram_stats(metas)
            return images, {
                "batch_size_requested": requested,
                "batch_size_effective": 1,
                "batch_fallback_reason": type(exc).__name__,
                **success,
                "vram_attempt_peak_mb": _max_number(
                    failed.get("vram_peak_mb"),
                    *(meta.get("vram_attempt_peak_mb") for meta in metas),
                ),
                "vram_attempt_peak_reserved_mb": _max_number(
                    failed.get("vram_peak_reserved_mb"),
                    *(meta.get("vram_attempt_peak_reserved_mb") for meta in metas),
                ),
                "vram_oom_peak_mb": None,
                "vram_oom_peak_reserved_mb": None,
            }

    def _pipe_single(self, item: dict[str, Any], width: int, height: int, steps: int, guidance: float):
        import torch

        kwargs: dict[str, Any] = {
            "prompt": item["prompt"],
            "height": height,
            "width": width,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "generator": torch.Generator(device="cuda").manual_seed(int(item["seed"])),
        }
        if item.get("negative_prompt"):
            kwargs["negative_prompt"] = item["negative_prompt"]
        return self.pipe(**kwargs).images

    def _pipe_group(self, group: list[dict[str, Any]], width: int, height: int, steps: int, guidance: float):
        import torch

        prompts = [item["prompt"] for item in group]
        negatives = [item.get("negative_prompt") or "" for item in group]
        generators = [
            torch.Generator(device="cuda").manual_seed(int(item["seed"])) for item in group
        ]
        kwargs: dict[str, Any] = {
            "prompt": prompts,
            "height": height,
            "width": width,
            "num_inference_steps": steps,
            "guidance_scale": guidance,
            "generator": generators,
        }
        if any(negatives):
            kwargs["negative_prompt"] = negatives
        return self.pipe(**kwargs).images


def _stamp_applied(
    results: list[dict[str, Any]],
    items: list[dict[str, Any]],
    runtime: dict[str, Any],
    loaded_model: str,
) -> None:
    by_id = {item.get("generation_id"): item for item in items}
    for row in results:
        item = by_id.get(row.get("generation_id")) or {}
        requested_model = item.get("model") or loaded_model
        row["runtime"] = runtime
        row["applied"] = {
            "model": loaded_model,
            "requested_model": requested_model,
            "model_match": requested_model == loaded_model,
            "requested_gpu": runtime.get("requested_gpu") or item.get("requested_gpu"),
            "actual_gpu": runtime.get("actual_gpu"),
            "actual_device": runtime.get("actual_device"),
            "gpu_match": runtime.get("gpu_match"),
            "width": row.get("width") or item.get("width"),
            "height": row.get("height") or item.get("height"),
            "steps": item.get("steps"),
            "guidance": item.get("guidance"),
            "seed": item.get("seed"),
            "from_snapshot": runtime.get("from_snapshot"),
            "cpu_load_ms": runtime.get("cpu_load_ms"),
            "gpu_move_ms": runtime.get("gpu_move_ms"),
        }


def _publish_cost_events(
    results: list[dict[str, Any]],
    items: list[dict[str, Any]],
    runtime: dict[str, Any],
    *,
    cold: bool,
    load_ms: float,
    ids: dict[str, Any],
) -> None:
    if not results:
        return
    billed = runtime.get("actual_gpu") or runtime.get("requested_gpu") or "L40S"
    now = datetime.now(timezone.utc).isoformat()
    events: list[dict[str, Any]] = []
    first = items[0] if items else {}
    generation_ids = [str(item.get("generation_id")) for item in items if item.get("generation_id")]
    if cold and load_ms > 0:
        load_s = load_ms / 1000.0
        load_id = ids.get("modal_input_id") or ids.get("modal_function_call_id") or first.get("generation_id")
        events.append(
            {
                "id": f"evt_{load_id}_gpu_load",
                "ts": now,
                "kind": "gpu_load",
                "job_id": first.get("job_id"),
                "generation_id": first.get("generation_id"),
                "generation_ids": generation_ids,
                "model": runtime.get("loaded_model") or first.get("model"),
                "requested_gpu": runtime.get("requested_gpu"),
                "actual_gpu": runtime.get("actual_gpu"),
                "actual_device": runtime.get("actual_device"),
                **_billing_fields(billed, load_s),
                "load_ms": load_ms,
                "cold_start": True,
                "from_snapshot": runtime.get("from_snapshot"),
                "gpu_match": runtime.get("gpu_match"),
                "model_match": runtime.get("model_match"),
                "modal_function_call_id": ids.get("modal_function_call_id"),
                "modal_input_id": ids.get("modal_input_id"),
                "modal_task_id": runtime.get("modal_task_id"),
                "vram_allocated_mb": runtime.get("vram_allocated_mb"),
                "vram_reserved_mb": runtime.get("vram_reserved_mb"),
                "vram_peak_mb": runtime.get("vram_peak_mb"),
                "vram_peak_reserved_mb": runtime.get("vram_peak_reserved_mb"),
                "vram_free_mb": runtime.get("vram_free_mb"),
                "vram_total_mb": runtime.get("vram_total_mb"),
                "source": "modal-worker",
            }
        )
    by_id = {item.get("generation_id"): item for item in items}
    for row in results:
        item = by_id.get(row.get("generation_id")) or {}
        infer_ms = float(row.get("infer_ms") or 0.0)
        encode_ms = float(row.get("encode_ms") or 0.0)
        gen_s = max((infer_ms + encode_ms) / 1000.0, 0.0)
        events.append(
            {
                "id": f"evt_{row.get('generation_id')}_gpu_generate",
                "ts": now,
                "kind": "gpu_generate",
                "job_id": item.get("job_id"),
                "generation_id": row.get("generation_id"),
                "generation_ids": [str(row.get("generation_id"))] if row.get("generation_id") else [],
                "model": runtime.get("loaded_model") or item.get("model"),
                "requested_gpu": runtime.get("requested_gpu") or item.get("requested_gpu"),
                "actual_gpu": runtime.get("actual_gpu"),
                "actual_device": runtime.get("actual_device"),
                **_billing_fields(billed, gen_s),
                "load_ms": 0.0,
                "infer_ms": infer_ms,
                "encode_ms": encode_ms,
                "vram_allocated_mb": row.get("vram_allocated_mb"),
                "vram_reserved_mb": row.get("vram_reserved_mb"),
                "vram_peak_mb": row.get("vram_peak_mb"),
                "vram_peak_reserved_mb": row.get("vram_peak_reserved_mb"),
                "vram_attempt_peak_mb": row.get("vram_attempt_peak_mb"),
                "vram_attempt_peak_reserved_mb": row.get("vram_attempt_peak_reserved_mb"),
                "vram_oom_peak_mb": row.get("vram_oom_peak_mb"),
                "vram_oom_peak_reserved_mb": row.get("vram_oom_peak_reserved_mb"),
                "vram_free_mb": row.get("vram_free_mb"),
                "vram_total_mb": row.get("vram_total_mb"),
                "batch_size_requested": row.get("batch_size_requested"),
                "batch_size_effective": row.get("batch_size_effective"),
                "width": row.get("width") or item.get("width"),
                "height": row.get("height") or item.get("height"),
                "steps": item.get("steps"),
                "guidance": item.get("guidance"),
                "seed": item.get("seed"),
                "cold_start": False,
                "from_snapshot": runtime.get("from_snapshot"),
                "gpu_match": runtime.get("gpu_match"),
                "model_match": runtime.get("model_match"),
                "modal_function_call_id": ids.get("modal_function_call_id"),
                "modal_input_id": ids.get("modal_input_id"),
                "modal_task_id": runtime.get("modal_task_id"),
                "source": "modal-worker",
            }
        )
    try:
        record_cost_events(events)
    except Exception:
        pass
    try:
        archive_cost_events.spawn(events)
    except Exception:
        pass


def _billing_fields(gpu_id: str, seconds: float) -> dict[str, Any]:
    seconds = max(float(seconds), 0.0)
    try:
        spec = get_gpu(gpu_id)
        return {
            "gpu_seconds": seconds,
            "usd_per_second": spec.usd_per_second,
            "usd_per_hour": spec.usd_per_hour,
            "cost_usd": spec.usd_per_second * seconds,
        }
    except ValueError:
        return {
            "gpu_seconds": seconds,
            "usd_per_second": None,
            "usd_per_hour": None,
            "cost_usd": _safe_cost(gpu_id, seconds),
        }


def _safe_cost(gpu_id: str, seconds: float) -> float:
    try:
        return cost_for_seconds(gpu_id, seconds)
    except ValueError:
        return 0.0


def _call_ids() -> dict[str, Any]:
    function_call_id = None
    input_id = None
    try:
        function_call_id = modal.current_function_call_id()
    except Exception:
        function_call_id = None
    try:
        input_id = modal.current_input_id()
    except Exception:
        input_id = None
    container = {
        key: os.environ[key]
        for key in ("MODAL_TASK_ID", "MODAL_CLOUD_PROVIDER", "MODAL_REGION", "MODAL_IMAGE_ID")
        if os.environ.get(key)
    }
    return {
        "modal_function_call_id": function_call_id,
        "modal_input_id": input_id,
        "container": container,
    }


def _max_number(*values: Any) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return max(numbers) if numbers else None


def _merge_vram_stats(metas: list[dict[str, Any]]) -> dict[str, float | None]:
    """Aggregate successful sub-batches without mixing in failed OOM peaks."""
    if not metas:
        return _vram_stats()
    return {
        "vram_allocated_mb": _max_number(*(meta.get("vram_allocated_mb") for meta in metas)),
        "vram_reserved_mb": _max_number(*(meta.get("vram_reserved_mb") for meta in metas)),
        "vram_peak_mb": _max_number(*(meta.get("vram_peak_mb") for meta in metas)),
        "vram_peak_reserved_mb": _max_number(*(meta.get("vram_peak_reserved_mb") for meta in metas)),
        "vram_free_mb": min(
            (float(meta["vram_free_mb"]) for meta in metas if meta.get("vram_free_mb") is not None),
            default=None,
        ),
        "vram_total_mb": _max_number(*(meta.get("vram_total_mb") for meta in metas)),
    }


def _vram_stats() -> dict[str, float | None]:
    """CUDA allocator + device memory snapshot for one load/batch boundary."""
    empty = {
        "vram_allocated_mb": None,
        "vram_reserved_mb": None,
        "vram_peak_mb": None,
        "vram_peak_reserved_mb": None,
        "vram_free_mb": None,
        "vram_total_mb": None,
    }
    try:
        import torch

        if not torch.cuda.is_available():
            return empty
        scale = 1024.0 * 1024.0
        free, total = torch.cuda.mem_get_info()
        max_reserved = getattr(torch.cuda, "max_memory_reserved", None)
        return {
            "vram_allocated_mb": torch.cuda.memory_allocated() / scale,
            "vram_reserved_mb": torch.cuda.memory_reserved() / scale,
            "vram_peak_mb": torch.cuda.max_memory_allocated() / scale,
            "vram_peak_reserved_mb": (max_reserved() / scale) if max_reserved else None,
            "vram_free_mb": free / scale,
            "vram_total_mb": total / scale,
        }
    except Exception:
        return empty


def _reset_vram_peak_stats() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        return


def _clear_cuda_cache() -> None:
    try:
        import gc
        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return


def _is_cuda_oom(exc: BaseException) -> bool:
    try:
        import torch

        oom_type = getattr(torch.cuda, "OutOfMemoryError", None)
        if oom_type is not None and isinstance(exc, oom_type):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return "cuda" in text and "out of memory" in text


def _vram_mb() -> float | None:
    return _vram_stats()["vram_allocated_mb"]


# Register CPU prefetch + ledger on the same App so `modal deploy -m modal_sana.modal.worker` includes them.
_ = prefetch_model
_ = prefetch_progress
_ = list_volume_models
_ = registered_model_ids
_ = archive_cost_events
_ = list_cost_events
