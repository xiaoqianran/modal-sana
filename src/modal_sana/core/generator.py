from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    generation_id: str
    prompt: str
    negative_prompt: str = ""
    seed: int
    width: int = 1024
    height: int = 1024
    steps: int = 2
    guidance: float = 4.5
    model: str = "sana-sprint-1.6b"
    image_format: str = "png"
    quality: int = 90
    job_id: str = ""
    requested_gpu: str = ""


class GenerateResult(BaseModel):
    generation_id: str
    image_bytes: bytes | None = None
    width: int = 0
    height: int = 0
    latency_ms: float = 0.0
    error: str | None = None
    telemetry: dict[str, Any] = Field(default_factory=dict)


class ImageGenerator(Protocol):
    def generate_batches(
        self,
        batches: list[list[GenerateRequest]],
        *,
        gpu: str,
        workers: int,
        model: str,
        retry: int = 2,
        deployed: bool | None = None,
    ) -> Iterator[GenerateResult]:
        """Yield one result per generation, as soon as a GPU batch finishes."""


class GeneratorOptions(BaseModel):
    gpu: str = "L40S"
    workers: int = 2
    model: str = "sana-sprint-1.6b"
    retry: int = 2
    deployed: bool | None = None
    extra: dict = Field(default_factory=dict)
