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
    created_at: datetime


class GalleryPage(BaseModel):
    items: list[ImageRecord]
    total: int
    page: int
    per_page: int
