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

    def generate_batches(
        self,
        batches: list[list[GenerateRequest]],
        *,
        gpu: str,
        workers: int,
        model: str,
        retry: int = 2,
        deployed: bool = False,
    ) -> Iterator[GenerateResult]:
        del gpu, workers, model, retry, deployed
        for batch in batches:
            for request in batch:
                started = time.perf_counter()
                image = render_placeholder(request)
                payload = encode_image(image, request.image_format, request.quality)
                yield GenerateResult(
                    generation_id=request.generation_id,
                    image_bytes=payload,
                    width=request.width,
                    height=request.height,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
