from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ImageRecord(BaseModel):
    id: str
    generation_id: str
    job_id: str
    prompt_task_id: str
    path: str
    prompt: str
    negative_prompt: str
    seed: int
    model: str
    gpu: str
    steps: int
    guidance: float
    width: int
    height: int
    format: str
    byte_size: int
    latency_ms: float | None = None
    cost_usd: float | None = None
    infer_ms: float | None = None
    load_ms: float | None = None
    encode_ms: float | None = None
    gpu_seconds: float | None = None
    vram_allocated_mb: float | None = None
    vram_reserved_mb: float | None = None
    vram_peak_mb: float | None = None
    vram_peak_reserved_mb: float | None = None
    vram_attempt_peak_mb: float | None = None
    vram_attempt_peak_reserved_mb: float | None = None
    vram_oom_peak_mb: float | None = None
    vram_oom_peak_reserved_mb: float | None = None
    vram_free_mb: float | None = None
    vram_total_mb: float | None = None
    batch_size_requested: int | None = None
    batch_size_effective: int | None = None
    batch_fallback_reason: str | None = None
    modal_function_call_id: str | None = None
    modal_input_id: str | None = None
    created_at: datetime
    actual_gpu: str | None = None
    actual_device: str | None = None
    gpu_match: bool | None = None


class GalleryPage(BaseModel):
    items: list[ImageRecord]
    total: int
    page: int
    per_page: int
