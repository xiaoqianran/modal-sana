import os
import time
from datetime import datetime, timezone
from typing import Any

import modal

from modal_sana.core.cost import cost_for_seconds, item_gpu_seconds
from modal_sana.modal.app import app
from modal_sana.modal.image import image
from modal_sana.modal.ledger import archive_cost_events, list_cost_events, record_cost_events
from modal_sana.modal.prefetch import list_volume_models, prefetch_model
from modal_sana.modal.runtime import probe_runtime
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
from modal_sana.modal.weights import assert_model_ready
from modal_sana.models.sana.registry import get_model

MINUTES = 60
# Modal allows 2s–20min. Idle GPU+CPU of this container are billed until then.
SCALEDOWN_SECONDS = 10


def _dtype(name: str):
    import torch

    return torch.bfloat16 if name == "bfloat16" else torch.float16


def _load_pipeline(model_id: str):
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
    pipe.to("cuda")
    if hasattr(pipe, "vae") and pipe.vae is not None:
        pipe.vae.to(dtype)
    if hasattr(pipe, "text_encoder") and pipe.text_encoder is not None:
        pipe.text_encoder.to(torch.bfloat16)
    return pipe


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
)
class SanaWorker:
    """One warm GPU container = one loaded SANA pipeline.

    Weights must already be on the Volume (CPU ``prefetch_model``).
    This class only loads from ``/cache/models/{id}`` with
    ``local_files_only=True`` — it never downloads from Hugging Face.

    GPU type and max_containers MUST be applied at the call site with
    `SanaWorker.with_options(gpu=..., max_containers=...)`. The ``gpu="L40S"``
    on this decorator is only the fallback if a caller forgets with_options.
    After the last input, Modal may keep this container (GPU + its CPU)
    idle for ``SCALEDOWN_SECONDS`` then scale to zero.
    """

    model_id: str = modal.parameter(default="sana-sprint-1.6b")

    @modal.enter()
    def load_pipeline(self) -> None:
        huggingface_cache_volume().reload()
        started = time.perf_counter()
        self.pipe = _load_pipeline(self.model_id)
        self._load_ms = (time.perf_counter() - started) * 1000
        self._calls = 0
        self._runtime = probe_runtime(
            requested_model=self.model_id,
            loaded_model=self.model_id,
        )

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
                item.get("image_format", "webp"),
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
            try:
                infer_ms, images = self._infer_group(group, width, height, steps, guidance)
                error = None
            except Exception as exc:  # noqa: BLE001 — surface to local job state
                infer_ms = 0.0
                images = []
                error = str(exc)

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
                            "vram_allocated_mb": _vram_mb(),
                        }
                    )
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
                        "vram_allocated_mb": _vram_mb(),
                    }
                )
            torch.cuda.empty_cache()
        _stamp_applied(results, items, runtime, self.model_id)
        _publish_cost_events(results, items, runtime, cold=cold, load_ms=load_ms, ids=ids)
        return {"items": results, "runtime": runtime, **ids}

    def _infer_group(self, group: list[dict[str, Any]], width: int, height: int, steps: int, guidance: float):
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        try:
            images = self._run_group(group, width, height, steps, guidance)
        finally:
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        return (time.perf_counter() - started) * 1000, images

    def _run_group(self, group: list[dict[str, Any]], width: int, height: int, steps: int, guidance: float):
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
        try:
            return self.pipe(**kwargs).images
        except Exception:
            images = []
            for item, generator in zip(group, generators, strict=True):
                single = {
                    "prompt": item["prompt"],
                    "height": height,
                    "width": width,
                    "num_inference_steps": steps,
                    "guidance_scale": guidance,
                    "generator": generator,
                }
                if item.get("negative_prompt"):
                    single["negative_prompt"] = item["negative_prompt"]
                images.extend(self.pipe(**single).images)
            return images


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
                "model": runtime.get("loaded_model") or first.get("model"),
                "requested_gpu": runtime.get("requested_gpu"),
                "actual_gpu": runtime.get("actual_gpu"),
                "actual_device": runtime.get("actual_device"),
                "gpu_seconds": load_s,
                "cost_usd": _safe_cost(billed, load_s),
                "load_ms": load_ms,
                "cold_start": True,
                "gpu_match": runtime.get("gpu_match"),
                "model_match": runtime.get("model_match"),
                "modal_function_call_id": ids.get("modal_function_call_id"),
                "modal_input_id": ids.get("modal_input_id"),
                "modal_task_id": runtime.get("modal_task_id"),
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
                "model": runtime.get("loaded_model") or item.get("model"),
                "requested_gpu": runtime.get("requested_gpu") or item.get("requested_gpu"),
                "actual_gpu": runtime.get("actual_gpu"),
                "actual_device": runtime.get("actual_device"),
                "gpu_seconds": gen_s,
                "cost_usd": _safe_cost(billed, gen_s),
                "load_ms": 0.0,
                "infer_ms": infer_ms,
                "encode_ms": encode_ms,
                "width": row.get("width") or item.get("width"),
                "height": row.get("height") or item.get("height"),
                "steps": item.get("steps"),
                "guidance": item.get("guidance"),
                "seed": item.get("seed"),
                "cold_start": False,
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


def _vram_mb() -> float | None:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / (1024 * 1024)
    except Exception:
        return None
    return None


# Register CPU prefetch + ledger on the same App so `modal deploy -m modal_sana.modal.worker` includes them.
_ = prefetch_model
_ = list_volume_models
_ = archive_cost_events
_ = list_cost_events
