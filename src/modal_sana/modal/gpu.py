from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GPUSpec:
    id: str
    modal_name: str
    recommended_batch: int
    dtype: str
    usd_per_second: float
    vram_gb: int
    notes: str = ""

    @property
    def usd_per_hour(self) -> float:
        return self.usd_per_second * 3600


# Prices from https://modal.com/pricing (August 2026).
GPUS: dict[str, GPUSpec] = {
    spec.id: spec
    for spec in (
        GPUSpec("T4", "T4", 2, "float16", 0.000164, 16, "Budget / debug"),
        GPUSpec("L4", "L4", 4, "bfloat16", 0.000222, 24, "Cheap small batches"),
        GPUSpec("A10", "A10", 4, "bfloat16", 0.000306, 24, "Older Ampere"),
        GPUSpec("L40S", "L40S", 8, "bfloat16", 0.000542, 48, "Default. Strong price/perf"),
        GPUSpec("A100", "A100", 8, "bfloat16", 0.000583, 40, "A100 40GB"),
        GPUSpec("A100-80GB", "A100-80GB", 12, "bfloat16", 0.000694, 80, "A100 80GB"),
        GPUSpec("RTX-PRO-6000", "RTX-PRO-6000", 16, "bfloat16", 0.000842, 96, "4K default. 96GB Blackwell"),
        GPUSpec("H100", "H100", 16, "bfloat16", 0.001097, 80, "Highest Sprint throughput"),
        GPUSpec("H200", "H200", 16, "bfloat16", 0.001261, 141, "Large VRAM"),
        GPUSpec("B200", "B200", 16, "bfloat16", 0.001736, 180, "Blackwell datacenter"),
        GPUSpec("B300", "B300", 16, "bfloat16", 0.001972, 288, "Newest / most expensive"),
    )
}


def get_gpu(gpu_id: str) -> GPUSpec:
    key = gpu_id.strip()
    if key not in GPUS:
        known = ", ".join(GPUS)
        raise ValueError(f"Unknown GPU {gpu_id!r}. Known: {known}")
    return GPUS[key]


def recommended_batch_size(model_id: str, gpu_id: str) -> int:
    """Conservative auto batch = model-side cap clipped by GPU capacity hint.

    Model caps protect high-resolution / large checkpoints; GPU hints keep small
    cards from inheriting a batch chosen for L40S/H100-class devices.  This is
    intentionally a starting point, not a claim that every prompt uses the same
    amount of activation memory.  Worker telemetry records the real peak.
    """
    from modal_sana.models.sana.registry import get_model

    model = get_model(model_id)
    gpu = get_gpu(gpu_id)
    return max(1, min(int(model.recommended_batch), int(gpu.recommended_batch)))


def resolve_batch_size(model_id: str, gpu_id: str, requested: int | None) -> int:
    if requested is None or int(requested) <= 0:
        return recommended_batch_size(model_id, gpu_id)
    return max(1, int(requested))


def list_gpus() -> list[GPUSpec]:
    return list(GPUS.values())


def estimate_cost_usd(gpu_id: str, seconds: float) -> float:
    return get_gpu(gpu_id).usd_per_second * max(seconds, 0.0)
