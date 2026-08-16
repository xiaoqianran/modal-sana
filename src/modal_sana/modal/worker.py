import os
import time
from typing import Any

import modal

from modal_sana.core.cost import item_gpu_seconds
from modal_sana.modal.app import app
from modal_sana.modal.image import image
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
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
    pipe = pipeline_cls.from_pretrained(spec.hf_id, torch_dtype=dtype)
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

    GPU type and max_containers are applied at the call site with
    `SanaWorker.with_options(gpu=..., max_containers=...)`.
    After the last input, Modal may keep this container (GPU + its CPU)
    idle for ``SCALEDOWN_SECONDS`` then scale to zero.
    """

    model_id: str = modal.parameter(default="sana-sprint-1.6b")

    @modal.enter()
    def load_pipeline(self) -> None:
        started = time.perf_counter()
        self.pipe = _load_pipeline(self.model_id)
        self._load_ms = (time.perf_counter() - started) * 1000
        self._calls = 0

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
        return {"items": results, **ids}

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
