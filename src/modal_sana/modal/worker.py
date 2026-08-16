from __future__ import annotations

import time
from typing import Any

import modal

from modal_sana.modal.app import app
from modal_sana.modal.image import image
from modal_sana.modal.volumes import CACHE_DIR, huggingface_cache_volume
from modal_sana.models.sana.registry import get_model

MINUTES = 60


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
    scaledown_window=5 * MINUTES,
    volumes={CACHE_DIR: huggingface_cache_volume()},
    retries=modal.Retries(max_retries=2, backoff_coefficient=2.0),
)
class SanaWorker:
    """One warm GPU container = one loaded SANA pipeline.

    GPU type and max_containers are applied at the call site with
    `SanaWorker.with_options(gpu=..., max_containers=...)`.
    """

    model_id: str = modal.parameter(default="sana-sprint-1.6b")

    @modal.enter()
    def load_pipeline(self) -> None:
        self.pipe = _load_pipeline(self.model_id)

    @modal.method()
    def generate_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        import torch

        items = payload.get("items") or []
        results: list[dict[str, Any]] = []
        if not items:
            return {"items": results}

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

        for (width, height, steps, guidance, image_format, quality), group in grouped.items():
            started = time.perf_counter()
            try:
                images = self._run_group(group, width, height, steps, guidance)
            except Exception as exc:  # noqa: BLE001 — surface to local job state
                elapsed = (time.perf_counter() - started) * 1000
                for item in group:
                    results.append(
                        {
                            "generation_id": item["generation_id"],
                            "image_bytes": None,
                            "width": width,
                            "height": height,
                            "latency_ms": elapsed / max(len(group), 1),
                            "error": str(exc),
                        }
                    )
                continue

            elapsed = (time.perf_counter() - started) * 1000
            per_image = elapsed / max(len(images), 1)
            for item, image in zip(group, images, strict=False):
                results.append(
                    {
                        "generation_id": item["generation_id"],
                        "image_bytes": _encode(image, image_format, quality),
                        "width": image.width,
                        "height": image.height,
                        "latency_ms": per_image,
                        "error": None,
                    }
                )
            torch.cuda.empty_cache()
        return {"items": results}

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
