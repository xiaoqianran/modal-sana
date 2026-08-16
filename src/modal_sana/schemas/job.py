from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ImageFormat = Literal["webp", "png", "jpg"]
DedupMode = Literal["skip", "regenerate", "reuse"]
JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
TaskStatus = Literal["pending", "running", "completed", "failed", "retrying", "cancelled"]


class JobConfig(BaseModel):
    model: str = "sana-sprint-1.6b"
    gpu: str = "L40S"
    width: int = 1024
    height: int = 1024
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    count: int = 1
    batch_size: int = 4
    workers: int = 2
    retry: int = 3
    image_format: ImageFormat = "webp"
    quality: int = 90
    negative_prompt: str = ""
    dry_run: bool = False
    deployed: bool = False
    deduplicate: bool = False
    dedup_mode: DedupMode = "skip"


class PromptSpec(BaseModel):
    prompt: str
    negative_prompt: str = ""
    count: int = 1
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    model: str | None = None
    source_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class GenerationSpec(BaseModel):
    id: str
    job_id: str
    prompt_task_id: str
    prompt: str
    negative_prompt: str = ""
    seed: int
    model: str
    gpu: str
    steps: int
    guidance: float
    width: int
    height: int
    image_format: ImageFormat = "webp"
    quality: int = 90
    task_hash: str
    status: TaskStatus = "pending"


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    model: str
    gpu: str
    total_images: int
    completed_images: int
    failed_images: int
    dry_run: bool = False
    error: str | None = None
    config: JobConfig
