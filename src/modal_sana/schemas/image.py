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
