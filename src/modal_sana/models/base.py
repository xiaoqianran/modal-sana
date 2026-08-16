from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    family: str
    hf_id: str
    pipeline: str
    default_steps: int
    default_guidance: float
    recommended_dtype: str
    description: str
    min_vram_gb: int = 16
    recommended_gpu: str = "L40S"
    native_width: int = 1024
    native_height: int = 1024
    recommended_batch: int = 4
    vae_tiling: bool = False
    prefetch_by_default: bool = True
