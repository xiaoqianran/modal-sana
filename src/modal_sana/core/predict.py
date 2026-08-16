from __future__ import annotations

from math import ceil
from statistics import median
from typing import Any

from modal_sana.core.ledger import CostEvent
from modal_sana.modal.gpu import get_gpu
from modal_sana.models.sana.registry import get_model

# Cold ``from_pretrained`` + ``.to("cuda")`` after weights are already on the Volume.
DEFAULT_LOAD_MS: dict[str, float] = {
    "sana-sprint-0.6b": 12_000,
    "sana-sprint-1.6b": 18_000,
    "sana-1.6b": 18_000,
    "sana-1.5-1.6b": 20_000,
    "sana-1.5-4.8b": 45_000,
}

# One-image infer on L40S at the model's default step count, 1024².
DEFAULT_INFER_MS_L40S: dict[str, float] = {
    "sana-sprint-0.6b": 800,
    "sana-sprint-1.6b": 3_600,
    "sana-1.6b": 8_000,
    "sana-1.5-1.6b": 8_500,
    "sana-1.5-4.8b": 18_000,
}

# Relative throughput vs L40S. Used only when we have no ledger history.
GPU_SPEED: dict[str, float] = {
    "T4": 0.25,
    "L4": 0.40,
    "A10": 0.45,
    "L40S": 1.00,
    "A100": 1.15,
    "A100-80GB": 1.20,
    "RTX-PRO-6000": 1.50,
    "H100": 2.00,
    "H200": 2.20,
    "B200": 2.80,
    "B300": 3.20,
}


def predict_run(
    *,
    model: str,
    gpu: str,
    count: int = 1,
    width: int = 1024,
    height: int = 1024,
    steps: int | None = None,
    batch_size: int = 4,
    workers: int = 2,
    history: list[CostEvent] | None = None,
) -> dict[str, Any]:
    """Estimate Modal GPU $ for one generate click (cold load + N images)."""
    spec = get_gpu(gpu)
    model_spec = get_model(model)
    resolved_steps = int(steps if steps is not None else model_spec.default_steps)
    n = max(int(count), 1)
    batch = max(int(batch_size), 1)
    n_workers = max(int(workers), 1)
    containers = min(n_workers, max(1, ceil(n / batch)))
    pixels = max(int(width), 1) * max(int(height), 1)
    pixel_scale = pixels / (1024 * 1024)
    step_scale = resolved_steps / max(model_spec.default_steps, 1)
    speed = GPU_SPEED.get(spec.id, 1.0)

    load_ms, load_src = _typical_ms(
        history,
        kind="gpu_load",
        model=model,
        gpu=gpu,
        field="load_ms",
        fallback=DEFAULT_LOAD_MS.get(model, 20_000) / max(speed, 0.2),
    )
    infer_fallback = (
        DEFAULT_INFER_MS_L40S.get(model, 4_000) * step_scale * pixel_scale / max(speed, 0.2)
    )
    infer_ms, infer_src = _typical_ms(
        history,
        kind="gpu_generate",
        model=model,
        gpu=gpu,
        field="infer_ms",
        fallback=infer_fallback,
        width=width,
        height=height,
        steps=resolved_steps,
    )
    encode_ms = max(40.0, 80.0 * pixel_scale)

    load_seconds = (load_ms / 1000.0) * containers
    generate_seconds = ((infer_ms + encode_ms) / 1000.0) * n
    load_usd = spec.usd_per_second * load_seconds
    generate_usd = spec.usd_per_second * generate_seconds
    idle_seconds = 10.0 * containers
    idle_usd = spec.usd_per_second * idle_seconds

    return {
        "model": model_spec.id,
        "model_name": model_spec.name,
        "gpu": spec.id,
        "usd_per_second": spec.usd_per_second,
        "usd_per_hour": spec.usd_per_hour,
        "vram_gb": spec.vram_gb,
        "min_vram_gb": model_spec.min_vram_gb,
        "recommended_gpu": model_spec.recommended_gpu,
        "vram_ok": spec.vram_gb >= model_spec.min_vram_gb,
        "resolved": {
            "count": n,
            "width": width,
            "height": height,
            "steps": resolved_steps,
            "guidance": model_spec.default_guidance,
            "batch_size": batch,
            "workers": n_workers,
            "containers": containers,
        },
        "load": {
            "label": "纯 GPU 加载",
            "seconds": load_seconds,
            "usd": load_usd,
            "per_container_ms": load_ms,
            "containers": containers,
            "source": load_src,
            "note": "Cold from_pretrained + pipe.to(cuda). One charge per new GPU container.",
        },
        "generate": {
            "label": "GPU 实际生成",
            "seconds": generate_seconds,
            "usd": generate_usd,
            "per_image_ms": infer_ms + encode_ms,
            "infer_ms": infer_ms,
            "encode_ms": encode_ms,
            "count": n,
            "source": infer_src,
            "note": "Infer + encode for every image. No load.",
        },
        "total_usd": load_usd + generate_usd,
        "scaledown_idle": {
            "seconds": idle_seconds,
            "usd": idle_usd,
            "note": "Modal may keep this GPU idle up to 10s after the last input. Not in the generate total.",
        },
        "independent": (
            "Model and GPU are independent. Changing the model does not change the GPU. "
            f"This click will request {spec.id} and load {model_spec.id}."
        ),
    }


def _typical_ms(
    history: list[CostEvent] | None,
    *,
    kind: str,
    model: str,
    gpu: str,
    field: str,
    fallback: float,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
) -> tuple[float, str]:
    if not history:
        return float(fallback), "default"
    values: list[float] = []
    for event in history:
        if event.kind != kind or event.model != model:
            continue
        event_gpu = event.actual_gpu or event.requested_gpu
        if event_gpu != gpu:
            continue
        if width and event.width and event.width != width:
            continue
        if height and event.height and event.height != height:
            continue
        if steps and event.steps and event.steps != steps:
            continue
        raw = getattr(event, field, None)
        if raw is None:
            continue
        value = float(raw)
        if value > 0:
            values.append(value)
        if len(values) >= 40:
            break
    if len(values) < 2:
        return float(fallback), "default"
    return float(median(values)), f"ledger:{len(values)}"
