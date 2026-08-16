from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator

from PIL import Image, ImageDraw, ImageFont

from modal_sana.core.generator import GenerateRequest, GenerateResult
from modal_sana.storage.encode import encode_image


def _color_for(prompt: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    return (40 + digest[0] % 80, 32 + digest[1] % 70, 28 + digest[2] % 60)


def render_placeholder(request: GenerateRequest) -> Image.Image:
    image = Image.new("RGB", (request.width, request.height), _color_for(request.prompt))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    text = (
        f"DRY RUN\n{request.model}\nseed {request.seed}\n\n{request.prompt}"
    )
    margin = max(24, request.width // 32)
    draw.multiline_text((margin, margin), text, fill=(244, 235, 225), font=font, spacing=8)
    return image


class MockGenerator:
    """Local stand-in so CLI/Web/tests work without Modal credentials."""

    def __init__(self) -> None:
        self.last_meta: dict = {}

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
        map_started = time.perf_counter()
        del workers, retry, deployed
        for batch in batches:
            for request in batch:
                started = time.perf_counter()
                image = render_placeholder(request)
                payload = encode_image(image, request.image_format, request.quality)
                latency_ms = (time.perf_counter() - started) * 1000
                yield GenerateResult(
                    generation_id=request.generation_id,
                    image_bytes=payload,
                    width=request.width,
                    height=request.height,
                    latency_ms=latency_ms,
                    telemetry={
                        "load_ms": 0.0,
                        "infer_ms": latency_ms * 0.85,
                        "encode_ms": latency_ms * 0.15,
                        "gpu_seconds": 0.0,
                        "cost_usd": 0.0,
                        "cold_start": False,
                        "dry_run": True,
                        "requested_gpu": gpu,
                        "actual_gpu": gpu,
                        "actual_device": "dry-run",
                        "gpu_match": True,
                        "loaded_model": request.model or model,
                        "requested_model": request.model or model,
                        "model_match": True,
                        "applied": {
                            "model": request.model or model,
                            "requested_gpu": gpu,
                            "actual_gpu": gpu,
                            "width": request.width,
                            "height": request.height,
                            "steps": request.steps,
                            "guidance": request.guidance,
                            "seed": request.seed,
                        },
                    },
                )
        self.last_meta = {
            "deployed": False,
            "dry_run": True,
            "map_wall_ms": (time.perf_counter() - map_started) * 1000,
            "gpu": gpu,
            "model": model,
        }
