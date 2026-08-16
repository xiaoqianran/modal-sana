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
